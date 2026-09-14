from dataclasses import dataclass
from typing import List, Optional, Dict


@dataclass
class ChatResponse:
    answer: str
    followup_suggestions: Optional[List[str]] = None
    execution_time_ms: float = 0.0
    latency_breakdown: Optional[Dict[str, float]] = None
    error: Optional[str] = None
    query_used: Optional[str] = None
    sources: Optional[List[str]] = None

