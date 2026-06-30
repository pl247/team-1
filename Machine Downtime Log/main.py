#!/usr/bin/env python3
"""
Machine Downtime Log - A tracker for manufacturing-floor machine stoppages
Designed to run on Cisco Secure AI Factory: fully on-prem, secure, with Splunk visibility
"""

import os
import sys
import json
import time
import asyncio
import sqlite3
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import httpx
from pydantic import BaseModel

# Configuration from environment variables with defaults
APP_PORT = int(os.getenv("APP_PORT", 8742))
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://ray-serve-llama.apps.rtp-ai1-ucs.svpod.dc-01.com/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "/nvidia/nemotron-3-super")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_TIMEOUT_SECONDS = int(os.getenv("LLM_TIMEOUT_SECONDS", 15))
DB_PATH = os.getenv("DB_PATH", "/data/downtime.db")
SIMULATOR_ENABLED = os.getenv("SIMULATOR_ENABLED", "true").lower() == "true"
SIMULATOR_INTERVAL_SECONDS = int(os.getenv("SIMULATOR_INTERVAL_SECONDS", 8))
GITHUB_REPO = os.getenv("GITHUB_REPO", "https://github.com/pl247/team-1")
GHCR_IMAGE = os.getenv("GHCR_IMAGE", "ghcr.io/pl247/team-1")

# Valid categories and severities for LLM validation
VALID_CATEGORIES = {
    "Mechanical Failure", "Operator Error", "Material Shortage", 
    "Maintenance", "Power Loss", "Unknown"
}
VALID_SEVERITIES = {"Low", "Medium", "High", "Critical"}

# Global state
app_state = {
    "llm_reachable": False,
    "start_time": time.time(),
    "events_processed": 0
}

# Pydantic models
class DowntimeEvent(BaseModel):
    machine_id: str
    machine_type: str
    start_time: str  # ISO format
    end_time: Optional[str] = None
    downtime_minutes: Optional[float] = None
    description: str = ""
    reason_category: str = "Unclassified"
    severity: str = "Medium"
    manual_notes: str = ""

class EventInput(BaseModel):
    machine_id: str
    machine_type: str
    description: str = ""

class LLMResponse(BaseModel):
    reason_category: str
    severity: str

