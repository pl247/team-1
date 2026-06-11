import os
import sqlite3
import json
import asyncio
import time
import sys
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
import uvicorn
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
import httpx
from pydantic import BaseModel

# Configuration from environment variables with defaults
APP_PORT = int(os.getenv("APP_PORT", 8742))
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://198.18.5.11:8000/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "nemotron 3 super 120B")
LLM_TIMEOUT_SECONDS = int(os.getenv("LLM_TIMEOUT_SECONDS", 15))
LLM_API_KEY = os.getenv("LLM_API_KEY", "LLM")
DB_PATH = os.getenv("DB_PATH", "/data/downtime.db")
SIMULATOR_ENABLED = os.getenv("SIMULATOR_ENABLED", "true").lower() == "true"
SIMULATOR_INTERVAL_SECONDS = int(os.getenv("SIMULATOR_INTERVAL_SECONDS", 8))

# Ensure the data directory exists for SQLite
os.makedirs(os.path.dirname(DB_PATH) if os.path.dirname(DB_PATH) else '/data', exist_ok=True)

# Initialize FastAPI app
app = FastAPI(title="Machine Downtime Log")

# Database setup
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS downtime (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            machine_id TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT,
            downtime_minutes REAL,
            reason_category TEXT,
            severity TEXT,
            notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

# Pydantic models for LLM response
class LLMResponse(BaseModel):
    reason_category: str
    severity: str

# LLM client with fallback
async def classify_event(description: str) -> Dict[str, str]:
    """
    Call the local LLM to classify the event description.
    Returns a dict with reason_category and severity.
    Falls back to Unclassified/Medium on failure.
    """
    prompt = f"""Analyze the following machine event description and classify it.
Return ONLY a JSON object with two fields:
- reason_category: one of ["Mechanical Failure", "Operator Error", "Material Shortage", "Maintenance", "Power Loss", "Unknown"]
- severity: one of ["Low", "Medium", "High", "Critical"]

Event description: "{description}"

JSON:"""

    try:
        async with httpx.AsyncClient(timeout=LLM_TIMEOUT_SECONDS) as client:
            response = await client.post(
                f"{LLM_BASE_URL}/v1/chat/completions",
                json={
                    "model": LLM_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1,
                    "max_tokens": 100,
                },
                headers={
                    "Authorization": f"Bearer {LLM_API_KEY}"
                }
            )
            response.raise_for_status()
            result = response.json()
            content = result["choices"][0]["message"]["content"].strip()
            # Try to parse JSON from the response
            # Find the first { and last } to extract JSON
            start = content.find('{')
            end = content.rfind('}') + 1
            if start >= 0 and end > start:
                json_str = content[start:end]
                parsed = json.loads(json_str)
                # Validate the fields
                reason = parsed.get("reason_category", "Unclassified")
                severity = parsed.get("severity", "Medium")
                # Ensure they are in expected lists (optional, but we can fallback if not)
                valid_reasons = ["Mechanical Failure", "Operator Error", "Material Shortage", "Maintenance", "Power Loss", "Unknown"]
                valid_severities = ["Low", "Medium", "High", "Critical"]
                if reason not in valid_reasons:
                    reason = "Unclassified"
                if severity not in valid_severities:
                    severity = "Medium"
                return {"reason_category": reason, "severity": severity}
            else:
                # No JSON found
                raise ValueError("No JSON in response")
    except Exception as e:
        print(f"LLM classification failed: {e}")
        return {"reason_category": "Unclassified", "severity": "Medium"}

# Event simulator
async def generate_machine_event() -> Dict[str, Any]:
    """Generate a realistic machine event for simulation."""
    import random
    machine_ids = ["MILL-01", "LATHE-02", "PRESS-03", "WELDER-04", "CNC-05"]
    descriptions = [
        "Machine stopped due to unusual vibration and noise",
        "Operator reported jam in feed mechanism",
        "Material ran out during operation",
        "Scheduled maintenance overrun",
        "Power fluctuation caused controller reset",
        "Tool broke during cutting operation",
        "Coolant system leak detected",
        "Safety gate triggered unexpectedly",
        "Software timeout in control system",
        "Hydraulic pressure low alarm"
    ]
    machine_id = random.choice(machine_ids)
    description = random.choice(descriptions)
    return {
        "machine_id": machine_id,
        "description": description,
        "timestamp": datetime.utcnow().isoformat() + "Z"
    }

# Background task to process events
class EventProcessor:
    def __init__(self):
        self.latest_events = []  # For SSE
        self.downtime_today = 0
        self.worst_machine = None
        self.worst_downtime = 0
        self.lock = asyncio.Lock()
        
    async def process_event(self, event: Dict[str, Any]):
        """Process a machine event: classify, log downtime, update stats."""
        machine_id = event["machine_id"]
        description = event["description"]
        start_time = event["timestamp"]
        
        # Classify via LLM
        classification = await classify_event(description)
        reason = classification["reason_category"]
        severity = classification["severity"]
        
        # Simulate: assume event means machine just stopped
        # We'll log a downtime start; in reality we'd need to track start/end
        # For simulation, we'll generate a random downtime duration between 5 and 120 minutes
        import random
        downtime_minutes = random.uniform(5, 120)
        end_time = (datetime.utcnow() + timedelta(minutes=downtime_minutes)).isoformat() + "Z"
        
        # Store in database
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''
            INSERT INTO downtime (machine_id, start_time, end_time, downtime_minutes, reason_category, severity)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (machine_id, start_time, end_time, downtime_minutes, reason, severity))
        conn.commit()
        conn.close()
        
        # Update statistics (simple in-memory for today)
        async with self.lock:
            # Recalculate today's stats from database for accuracy
            self.recalculate_stats()
            
            # Create event for SSE
            sse_event = {
                "machine_id": machine_id,
                "reason": reason,
                "severity": severity,
                "downtime_minutes": round(downtime_minutes, 1),
                "timestamp": start_time
            }
            self.latest_events.append(sse_event)
            # Keep only last 50 events
            if len(self.latest_events) > 50:
                self.latest_events = self.latest_events[-50:]
    
    def recalculate_stats(self):
        """Recalculate total downtime today and worst machine from database."""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        today = datetime.utcnow().date()
        c.execute('''
            SELECT machine_id, SUM(downtime_minutes) 
            FROM downtime 
            WHERE DATE(start_time) = ?
            GROUP BY machine_id
        ''', (today.isoformat(),))
        rows = c.fetchall()
        conn.close()
        
        total = 0
        worst_machine = None
        worst_downtime = 0
        for machine_id, sum_minutes in rows:
            total += sum_minutes
            if sum_minutes > worst_downtime:
                worst_downtime = sum_minutes
                worst_machine = machine_id
        
        self.downtime_today = total
        self.worst_machine = worst_machine
        self.worst_downtime = worst_downtime

