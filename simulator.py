import asyncio
import random
import os
from datetime import datetime, timedelta
from typing import AsyncGenerator, Dict, Any

SIMULATOR_ENABLED = os.getenv("SIMULATOR_ENABLED", "true").lower() == "true"
SIMULATOR_INTERVAL_SECONDS = int(os.getenv("SIMULATOR_INTERVAL_SECONDS", "8"))

MACHINE_TYPES = ["CNC Mill", "Lathe", "Press", "Conveyor", "Robot Arm", "Welder", "Injection Mold", "Packaging Line"]
MACHINE_IDS = [f"M{i:03d}" for i in range(1, 21)]  # M001 to M020

EVENT_TEMPLATES = [
    ("Mechanical Failure", "Unexpected vibration detected in spindle bearing assembly"),
    ("Mechanical Failure", "Hydraulic pressure loss in clamping system"),
    ("Operator Error", "Incorrect tool offset entered during setup"),
    ("Operator Error", "Safety interlock bypassed during maintenance procedure"),
    ("Material Shortage", "Raw material feeder jammed due to incorrect feed rate"),
    ("Material Shortage", "Waiting for approved material lot release from QA"),
    ("Maintenance", "Scheduled preventive maintenance overran estimated time"),
    ("Maintenance", "Calibration procedure required additional iterations"),
    ("Power Loss", "Brief power fluctuation triggered emergency stop"),
    ("Power Loss", "UPS battery swap required during preventative maintenance"),
    ("Unknown", "Intermittent fault logged - root cause under investigation"),
    ("Unknown", "Sensor reading outside normal parameters - no clear failure mode")
]

async def generate_event() -> Dict[str, Any]:
    """Generate a realistic machine stoppage event."""
    machine_id = random.choice(MACHINE_IDS)
    machine_type = random.choice(MACHINE_TYPES)
    category, description = random.choice(EVENT_TEMPLATES)
    
    # Start time is now or slightly in the past
    start_offset = random.randint(0, 300)  # Up to 5 minutes ago
    start_time = (datetime.now() - timedelta(seconds=start_offset)).isoformat()
    
    # Downtime duration (5 minutes to 4 hours)
    downtime_minutes = random.uniform(5, 240)
    
    # End time calculated from start + downtime
    end_time = (datetime.now() + timedelta(minutes=downtime_minutes - start_offset)).isoformat()
    
    return {
        "machine_id": machine_id,
        "machine_type": machine_type,
        "start_time": start_time,
        "end_time": end_time,
        "downtime_minutes": round(downtime_minutes, 2),
        "description": description,
        "reason_category": category,  # Will be refined by LLM
        "severity": "Medium"  # Will be refined by LLM
    }

async def event_stream() -> AsyncGenerator[Dict[str, Any], None]:
    """Async generator that yields events at the configured interval."""
    if not SIMULATOR_ENABLED:
        return
        yield  # Make it a generator even when disabled
    
    while True:
        event = await generate_event()
        yield event
        await asyncio.sleep(SIMULATOR_INTERVAL_SECONDS)