# Database initialization
def init_db():
    """Initialize SQLite database with required tables"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Create downtime_events table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS downtime_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            machine_id TEXT NOT NULL,
            machine_type TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT,
            downtime_minutes REAL,
            description TEXT,
            reason_category TEXT DEFAULT 'Unclassified',
            severity TEXT DEFAULT 'Medium',
            manual_notes TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Create indexes for better query performance
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_machine_id ON downtime_events(machine_id)
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_start_time ON downtime_events(start_time)
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_end_time ON downtime_events(end_time)
    ''')
    
    conn.commit()
    conn.close()

def log_structured(message: str, level: str = "INFO", **kwargs):
    """Log structured messages for Splunk ingestion"""
    log_entry = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "level": level,
        "message": message,
        "app": "machine-downtime-log",
        **kwargs
    }
    print(json.dumps(log_entry))

async def check_port_available(port: int) -> bool:
    """Check if a port is available for binding"""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(('0.0.0.0', port))
            return True
        except OSError:
            return False

async def call_llm(description: str) -> LLMResponse:
    """Call the LLM service for event classification"""
    if not LLM_BASE_URL or not LLM_MODEL:
        return LLMResponse(reason_category="Unclassified", severity="Medium")
    
    headers = {
        "Content-Type": "application/json"
    }
    if LLM_API_KEY:
        headers["Authorization"] = f"Bearer {LLM_API_KEY}"
    
    payload = {
        "model": LLM_MODEL,
        "messages": [
            {
                "role": "system",
                "content": "You are an industrial equipment expert. Analyze the machine downtime description and classify it into exactly one reason_category and one severity. Return ONLY valid JSON with the exact fields specified."
            },
            {
                "role": "user",
                "content": f"""Classify this machine downtime event:
                
                Description: {description}
                
                Return JSON with exactly these fields:
                - reason_category: one of ["Mechanical Failure", "Operator Error", "Material Shortage", "Maintenance", "Power Loss", "Unknown"]
                - severity: one of ["Low", "Medium", "High", "Critical"]
                
                Do not include any other text or formatting."""
            }
        ],
        "temperature": 0.1,
        "max_tokens": 100
    }
    
    try:
        async with httpx.AsyncClient(timeout=LLM_TIMEOUT_SECONDS) as client:
            response = await client.post(
                f"{LLM_BASE_URL}/chat/completions",
                headers=headers,
                json=payload
            )
            
            if response.status_code == 200:
                result = response.json()
                content = result.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                
                # Try to parse JSON from the response
                try:
                    # Find JSON object in the response
                    start_idx = content.find('{')
                    end_idx = content.rfind('}') + 1
                    if start_idx != -1 and end_idx != 0:
                        json_str = content[start_idx:end_idx]
                        parsed = json.loads(json_str)
                        
                        # Validate the response
                        category = parsed.get("reason_category", "Unclassified")
                        severity = parsed.get("severity", "Medium")
                        
                        if category in VALID_CATEGORIES and severity in VALID_SEVERITIES:
                            return LLMResponse(reason_category=category, severity=severity)
                except (json.JSONDecodeError, KeyError, ValueError):
                    pass
            
            # If we get here, something went wrong
            log_structured(
                "LLM call failed or returned invalid response",
                level="WARNING",
                status_code=response.status_code if 'response' in locals() else None,
                response_text=response.text if 'response' in locals() else "No response"
            )
            
    except Exception as e:
        log_structured(
            "LLM service unreachable",
            level="WARNING",
            error=str(e)
        )
    
    # Fallback response
    return LLMResponse(reason_category="Unclassified", severity="Medium")

# Event simulator
async def event_simulator():
    """Generate realistic machine downtime events for testing"""
    import random
    
    machine_types = ["CNC Mill", "Lathe", "Press", "Conveyor", "Robot Arm", "Welder", "Cutter"]
    machine_prefixes = ["MC", "LT", "PR", "CV", "RB", "WL", "CT"]
    descriptions = [
        "Tool jammed during operation",
        "Operator error - incorrect setup",
        "Material feeder blocked",
        "Scheduled maintenance required",
        "Power fluctuation detected",
        "Hydraulic pressure low",
        "Sensor malfunction",
        "Software timeout",
        "Material shortage",
        "Emergency stop activated"
    ]
    
    while SIMULATOR_ENABLED:
        try:
            # Wait for the specified interval
            await asyncio.sleep(SIMULATOR_INTERVAL_SECONDS)
            
            # Generate random event
            machine_type_idx = random.randint(0, len(machine_types) - 1)
            machine_type = machine_types[machine_type_idx]
            machine_prefix = machine_prefixes[machine_type_idx]
            machine_id = f"{machine_prefix}-{random.randint(100, 999)}"
            
            description = random.choice(descriptions)
            
            # Create event
            event_input = EventInput(
                machine_id=machine_id,
                machine_type=machine_type,
                description=description
            )
            
            # Process the event
            await process_downtime_event(event_input)
            
        except asyncio.CancelledError:
            break
        except Exception as e:
            log_structured(
                "Error in event simulator",
                level="ERROR",
                error=str(e)
            )
            await asyncio.sleep(5)  # Brief pause before retrying

async def process_downtime_event(event_input: EventInput) -> DowntimeEvent:
    """Process a downtime event and store it in the database"""
    start_time = datetime.utcnow()
    
    # Call LLM for classification
    llm_response = await call_llm(event_input.description)
    
    # Create the downtime event
    event = DowntimeEvent(
        machine_id=event_input.machine_id,
        machine_type=event_input.machine_type,
        start_time=start_time.isoformat(),
        description=event_input.description,
        reason_category=llm_response.reason_category,
        severity=llm_response.severity
    )
    
    # Store in database
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT INTO downtime_events 
        (machine_id, machine_type, start_time, description, reason_category, severity)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (
        event.machine_id,
        event.machine_type,
        event.start_time,
        event.description,
        event.reason_category,
        event.severity
    ))
    
    event_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    # Update stats
    app_state["events_processed"] += 1
    
    # Log for Splunk
    log_structured(
        "Downtime event logged",
        event_id=event_id,
        machine_id=event.machine_id,
        machine_type=event.machine_type,
        reason_category=event.reason_category,
        severity=event.severity,
        description=event.description[:100]  # Truncate for log size
    )
    
    return event

# FastAPI lifespan manager
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    log_structured("Application starting up")
    
    # Check if port is available
    if not await check_port_available(APP_PORT):
        print(f"Error: Port {APP_PORT} is already in use", file=sys.stderr)
        print(f"Please set APP_PORT to a free port or stop the process using port {APP_PORT}", file=sys.stderr)
        sys.exit(1)
    
    # Initialize database
    init_db()
    log_structured("Database initialized", db_path=DB_PATH)
    
    # Start event simulator if enabled
    simulator_task = None
    if SIMULATOR_ENABLED:
        simulator_task = asyncio.create_task(event_simulator())
        log_structured("Event simulator started", interval=SIMULATOR_INTERVAL_SECONDS)
    
    # Test LLM connectivity on startup
    try:
        test_response = await call_llm("test")
        app_state["llm_reachable"] = True
        log_structured("LLM service connectivity confirmed")
    except Exception as e:
        app_state["llm_reachable"] = False
        log_structured("LLM service unreachable on startup", level="WARNING", error=str(e))
    
    yield
    
    # Shutdown
    log_structured("Application shutting down")
    if simulator_task:
        simulator_task.cancel()
        try:
            await simulator_task
        except asyncio.CancelledError:
            pass

# Create FastAPI app
app = FastAPI(
    title="Machine Downtime Log",
    description="Tracker for manufacturing-floor machine stoppages - Cisco Secure AI Factory",
    version="1.0.0",
    lifespan=lifespan
)

# Serve static files (frontend)
app.mount("/static", StaticFiles(directory="static"), name="static")

# Routes
@app.get("/", response_class=HTMLResponse)
async def get_dashboard():
    """Serve the main dashboard HTML"""
    try:
        with open("static/index.html", "r") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Dashboard not found")

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "uptime_seconds": time.time() - app_state["start_time"],
        "events_processed": app_state["events_processed"],
        "llm_reachable": app_state["llm_reachable"],
        "port": APP_PORT
    }

@app.post("/api/events", response_model=DowntimeEvent)
async def create_downtime_event(event_input: EventInput):
    """Create a new downtime event"""
    return await process_downtime_event(event_input)

@app.get("/api/events/today")
async def get_today_events():
    """Get downtime events for today"""
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT id, machine_id, machine_type, start_time, end_time, downtime_minutes,
               description, reason_category, severity, manual_notes
        FROM downtime_events 
        WHERE start_time >= ?
        ORDER BY start_time DESC
    ''', (today_start.isoformat(),))
    
    rows = cursor.fetchall()
    conn.close()
    
    events = []
    for row in rows:
        events.append({
            "id": row[0],
            "machine_id": row[1],
            "machine_type": row[2],
            "start_time": row[3],
            "end_time": row[4],
            "downtime_minutes": row[5],
            "description": row[6],
            "reason_category": row[7],
            "severity": row[8],
            "manual_notes": row[9]
        })
    
    return {"events": events}

@app.get("/api/stats/today")
async def get_today_stats():
    """Get today's downtime statistics"""
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Total downtime minutes today
    cursor.execute('''
        SELECT COALESCE(SUM(downtime_minutes), 0)
        FROM downtime_events 
        WHERE start_time >= ? AND downtime_minutes IS NOT NULL
    ''', (today_start.isoformat(),))
    total_downtime = cursor.fetchone()[0] or 0
    
    # Events by machine
    cursor.execute('''
        SELECT machine_id, machine_type, 
               COALESCE(SUM(downtime_minutes), 0) as total_downtime,
               COUNT(*) as event_count
        FROM downtime_events 
        WHERE start_time >= ?
        GROUP BY machine_id, machine_type
        ORDER BY total_downtime DESC
    ''', (today_start.isoformat(),))
    
    machine_stats = []
    for row in cursor.fetchall():
        machine_stats.append({
            "machine_id": row[0],
            "machine_type": row[1],
            "total_downtime": row[2],
            "event_count": row[3]
        })
    
    # Worst machine (highest downtime)
    worst_machine = machine_stats[0] if machine_stats else None
    
    conn.close()
    
    return {
        "total_downtime_minutes": round(total_downtime, 2),
        "worst_machine": worst_machine,
        "machine_stats": machine_stats,
        "events_processed_today": len([e for e in machine_stats if e["event_count"] > 0])
    }

