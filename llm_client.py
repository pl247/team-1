import json
import logging
import time
import os
from typing import Dict, Any, Optional
import httpx
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

# LLM configuration from environment variables
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://198.18.5.11:8000/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "/ai/models/NVIDIA/Nemotron-3-120B/")
LLM_API_KEY = os.getenv("LLM_API_KEY", "LLM")
LLM_TIMEOUT_SECONDS = int(os.getenv("LLM_TIMEOUT_SECONDS", "15"))

# Valid categories and severities
VALID_CATEGORIES = {
    "Mechanical Failure", "Operator Error", "Material Shortage", 
    "Maintenance", "Power Loss", "Unknown"
}
VALID_SEVERITIES = {"Low", "Medium", "High", "Critical"}

# Fallback values
FALLBACK_CATEGORY = "Unclassified"
FALLBACK_SEVERITY = "Medium"

class LLMResponse(BaseModel):
    reason_category: str
    severity: str

class LLMClient:
    def __init__(self):
        self.client = httpx.AsyncClient(
            base_url=LLM_BASE_URL,
            headers={"Authorization": f"Bearer {LLM_API_KEY}"},
            timeout=LLM_TIMEOUT_SECONDS
        )
    
    async def classify_event(self, description: str) -> Dict[str, str]:
        """
        Classify a machine event using the LLM with defensive parsing and fallback.
        Returns dict with reason_category and severity.
        """
        if not description or not description.strip():
            logger.warning("Empty description provided for LLM classification")
            return {
                "reason_category": FALLBACK_CATEGORY,
                "severity": FALLBACK_SEVERITY
            }
        
        # Construct the prompt for strict JSON output
        prompt = f"""Analyze this manufacturing equipment event and classify it with strict JSON output.

Event description: "{description}"

You must respond with ONLY a JSON object containing exactly these two fields:
- reason_category: one of "Mechanical Failure", "Operator Error", "Material Shortage", "Maintenance", "Power Loss", or "Unknown"
- severity: one of "Low", "Medium", "High", or "Critical"

Do not include any other text, explanation, or formatting. Respond with valid JSON only."""

        try:
            start_time = time.time()
            response = await self.client.post(
                "/chat/completions",
                json={
                    "model": LLM_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1,  # Low temperature for consistent output
                    "max_tokens": 100
                }
            )
            
            logger.debug(f"LLM request took {time.time() - start_time:.2f}s")
            
            if response.status_code != 200:
                logger.error(f"LLM service returned status {response.status_code}: {response.text}")
                return {
                    "reason_category": FALLBACK_CATEGORY,
                    "severity": FALLBACK_SEVERITY
                }
            
            result = response.json()
            
            # Extract the content from the response
            if "choices" not in result or not result["choices"]:
                logger.error("Invalid LLM response: missing choices")
                return {
                    "reason_category": FALLBACK_CATEGORY,
                    "severity": FALLBACK_SEVERITY
                }
            
            content = result["choices"][0]["message"]["content"].strip()
            
            # Try to parse as JSON
            try:
                parsed = json.loads(content)
                validated = LLMResponse(**parsed)
                
                # Validate against allowed values
                if (validated.reason_category not in VALID_CATEGORIES or 
                    validated.severity not in VALID_SEVERITIES):
                    logger.warning(f"LLM returned invalid values: {validated.reason_category}, {validated.severity}")
                    return {
                        "reason_category": FALLBACK_CATEGORY,
                        "severity": FALLBACK_SEVERITY
                    }
                
                logger.info(f"LLM classification successful: {validated.reason_category}, {validated.severity}")
                return {
                    "reason_category": validated.reason_category,
                    "severity": validated.severity
                }
                
            except (json.JSONDecodeError, ValidationError) as e:
                logger.error(f"Failed to parse/validate LLM response: {e}. Content: '{content}'")
                return {
                    "reason_category": FALLBACK_CATEGORY,
                    "severity": FALLBACK_SEVERITY
                }
                
        except httpx.TimeoutException:
            logger.error("LLM request timed out")
            return {
                "reason_category": FALLBACK_CATEGORY,
                "severity": FALLBACK_SEVERITY
            }
        except httpx.RequestError as e:
            logger.error(f"LLM request failed: {e}")
            return {
                "reason_category": FALLBACK_CATEGORY,
                "severity": FALLBACK_SEVERITY
            }
        except Exception as e:
            logger.error(f"Unexpected error in LLM client: {e}")
            return {
                "reason_category": FALLBACK_CATEGORY,
                "severity": FALLBACK_SEVERITY
            }
    
    async def close(self):
        await self.client.aclose()