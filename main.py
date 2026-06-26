import os
import socket
import sys
import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import List, Optional, AsyncGenerator
import random

from fastapi import FastAPI, HTTPException, Depends
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
import uvicorn

from .storage import SessionLocal, DowntimeEvent, create_db_and_tables
from .llm_client import LLMClient

# Configure logging for Splunk-friendly structured output
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("machine-downtime-log")

# Configuration from environment variables
APP_PORT = int(os.getenv("APP_PORT", "8742"))
SIMULATOR_ENABLED = os.getenv("SIMULATOR_ENABLED", "true").lower() == "true"
SIMULATOR_INTERVAL_SECONDS = int(os.getenv("SIMULATOR_INTERVAL_SECONDS", "8"))

# Initialize FastAPI app
app = FastAPI(title="Machine Downtime Log", version="1.0.0")

# Global LLM client instance
llm_client: Optional[LLMClient] = None

# Event simulator states
simulator_task: Optional[asyncio.Task] = None
simulator_running = False

# Sample machine data for simulation
MACHINES = [
    {"id": "CNC-001", "type": "CNC Mill"},
    {"id": "CNC-002", "type": "CNC Lathe"}, 
    {"id": "ROBOT-001", "type": "Assembly Robot"},
    {"id": "PRESS-001", "type": "Hydraulic Press"},
    {"id": "WELD-001", "type": "Welding Station"},
    {"id": "CONV-001", "type": "Conveyor System"}
]

EVENT_DESCRIPTIONS = [
    "Machine jammed due to material misfeed",
    "Tool wear requiring replacement",
    "Operator stopped machine for safety check",
    "Material container empty, awaiting refill",
    "Preventive maintenance scheduled",
    "Power fluctuation caused temporary shutdown",
    "Robot arm calibration needed",
    "Conveyor belt tracking issue",
    "Hydraulic pressure drop detected",
    "Spindle overheating, cooling required"
]

def check_port_available(port: int) -> bool:
    """Check if a port is available for binding."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("0.0.0.0", port))
            s.close()
            return True
        except OSError:
            return False

def get_db() -> Session:
    """Dependency to get database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.on_event("startup")
async def startup_event():
    """Initialize application on startup."""
    global llm_client
    
    # Check if port is available
    if not check_port_available(APP_PORT):
        logger.error(f"Port {APP_PORT} is already in use. Please set a free APP_PORT or stop the conflicting service.")
        sys.exit(1)
    
    logger.info(f"Starting Machine Downtime Log on port {APP_PORT}")
    
    # Initialize database
    create_db_and_tables()
    logger.info("Database initialized")
    
    # Initialize LLM client
    llm_client = LLMClient()
    logger.info("LLM client initialized")
    
    # Start event simulator if enabled
    if SIMULATOR_ENABLED:
        await start_simulator()
        logger.info(f"Event simulator started with {SIMULATOR_INTERVAL_SECONDS}s interval")

