import os
import json
import httpx
from typing import Dict, Any, Optional
from datetime import datetime

LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://198.18.5.11:8000/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "/ai/models/NVIDIA/Nemotron-3-120B/")
LLM_API_KEY = os.getenv("LLM_API_KEY", "LLM")
LLM_TIMEOUT_SECONDS = int(os.getenv("LLM_TIMEOUT_SECONDS", "15"))

VALID_CATEGORIES = {
    "Mechanical Failure", "Operator Error", "Material Shortage", 
    "Maintenance", "Power Loss", "Unknown"
}
VALID_SEVERITIES = {"Low", "Medium", "High", "Critical"}

async def classify_event(description: str) -> Dict[str, str]:
    """
    Classify a machine event using the LLM.
    Returns dict with reason_category and severity.
    Falls back to Unclassified/Medium on any failure.
    """
    if not description or not description.strip():
        return {"reason_category": "Unknown", "severity": "Low"}
    
    prompt = f"""Analyze this machine event description and classify it.
Description: "{description}"

Respond with ONLY a JSON object containing exactly these two fields:
- reason_category: one of "Mechanical Failure", "Operator Error", "Material Shortage", "Maintenance", "Power Loss", or "Unknown"
- severity: one of "Low", "Medium", "High", or "Critical"

Example: {{"reason_category": "Mechanical Failure", "severity": "High"}}"""

    headers = {
        "Authorization": f"Bearer {LLM_API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": LLM_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_tokens": 50,
        "response_format": {"type": "json_object"}
    }
    
    try:
        async with httpx.AsyncClient(timeout=LLM_TIMEOUT_SECONDS) as client:
            response = await client.post(
                f"{LLM_BASE_URL}/chat/completions",
                headers=headers,
                json=payload
            )
            response.raise_for_status()
            
            result = response.json()
            content = result["choices"][0]["message"]["content"].strip()
            
            # Parse and validate the JSON response
            try:
                data = json.loads(content)
                category = data.get("reason_category", "").strip()
                severity = data.get("severity", "").strip()
                
                # Validate against allowed values
                if category not in VALID_CATEGORIES:
                    category = "Unclassified"
                if severity not in VALID_SEVERITIES:
                    severity = "Medium"
                    
                return {"reason_category": category, "severity": severity}
            except (json.JSONDecodeError, KeyError, TypeError):
                # Malformed JSON or missing fields
                return {"reason_category": "Unclassified", "severity": "Medium"}
                
    except Exception as e:
        # Network error, timeout, HTTP error, etc.
        # Log to stdout for Splunk visibility
        print(json.dumps({
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": "WARNING",
            "message": f"LLM service unavailable or error: {str(e)[:100]}",
            "component": "llm_client",
            "fallback_used": True
        }))
        return {"reason_category": "Unclassified", "severity": "Medium"}