# Global event processor
event_processor = EventProcessor()

# Startup event
@app.on_event("startup")
async def startup_event():
    init_db()
    # Start background task if simulator enabled
    if SIMULATOR_ENABLED:
        asyncio.create_task(simulator_task())

# Simulator task
async def simulator_task():
    while True:
        event = await generate_machine_event()
        await event_processor.process_event(event)
        await asyncio.sleep(SIMULATOR_INTERVAL_SECONDS)

# Routes
@app.get("/", response_class=HTMLResponse)
async def get_dashboard():
    """Serve the single-page dashboard."""
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Machine Downtime Log</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <style>
            .worst-machine { animation: pulse 2s infinite; }
            @keyframes pulse {
                0% { box-shadow: 0 0 0 0 rgba(239, 68, 68, 0.4); }
                70% { box-shadow: 0 0 0 10px rgba(239, 68, 68, 0); }
                100% { box-shadow: 0 0 0 0 rgba(239, 68, 68, 0); }
            }
        </style>
    </head>
    <body class="bg-gray-50 min-h-screen">
        <div class="container mx-auto px-4 py-8">
            <h1 class="text-3xl font-bold text-gray-800 mb-6">Machine Downtime Log</h1>
            <div class="bg-white rounded-lg shadow-md p-6 mb-6">
                <h2 class="text-xl font-semibold mb-4">Live Status</h2>
                <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div>
                        <p class="text-gray-600">Total Downtime Today:</p>
                        <p id="total-downtime" class="text-2xl font-bold text-gray-800">0 minutes</p>
                    </div>
                    <div>
                        <p class="text-gray-600">Worst Performing Machine:</p>
                        <p id="worst-machine" class="text-2xl font-bold text-gray-800">None</p>
                    </div>
                </div>
                <div class="mt-4 p-3 bg-blue-50 rounded">
                    <p class="text-sm text-blue-800">
                        <strong>Note:</strong> Classification runs on-premises via local LLM (<code>http://198.18.5.11:8000/v1</code>). 
                        No data leaves the factory. Event-to-display latency shown below.
                    </p>
                </div>
            </div>
            
            <div class="bg-white rounded-lg shadow-md p-6">
                <h2 class="text-xl font-semibold mb-4">Recent Downtime Events</h2>
                <div id="events-list" class="space-y-3">
                    <p class="text-gray-500 text-center py-4">No events yet...</p>
                </div>
            </div>
            
            <div class="mt-6 p-3 bg-gray-50 rounded text-xs text-gray-500">
                Latency: <span id="latency">--</span> ms | 
                <span id="llm-status">LLM: Ready</span>
            </div>
        </div>

        <script>
            const EVENT_SOURCE = new EventSource("/events");
            let lastUpdate = Date.now();
            
            EVENT_SOURCE.onmessage = function(event) {
                const data = JSON.parse(event.data);
                updateDashboard(data);
                // Calculate latency: time since event timestamp
                const eventTime = new Date(data.timestamp).getTime();
                const latency = Date.now() - eventTime;
                document.getElementById('latency').textContent = latency.toFixed(0);
            };
            
            EVENT_SOURCE.onerror = function(err) {
                console.error("SSE error:", err);
                document.getElementById('llm-status').textContent = "LLM: Disconnected";
            };
            
            function updateDashboard(data) {
                // Update totals (we'll fetch from server periodically for accuracy)
                fetchStats();
                
                // Add event to list
                const eventsDiv = document.getElementById('events-list');
                const eventEl = document.createElement('div');
                eventEl.className = 'p-4 border rounded-lg bg-gray-50';
                
                // Highlight if worst machine
                const isWorst = data.machine_id === document.getElementById('worst-machine')?.textContent;
                if (isWorst) {
                    eventEl.classList.add('worst-machine', 'border-red-200', 'bg-red-50');
                }
                
                eventEl.innerHTML = `
                    <div class="flex justify-between items-start">
                        <span class="font-mono">${data.machine_id}</span>
                        <span class="px-2 py-1 text-xs rounded-full 
                            ${data.severity === 'Critical' ? 'bg-red-100 text-red-800' :
                              data.severity === 'High' ? 'bg-orange-100 text-orange-800' :
                              data.severity === 'Medium' ? 'bg-yellow-100 text-yellow-800' :
                              'bg-green-100 text-green-800'}">
                            ${data.severity}
                        </span>
                    </div>
                    <p class="mt-2 text-sm text-gray-700">${data.reason}</p>
                    <p class="mt-1 text-xs text-gray-500">${data.downtime_minutes} min ↓</p>
                `;
                
                eventsDiv.insertBefore(eventEl, eventsDiv.firstChild);
                // Keep only 10 visible
                while (eventsDiv.children.length > 10) {
                    eventsDiv.removeChild(eventsDiv.lastChild);
                }
            }
            
            async function fetchStats() {
                try {
                    const response = await fetch('/stats');
                    const stats = await response.json();
                    document.getElementById('total-downtime').textContent = stats.total_downtime_minutes + ' minutes';
                    const worstEl = document.getElementById('worst-machine');
                    worstEl.textContent = stats.worst_machine || 'None';
                    if (stats.worst_machine) {
                        worstEl.classList.add('worst-machine');
                    } else {
                        worstEl.classList.remove('worst-machine');
                    }
                } catch (e) {
                    console.error('Failed to fetch stats:', e);
                }
            }
            
            // Fetch stats every 5 seconds
            setInterval(fetchStats, 5000);
            // Initial fetch
            fetchStats();
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

@app.get("/events")
async def event_stream():
    """Server-Sent Events endpoint for real-time updates."""
    async def event_generator():
        while True:
            # Send latest event if available
            async with event_processor.lock:
                if event_processor.latest_events:
                    event = event_processor.latest_events[-1]  # Send most recent
                    yield f"data: {json.dumps(event)}\n\n"
            await asyncio.sleep(0.1)  # Check every 100ms
    
    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.get("/stats")
async def get_stats():
    """Get current statistics for dashboard."""
    async with event_processor.lock:
        return {
            "total_downtime_minutes": round(event_processor.downtime_today, 1),
            "worst_machine": event_processor.worst_machine,
            "worst_downtime_minutes": round(event_processor.worst_downtime, 1)
        }

# Health check
@app.get("/health")
async def health():
    return {"status": "ok"}

if __name__ == "__main__":
    # Check if port is available
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    result = sock.connect_ex(('127.0.0.1', APP_PORT))
    sock.close()
    if result == 0:
        print(f"Error: Port {APP_PORT} is already in use.", file=sys.stderr)
        print(f"Set APP_PORT environment variable to a free port and try again.", file=sys.stderr)
        sys.exit(1)
    
    uvicorn.run(app, host="0.0.0.0", port=APP_PORT)