@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on application shutdown."""
    global llm_client, simulator_task, simulator_running
    
    logger.info("Shutting down Machine Downtime Log")
    
    # Stop simulator
    if simulator_running and simulator_task:
        simulator_running = False
        simulator_task.cancel()
        try:
            await simulator_task
        except asyncio.CancelledError:
            pass
    
    # Close LLM client
    if llm_client:
        await llm_client.close()
    
    logger.info("Shutdown complete")

async def start_simulator():
    """Start the background event simulator."""
    global simulator_task, simulator_running
    
    if simulator_running:
        return
        
    simulator_running = True
    simulator_task = asyncio.create_task(simulator_loop())

async def simulator_loop():
    """Main loop for the event simulator."""
    global simulator_running
    
    while simulator_running:
        try:
            await generate_simulated_event()
            await asyncio.sleep(SIMULATOR_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in simulator loop: {e}")
            await asyncio.sleep(SIMULATOR_INTERVAL_SECONDS)

async def generate_simulated_event():
    """Generate a single simulated machine event."""
    machine = random.choice(MACHINES)
    description = random.choice(EVENT_DESCRIPTIONS)
    
    # Generate realistic timestamps
    start_time = datetime.utcnow() - timedelta(minutes=random.randint(0, 60))
    end_time = start_time + timedelta(minutes=random.randint(5, 120))
    
    # Classify event using LLM
    category = "Unclassified"
    severity = "Medium"
    
    if llm_client:
        try:
            result = await llm_client.classify_event(description)
            category = result["reason_category"]
            severity = result["severity"]
        except Exception as e:
            logger.error(f"LLM classification failed in simulator: {e}")
    
    # Save to database
    db = SessionLocal()
    try:
        event = DowntimeEvent(
            machine_id=machine["id"],
            machine_type=machine["type"],
            start_time=start_time,
            end_time=end_time,
            downtime_minutes=(end_time - start_time).total_seconds() / 60,
            description=description,
            reason_category=category,
            severity=severity
        )
        db.add(event)
        db.commit()
        
        logger.info(
            f"event_type=downtime_detected "
            f"machine_id={machine['id']} "
            f"machine_type={machine['type']} "
            f"downtime_minutes={event.downtime_minutes:.1f} "
            f"reason_category={category} "
            f"severity={severity} "
            f"source=simulator"
        )
        
    except Exception as e:
        logger.error(f"Failed to save simulated event: {e}")
        db.rollback()
    finally:
        db.close()

# API Endpoints
@app.get("/")
async def root():
    """Root endpoint - serve the dashboard."""
    try:
        with open("static/index.html", "r") as f:
            html_content = f.read()
        return HTMLResponse(content=html_content)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Dashboard not found")

@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}

@app.get("/api/events")
async def get_events(limit: int = 50, db: Session = Depends(get_db)):
    """Get recent downtime events."""
    events = db.query(DowntimeEvent)\
        .order_by(DowntimeEvent.start_time.desc())\
        .limit(limit)\
        .all()
    
    return [{
        "id": event.id,
        "machine_id": event.machine_id,
        "machine_type": event.machine_type,
        "start_time": event.start_time.isoformat() if event.start_time else None,
        "end_time": event.end_time.isoformat() if event.end_time else None,
        "downtime_minutes": event.downtime_minutes,
        "description": event.description,
        "reason_category": event.reason_category,
        "severity": event.severity,
        "notes": event.notes,
        "created_at": event.created_at.isoformat() if event.created_at else None
    } for event in events]

@app.get("/api/stats")
async def get_stats(db: Session = Depends(get_db)):
    """Get dashboard statistics."""
    today = datetime.utcnow().date()
    
    # Total downtime minutes today
    today_events = db.query(DowntimeEvent)\
        .filter(DowntimeEvent.start_time >= today)\
        .all()
    
    total_downtime = sum(event.downtime_minutes or 0 for event in today_events)
    
    # Worst machine by downtime today
    machine_downtime = {}
    for event in today_events:
        if event.machine_id not in machine_downtime:
            machine_downtime[event.machine_id] = 0
        machine_downtime[event.machine_id] += event.downtime_minutes or 0
    
    worst_machine = None
    worst_downtime = 0
    for machine_id, downtime in machine_downtime.items():
        if downtime > worst_downtime:
            worst_downtime = downtime
            worst_machine = machine_id
    
    # Events count
    events_count = len(today_events)
    
    return {
        "total_downtime_minutes_today": round(total_downtime, 1),
        "worst_machine": worst_machine,
        "worst_machine_downtime_minutes": round(worst_downtime, 1),
        "events_today": events_count
    }

@app.post("/api/events/{event_id}/notes")
async def add_notes(event_id: int, notes: str, db: Session = Depends(get_db)):
    """Add manual notes to an event."""
    event = db.query(DowntimeEvent).filter(DowntimeEvent.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    
    event.notes = notes
    db.commit()
    return {"status": "success"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=APP_PORT)