@app.post("/api/events/{event_id}/notes")
async def add_manual_notes(event_id: int, notes: Dict[str, str]):
    """Add manual notes to a downtime event"""
    note_text = notes.get("notes", "")
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        UPDATE downtime_events 
        SET manual_notes = ?
        WHERE id = ?
    ''', (note_text, event_id))
    
    if cursor.rowcount == 0:
        conn.close()
        raise HTTPException(status_code=404, detail="Event not found")
    
    conn.commit()
    conn.close()
    
    log_structured(
        "Manual notes added",
        event_id=event_id,
        notes_length=len(note_text)
    )
    
    return {"status": "success", "message": "Notes added"}

@app.post("/api/events/{event_id}/end")
async def end_downtime_event(event_id: int):
    """Mark a downtime event as ended"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Get the event
    cursor.execute('''
        SELECT start_time FROM downtime_events WHERE id = ?
    ''', (event_id,))
    
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Event not found")
    
    start_time = datetime.fromisoformat(row[0])
    end_time = datetime.utcnow()
    downtime_minutes = (end_time - start_time).total_seconds() / 60
    
    # Update the event
    cursor.execute('''
        UPDATE downtime_events 
        SET end_time = ?, downtime_minutes = ?
        WHERE id = ?
    ''', (end_time.isoformat(), downtime_minutes, event_id))
    
    conn.commit()
    conn.close()
    
    log_structured(
        "Downtime event ended",
        event_id=event_id,
        downtime_minutes=round(downtime_minutes, 2)
    )
    
    return {
        "status": "success",
        "end_time": end_time.isoformat(),
        "downtime_minutes": round(downtime_minutes, 2)
    }

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=APP_PORT,
        log_level="info",
        access_log=True
    )