import sqlite3
import os
from contextlib import contextmanager
from typing import Optional, List, Dict, Any
from datetime import datetime

DB_PATH = os.getenv("DB_PATH", "/data/downtime.db")

def init_db():
    """Initialize the database with required tables."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with get_db() as conn:
        conn.execute("""
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
                manual_note TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_machine_start ON downtime_events(machine_id, start_time)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_created_at ON downtime_events(created_at)
        """)
        conn.commit()

@contextmanager
def get_db():
    """Context manager for database connections."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def insert_event(event_data: Dict[str, Any]) -> int:
    """Insert a new downtime event and return its ID."""
    with get_db() as conn:
        cursor = conn.execute("""
            INSERT INTO downtime_events 
            (machine_id, machine_type, start_time, end_time, downtime_minutes, 
             description, reason_category, severity, manual_note)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            event_data['machine_id'],
            event_data['machine_type'],
            event_data['start_time'],
            event_data.get('end_time'),
            event_data.get('downtime_minutes'),
            event_data.get('description', ''),
            event_data.get('reason_category', 'Unclassified'),
            event_data.get('severity', 'Medium'),
            event_data.get('manual_note', '')
        ))
        conn.commit()
        return cursor.lastrowid

def update_event_end(event_id: int, end_time: str, downtime_minutes: float):
    """Update an event with end time and calculated downtime."""
    with get_db() as conn:
        conn.execute("""
            UPDATE downtime_events 
            SET end_time = ?, downtime_minutes = ?
            WHERE id = ?
        """, (end_time, downtime_minutes, event_id))
        conn.commit()

def get_events(limit: int = 100) -> List[Dict[str, Any]]:
    """Get recent downtime events."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT * FROM downtime_events 
            ORDER BY start_time DESC 
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(row) for row in rows]

def get_today_stats() -> Dict[str, Any]:
    """Get today's downtime statistics."""
    with get_db() as conn:
        # Total downtime minutes today
        total_row = conn.execute("""
            SELECT COALESCE(SUM(downtime_minutes), 0) as total
            FROM downtime_events 
            WHERE date(start_time) = date('now')
        """).fetchone()
        
        # Worst machine by downtime today
        worst_row = conn.execute("""
            SELECT machine_id, machine_type, SUM(downtime_minutes) as total_downtime
            FROM downtime_events 
            WHERE date(start_time) = date('now')
            GROUP BY machine_id, machine_type
            ORDER BY total_downtime DESC
            LIMIT 1
        """).fetchone()
        
        total_downtime = total_row['total'] if total_row else 0
        worst_machine = dict(worst_row) if worst_row else None
        
        return {
            'total_downtime_minutes': total_downtime,
            'worst_machine': worst_machine
        }