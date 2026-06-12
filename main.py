#!/usr/bin/env python3
"""
Machine Downtime Log - Field-services ticket tracker for manufacturing-floor machine stoppages
"""

import os
import json
import sqlite3
import asyncio
import time
import uuid
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn
import httpx
from pydantic import BaseModel
import logging

# Configuration from environment variables
APP_PORT = int(os.getenv("APP_PORT", "8742"))
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://198.18.5.11:8000/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "/ai/models/NVIDIA/Nemotron-3-120B/")
LLM_API_KEY = os.getenv("LLM_API_KEY", "LLM")
LLM_TIMEOUT_SECONDS = int(os.getenv("LLM_TIMEOUT_SECONDS", "15"))
DB_PATH = os.getenv("DB_PATH", "/data/downtime.db")
SIMULATOR_ENABLED = os.getenv("SIMULATOR_ENABLED", "true").lower() == "true"
SIMULATOR_INTERVAL_SECONDS = int(os.getenv("SIMULATOR_INTERVAL_SECONDS", "8"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Setup logging
logging.basicConfig(level=getattr(logging, LOG_LEVEL.upper()))
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(title="Machine Downtime Log", version="1.0.0")

# Database initialization
def init_db():
    """Initialize SQLite database with required tables"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Downtime events table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS downtime_events (
            id TEXT PRIMARY KEY,
            machine_id TEXT NOT NULL,
            machine_type TEXT NOT NULL,
            start_time TIMESTAMP NOT NULL,
            end_time TIMESTAMP,
            downtime_minutes REAL,
            reason_category TEXT,
            severity TEXT,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Machine events table (for simulator and real events)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS machine_events (
            id TEXT PRIMARY KEY,
            machine_id TEXT NOT NULL,
            machine_type TEXT NOT NULL,
            event_type TEXT NOT NULL,  -- 'start' or 'stop'
            timestamp TIMESTAMP NOT NULL,
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    conn.commit()
    conn.close()
    logger.info(f"Database initialized at {DB_PATH}")

# Pydantic models
class MachineEvent(BaseModel):
    machine_id: str
    machine_type: str
    event_type: str  # 'start' or 'stop'
    description: Optional[str] = None
    timestamp: Optional[str] = None

class DowntimeEvent(BaseModel):
    id: str
    machine_id: str
    machine_type: str
    start_time: str
    end_time: Optional[str] = None
    downtime_minutes: Optional[float] = None
    reason_category: Optional[str] = None
    severity: Optional[str] = None
    notes: Optional[str] = None

class LLMClassification(BaseModel):
    reason_category: str
    severity: str  # Low, Medium, High, Critical

# Global state for tracking active stoppages
active_stoppages: Dict[str, Dict] = {}  # machine_id -> event data

# LLM client for classification
class LLMClient:
    def __init__(self):
        self.base_url = LLM_BASE_URL
        self.model = LLM_MODEL
        self.api_key = LLM_API_KEY
        self.timeout = LLM_TIMEOUT_SECONDS
        self.client = httpx.AsyncClient(timeout=self.timeout)
    
    async def classify_downtime(self, description: str) -> LLMClassification:
        """Classify downtime using local LLM with fallback"""
        if not description or not description.strip():
            return LLMClassification(reason_category="Unknown", severity="Medium")
        
        prompt = f"""Classify the following machine downtime description into a reason category and severity level.

Description: {description}

Return ONLY a JSON object with exactly these two fields:
- reason_category: one of ["Mechanical Failure", "Operator Error", "Material Shortage", "Maintenance", "Power Loss", "Unknown"]
- severity: one of ["Low", "Medium", "High", "Critical"]

Base your classification on the description content. If unsure, use "Unknown" for reason_category and "Medium" for severity."""

        try:
            response = await self.client.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": self.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1,
                    "max_tokens": 100
                }
            )
            
            if response.status_code == 200:
                result = response.json()
                content = result["choices"][0]["message"]["content"].strip()
                
                # Try to parse JSON from response
                try:
                    # Extract JSON if it's wrapped in text
                    if "{" in content and "}" in content:
                        start = content.find("{")
                        end = content.rfind("}") + 1
                        json_str = content[start:end]
                        data = json.loads(json_str)
                        
                        # Validate fields
                        reason = data.get("reason_category", "Unknown")
                        severity = data.get("severity", "Medium")
                        
                        # Ensure valid values
                        valid_reasons = ["Mechanical Failure", "Operator Error", "Material Shortage", "Maintenance", "Power Loss", "Unknown"]
                        valid_severities = ["Low", "Medium", "High", "Critical"]
                        
                        if reason not in valid_reasons:
                            reason = "Unknown"
                        if severity not in valid_severities:
                            severity = "Medium"
                            
                        return LLMClassification(reason_category=reason, severity=severity)
                except (json.JSONDecodeError, KeyError):
                    pass
            
            # Fallback if anything goes wrong
            logger.warning(f"LLM classification failed or returned invalid format, using fallback")
            return LLMClassification(reason_category="Unknown", severity="Medium")
            
        except Exception as e:
            logger.error(f"LLM service error: {e}")
            return LLMClassification(reason_category="Unknown", severity="Medium")

llm_client = LLMClient()

# Event simulator
class EventSimulator:
    def __init__(self):
        self.running = False
        self.task = None
        
    async def start(self):
        if not SIMULATOR_ENABLED:
            logger.info("Event simulator disabled via SIMULATOR_ENABLED=false")
            return
            
        self.running = True
        self.task = asyncio.create_task(self._simulate_events())
        logger.info(f"Event simulator started with interval {SIMULATOR_INTERVAL_SECONDS}s")
    
    async def stop(self):
        self.running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        logger.info("Event simulator stopped")
    
    async def _simulate_events(self):
        """Generate realistic machine events for testing"""
        machine_types = ["CNC Mill", "Lathe", "Injection Mold", "Press", "Conveyor", "Robot Arm"]
        machine_prefixes = ["MC", "LT", "IM", "PR", "CV", "RB"]
        descriptions = [
            "Tool breakage detected",
            "Material jam in feeder",
            "Hydraulic pressure low",
            "Electrical fault in motor",
            "Operator intervention required",
            "Scheduled maintenance needed",
            "Power fluctuation detected",
            "Quality issue - part out of spec",
            "Coolant leak detected",
            "Air pressure insufficient"
        ]
        severities = ["Low", "Medium", "High", "Critical"]
        
        import random
        
        while self.running:
            try:
                # Select random machine
                machine_idx = random.randint(0, len(machine_types) - 1)
                machine_type = machine_types[machine_idx]
                machine_prefix = machine_prefixes[machine_idx]
                machine_id = f"{machine_prefix}{random.randint(1, 99):02d}"
                
                # Randomly choose start or stop event (biased towards starts to create stoppages)
                event_type = random.choices(["start", "stop"], weights=[0.7, 0.3])[0]
                
                description = random.choice(descriptions)
                severity = random.choice(severities)
                
                # Create event
                event_data = {
                    "machine_id": machine_id,
                    "machine_type": machine_type,
                    "event_type": event_type,
                    "description": f"{description} [{severity}]",
                    "timestamp": datetime.utcnow().isoformat() + "Z"
                }
                
                await self._process_machine_event(event_data)
                
                # Wait for next event
                await asyncio.sleep(SIMULATOR_INTERVAL_SECONDS)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in event simulator: {e}")
                await asyncio.sleep(SIMULATOR_INTERVAL_SECONDS)
    
    async def _process_machine_event(self, event_data: dict):
        """Process a machine event (start or stop)"""
        machine_id = event_data["machine_id"]
        
        if event_data["event_type"] == "start":
            # Start of a potential stoppage
            active_stoppages[machine_id] = {
                "machine_id": machine_id,
                "machine_type": event_data["machine_type"],
                "start_time": event_data["timestamp"],
                "description": event_data["description"],
                "timestamp": event_data["timestamp"]
            }
            logger.info(f"Machine {machine_id} started potential stoppage")
            
            # Save to database
            self._save_machine_event(event_data)
            
        elif event_data["event_type"] == "stop":
            # End of a stoppage - create downtime ticket
            if machine_id in active_stoppages:
                start_data = active_stoppages.pop(machine_id)
                
                # Calculate downtime
                try:
                    start_time = datetime.fromisoformat(start_data["start_time"].replace("Z", "+00:00"))
                    end_time = datetime.fromisoformat(event_data["timestamp"].replace("Z", "+00:00"))
                    downtime_minutes = (end_time - start_time).total_seconds() / 60.0
                except Exception:
                    downtime_minutes = 0.0
                
                # Extract description and severity from the start event description
                desc_parts = start_data["description"].split(" [")
                base_description = desc_parts[0] if len(desc_parts) > 0 else start_data["description"]
                severity_from_desc = "Medium"  # default
                if len(desc_parts) > 1 and desc_parts[1].endswith("]"):
                    severity_from_desc = desc_parts[1][:-1]
                
                # Classify with LLM (using the base description)
                classification = await llm_client.classify_downtime(base_description)
                
                # Create downtime event
                downtime_event = {
                    "id": str(uuid.uuid4()),
                    "machine_id": machine_id,
                    "machine_type": start_data["machine_type"],
                    "start_time": start_data["start_time"],
                    "end_time": event_data["timestamp"],
                    "downtime_minutes": round(downtime_minutes, 2),
                    "reason_category": classification.reason_category,
                    "severity": classification.severity,
                    "notes": f"Auto-generated from event: {base_description}"
                }
                
                # Save to database
                self._save_downtime_event(downtime_event)
                self._save_machine_event(event_data)
                
                logger.info(f"Downtime ticket created for {machine_id}: {downtime_minutes:.1f} min, {classification.reason_category}, {classification.severity}")
            else:
                # Stop without matching start - treat as new start immediately followed by stop
                logger.warning(f"Received stop event for {machine_id} without matching start")
                # Save the stop event anyway for tracking
                self._save_machine_event(event_data)
    
    def _save_machine_event(self, event_data: dict):
        """Save machine event to database"""
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO machine_events (id, machine_id, machine_type, event_type, timestamp, description)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            str(uuid.uuid4()),
            event_data["machine_id"],
            event_data["machine_type"],
            event_data["event_type"],
            event_data["timestamp"],
            event_data.get("description", "")
        ))
        conn.commit()
        conn.close()
    
    def _save_downtime_event(self, event_data: dict):
        """Save downtime event to database"""
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO downtime_events 
            (id, machine_id, machine_type, start_time, end_time, downtime_minutes, reason_category, severity, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            event_data["id"],
            event_data["machine_id"],
            event_data["machine_type"],
            event_data["start_time"],
            event_data["end_time"],
            event_data["downtime_minutes"],
            event_data["reason_category"],
            event_data["severity"],
            event_data["notes"]
        ))
        conn.commit()
        conn.close()

# Initialize simulator
event_simulator = EventSimulator()

# Startup and shutdown events
@app.on_event("startup")
async def startup_event():
    """Initialize application on startup"""
    logger.info("Starting Machine Downtime Log application...")
    
    # Check if port is available
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    result = sock.connect_ex(('0.0.0.0', APP_PORT))
    sock.close()
    
    if result == 0:
        logger.error(f"Port {APP_PORT} is already in use. Please set APP_PORT to a free port.")
        raise RuntimeError(f"Port {APP_PORT} is already in use")
    
    # Initialize database
    init_db()
    
    # Start event simulator
    await event_simulator.start()
    
    logger.info(f"Machine Downtime Log started successfully on port {APP_PORT}")

@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    logger.info("Shutting down Machine Downtime Log...")
    await event_simulator.stop()

# API Endpoints
@app.get("/", response_class=HTMLResponse)
async def get_dashboard():
    """Serve the main dashboard HTML"""
    html_content = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Machine Downtime Log</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { 
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
            color: white;
        }
        .container { max-width: 1200px; margin: 0 auto; }
        header { 
            text-align: center; 
            margin-bottom: 30px;
            padding: 20px;
            background: rgba(0,0,0,0.2);
            border-radius: 15px;
        }
        h1 { font-size: 2.5rem; margin-bottom: 10px; }
        .subtitle { opacity: 0.9; font-size: 1.1rem; }
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }
        .stat-card {
            background: rgba(255,255,255,0.15);
            backdrop-filter: blur(10px);
            border-radius: 15px;
            padding: 25px;
            text-align: center;
            border: 1px solid rgba(255,255,255,0.2);
            transition: transform 0.2s ease;
        }
        .stat-card:hover { transform: translateY(-5px); }
        .stat-value { 
            font-size: 2.8rem; 
            font-weight: bold; 
            margin: 10px 0;
            text-shadow: 0 2px 4px rgba(0,0,0,0.3);
        }
        .stat-label { 
            font-size: 1.1rem; 
            opacity: 0.9;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        .worst-machine { 
            position: relative;
            overflow: hidden;
        }
        .worst-machine::before {
            content: '';
            position: absolute;
            top: -50%;
            left: -50%;
            width: 200%;
            height: 200%;
            background: conic-gradient(from 0deg, transparent, rgba(255,255,255,0.3));
            animation: rotate 4s linear infinite;
            pointer-events: none;
        }
        @keyframes rotate { to { transform: rotate(360deg); } }
        .machine-details { 
            margin-top: 15px;
            text-align: left;
        }
        .machine-row {
            display: flex;
            justify-content: space-between;
            margin: 8px 0;
            padding: 8px 0;
            border-bottom: 1px solid rgba(255,255,255,0.1);
        }
        .machine-label { font-weight: 500; }
        .machine-value { 
            font-family: monospace;
            background: rgba(0,0,0,0.3);
            padding: 2px 8px;
            border-radius: 10px;
        }
        .severity-badge {
            display: inline-block;
            padding: 4px 8px;
            border-radius: 12px;
            font-size: 0.85rem;
            font-weight: 600;
            text-transform: uppercase;
        }
        .severity-low { background: #4ade80; color: #064e3b; }
        .severity-medium { background: #fbbf24; color: #92400e; }
        .severity-high { background: #f87171; color: #991b1b; }
        .severity-critical { background: #ef4444; color: #ffffff; animation: pulse 1.5s infinite; }
        @keyframes pulse { 0% { opacity: 1; } 50% { opacity: 0.7; } 100% { opacity: 1; } }
        .latency-indicator {
            position: fixed;
            top: 20px;
            right: 20px;
            background: rgba(0,0,0,0.6);
            backdrop-filter: blur(10px);
            border: 1px solid rgba(255,255,255,0.2);
            border-radius: 12px;
            padding: 12px 16px;
            font-family: monospace;
            font-size: 0.9rem;
            z-index: 1000;
        }
        .llm-status {
            position: fixed;
            top: 20px;
            left: 20px;
            display: flex;
            align-items: center;
            gap: 8px;
            background: rgba(0,0,0,0.6);
            backdrop-filter: blur(10px);
            border: 1px solid rgba(255,255,255,0.2);
            border-radius: 12px;
            padding: 12px 16px;
            font-size: 0.9rem;
            z-index: 1000;
        }
        .llm-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            display: inline-block;
        }
        .llm-connected { background: #10b981; animation: pulse 2s infinite; }
        .llm-disconnected { background: #ef4444; }
        .footer {
            text-align: center;
            margin-top: 40px;
            opacity: 0.8;
            font-size: 0.9rem;
        }
        .refresh-btn {
            position: fixed;
            bottom: 20px;
            right: 20px;
            background: rgba(255,255,255,0.2);
            backdrop-filter: blur(10px);
            border: 1px solid rgba(255,255,255,0.3);
            color: white;
            border: none;
            padding: 12px 20px;
            border-radius: 25px;
            cursor: pointer;
            font-size: 1rem;
            transition: all 0.2s ease;
        }
        .refresh-btn:hover {
            background: rgba(255,255,255,0.3);
            transform: scale(1.05);
        }
        .refresh-btn:active {
            transform: scale(0.95);
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>Machine Downtime Log</h1>
            <div class="subtitle">Real-time manufacturing floor monitoring</div>
        </header>
        
        <div class="llm-status">
            <div class="llm-dot llm-disconnected" id="llm-status">●</div>
            <span>LLM: Disconnected</span>
        </div>
        
        <div class="latency-indicator">
            Latency: <span id="latency">--</span> ms
        </div>
        
        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-label">Total Downtime Today</div>
                <div class="stat-value" id="total-downtime">0</div>
                <div>minutes</div>
            </div>
            <div class="stat-card worst-machine">
                <div class="stat-label">Worst Machine Today</div>
                <div class="stat-value" id="worst-machine">None</div>
                <div class="machine-details" id="machine-details">
                    <!-- Machine details will be inserted here -->
                </div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Active Stoppages</div>
                <div class="stat-value" id="active-stoppages">0</div>
                <div>machines</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Events Processed</div>
                <div class="stat-value" id="events-processed">0</div>
                <div>total</div>
            </div>
        </div>
    </div>
    
    <button class="refresh-btn" onclick="location.reload()">↻ Refresh</button>

    <script>
        let startTime = Date.now();
        let lastUpdate = startTime;
        
        // Update latency indicator
        function updateLatency() {
            const now = Date.now();
            const latency = now - lastUpdate;
            document.getElementById('latency').textContent = latency;
            lastUpdate = now;
        }
        
        // Update LLM status indicator
        async function updateLLMStatus() {
            try {
                const response = await fetch('/api/llm-status');
                const data = await response.json();
                const dot = document.getElementById('llm-status');
                const text = document.querySelector('.llm-status span');
                
                if data.connected) {
                    dot.className = 'llm-dot llm-connected';
                    text.textContent = 'LLM: Connected';
                } else {
                    dot.className = 'llm-dot llm-disconnected';
                    text.textContent = 'LLM: Disconnected';
                }
            } catch (error) {
                document.getElementById('llm-status').className = 'llm-dot llm-disconnected';
                document.querySelector('.llm-status span').textContent = 'LLM: Error';
            }
        }
        
        // Fetch dashboard data
        async function updateDashboard() {
            try {
                const response = await fetch('/api/dashboard');
                const data = await response.json();
                
                // Update total downtime
                document.getElementById('total-downtime').textContent = data.total_downtime_minutes || 0;
                
                // Update worst machine
                const worstMachineEl = document.getElementById('worst-machine');
                const machineDetailsEl = document.getElementById('machine-details');
                
                if data.worst_machine && data.worst_machine.machine_id) {
                    worstMachineEl.textContent = data.worst_machine.machine_id;
                    
                    // Clear and rebuild machine details
                    machineDetailsEl.innerHTML = '';
                    
                    const details = [
                        ['Machine Type', data.worst_machine.machine_type || 'Unknown'],
                        ['Downtime', `${(data.worst_machine.downtime_minutes || 0).toFixed(1)} min`],
                        ['Reason', data.worst_machine.reason_category || 'Unknown'],
                        ['Severity', data.worst_machine.severity || 'Medium']
                    ];
                    
                    details.forEach(([label, value]) => {
                        const row = document.createElement('div');
                        row.className = 'machine-row';
                        row.innerHTML = `<span class="machine-label">${label}:</span><span class="machine-value">${value}</span>`;
                        machineDetailsEl.appendChild(row);
                    });
                    
                    // Add severity badge
                    const severityBadge = document.createElement('span');
                    severityBadge.className = `severity-badge severity-${(data.worst_machine.severity || 'medium').toLowerCase()}`;
                    severityBadge.textContent = data.worst_machine.severity || 'Medium';
                    machineDetailsEl.appendChild(severityBadge);
                } else {
                    worstMachineEl.textContent = 'None';
                    machineDetailsEl.innerHTML = '<div>No downtime recorded today</div>';
                }
                
                // Update active stoppages
                document.getElementById('active-stoppages').textContent = data.active_stoppages || 0;
                
                // Update events processed
                document.getElementById('events-processed').textContent = data.events_processed || 0;
                
            } catch (error) {
                console.error('Error fetching dashboard data:', error);
            }
        }
        
        // Initialize updates
        setInterval(updateLatency, 100);
        setInterval(updateLLMStatus, 5000);
        setInterval(updateDashboard, 3000);
        
        // Initial load
        updateLatency();
        updateLLMStatus();
        updateDashboard();
    </script>
</body>
</html>
    """
    return HTMLResponse(content=html_content)

@app.get("/api/dashboard")
async def get_dashboard_data():
    """Get dashboard statistics"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Get today's date boundaries
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)
    
    # Total downtime today
    cursor.execute("""
        SELECT COALESCE(SUM(downtime_minutes), 0) 
        FROM downtime_events 
        WHERE start_time >= ? AND start_time < ?
    """, (today_start.isoformat(), today_end.isoformat()))
    total_downtime = cursor.fetchone()[0] or 0
    
    # Worst machine today (most downtime)
    cursor.execute("""
        SELECT machine_id, machine_type, SUM(downtime_minutes) as total_downtime,
               reason_category, severity
        FROM downtime_events 
        WHERE start_time >= ? AND start_time < ?
        GROUP BY machine_id, machine_type, reason_category, severity
        ORDER BY total_downtime DESC
        LIMIT 1
    """, (today_start.isoformat(), today_end.isoformat()))
    worst_machine_row = cursor.fetchone()
    
    worst_machine = None
    if worst_machine_row:
        worst_machine = {
            "machine_id": worst_machine_row[0],
            "machine_type": worst_machine_row[1],
            "downtime_minutes": worst_machine_row[2],
            "reason_category": worst_machine_row[3],
            "severity": worst_machine_row[4]
        }
    
    # Active stoppages count
    cursor.execute("""
        SELECT COUNT(*) FROM machine_events 
        WHERE event_type = 'start' 
        AND id NOT IN (
            SELECT me.id FROM machine_events me
            JOIN machine_events me2 ON me.machine_id = me2.machine_id 
            WHERE me.event_type = 'start' AND me2.event_type = 'stop' 
            AND me2.timestamp > me.timestamp
        )
    """)
    # Simpler approach: count starts without matching stops after them
    cursor.execute("""
        SELECT COUNT(*) FROM (
            SELECT me1.machine_id, me1.timestamp as start_time,
                   MIN(me2.timestamp) as end_time
            FROM machine_events me1
            LEFT JOIN machine_events me2 ON me1.machine_id = me2.machine_id 
                AND me1.event_type = 'start' AND me2.event_type = 'stop'
                AND me2.timestamp > me1.timestamp
            WHERE me1.event_type = 'start'
            GROUP BY me1.machine_id, me1.timestamp
            HAVING end_time IS NULL
        )
    """)
    active_stoppages = cursor.fetchone()[0] or 0
    
    # Total events processed
    cursor.execute("SELECT COUNT(*) FROM machine_events")
    events_processed = cursor.fetchone()[0] or 0
    
    conn.close()
    
    return {
        "total_downtime_minutes": round(total_downtime, 1),
        "worst_machine": worst_machine,
        "active_stoppages": active_stoppages,
        "events_processed": events_processed
    }

@app.get("/api/llm-status")
async def get_llm_status():
    """Check if LLM service is reachable"""
    try:
        # Try a simple request to the LLM endpoint
        response = await llm_client.client.get(
            f"{llm_client.base_url}/models",
            headers={"Authorization": f"Bearer {llm_client.api_key}"},
            timeout=5.0
        )
        return {"connected": response.status_code == 200}
    except Exception:
        return {"connected": False}

@app.post("/api/events")
async def receive_machine_event(event: MachineEvent):
    """Receive a machine event from external source"""
    event_data = event.dict()
    if not event_data.get("timestamp"):
        event_data["timestamp"] = datetime.utcnow().isoformat() + "Z"
    
    await event_simulator._process_machine_event(event_data)
    return {"status": "received", "event_id": str(uuid.uuid4())}

# Health check endpoint
@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "version": "1.0.0",
        "port": APP_PORT,
        "simulator_enabled": SIMULATOR_ENABLED
    }

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=APP_PORT,
        reload=False,
        access_log=True
    )