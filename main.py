import os
import socket
import sys
import json
import asyncio
from datetime import datetime
from typing import List, Dict, Any
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

# Import our modules
from db import init_db, insert_event, update_event_end, get_events, get_today_stats
from simulator import event_stream, SIMULATOR_ENABLED, SIMULATOR_INTERVAL_SECONDS
from llm_client import classify_event

APP_PORT = int(os.getenv("APP_PORT", "8742"))

def check_port_available(port: int) -> bool:
    """Check if a port is available for binding."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(('0.0.0.0', port))
            return True
        except OSError:
            return False

def log_structured(level: str, message: str, component: str = "backend", **kwargs):
    """Log structured JSON to stdout for Splunk ingestion."""
    log_entry = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "level": level,
        "message": message,
        "component": component,
        **kwargs
    }
    print(json.dumps(log_entry), flush=True)

# Initialize FastAPI app
app = FastAPI(title="Machine Downtime Log", version="1.0.0")

# Initialize database on startup
@app.on_event("startup")
async def startup_event():
    # Check if port is available
    if not check_port_available(APP_PORT):
        error_msg = f"Port {APP_PORT} is already in use. Please set APP_PORT to a free port or stop the conflicting service."
        log_structured("ERROR", error_msg, component="startup")
        print(error_msg, file=sys.stderr, flush=True)
        sys.exit(1)
    
    # Initialize database
    init_db()
    log_structured("INFO", f"Database initialized at {os.getenv('DB_PATH', '/data/downtime.db')}", component="startup")
    log_structured("INFO", f"Starting server on port {APP_PORT}", component="startup")
    
    # Start simulator if enabled
    if SIMULATOR_ENABLED:
        asyncio.create_task(simulator_background_task())
        log_structured("INFO", f"Event simulator enabled (interval: {SIMULATOR_INTERVAL_SECONDS}s)", component="startup")

async def simulator_background_task():
    """Background task to process events from the simulator."""
    async for event_data in event_stream():
        try:
            # Classify the event using LLM (with fallback)
            classification = await classify_event(event_data['description'])
            event_data.update(classification)
            
            # Insert the start event
            event_id = insert_event(event_data)
            
            # Log the event start
            log_structured("INFO", f"Event started: {event_data['machine_id']} - {event_data['description']}", 
                         component="event_processor", event_id=event_id)
            
            # Simulate the passage of time and update with end time
            # In a real system, this would come from actual machine signals
            # For simulation, we'll update after the configured downtime
            await asyncio.sleep(event_data['downtime_minutes'] * 60)  # Convert minutes to seconds
            
            # Update with end time
            update_event_end(event_id, event_data['end_time'], event_data['downtime_minutes'])
            
            # Log the event completion
            log_structured("INFO", f"Event ended: {event_data['machine_id']} - {event_data['downtime_minutes']:.1f} minutes", 
                         component="event_processor", event_id=event_id)
            
        except Exception as e:
            log_structured("ERROR", f"Error processing event: {str(e)}", component="event_processor")
            continue

@app.get("/", response_class=HTMLResponse)
async def get_dashboard(request: Request):
    """Serve the main dashboard HTML."""
    try:
        with open("static/index.html", "r") as f:
            html_content = f.read()
        return HTMLResponse(content=html_content)
    except FileNotFoundError:
        return HTMLResponse(content="<h1>Dashboard not found</h1><p>Please ensure static/index.html exists</p>", status_code=404)

@app.get("/api/events")
async def get_api_events(limit: int = 50):
    """Get recent downtime events."""
    events = get_events(limit=limit)
    return JSONResponse(content=events)

@app.get("/api/stats")
async def get_api_stats():
    """Get today's downtime statistics."""
    stats = get_today_stats()
    return JSONResponse(content=stats)

@app.post("/api/events/{event_id}/note")
async def add_manual_note(event_id: int, note: Dict[str, str]):
    """Add a manual note to an event."""
    note_text = note.get("note", "").strip()
    if not note_text:
        return JSONResponse(content={"error": "Note cannot be empty"}, status_code=400)
    
    # In a full implementation, we would update the database here
    # For now, we'll log it and return success
    log_structured("INFO", f"Manual note added to event {event_id}: {note_text[:100]}", 
                  component="api", event_id=event_id)
    return JSONResponse(content={"success": True})

@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return JSONResponse(content={
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "port": APP_PORT,
        "simulator_enabled": SIMULATOR_ENABLED
    })

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=APP_PORT, log_level="warning")