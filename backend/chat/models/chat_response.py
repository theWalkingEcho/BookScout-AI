from dataclasses import dataclass
from typing import List, Optional


@dataclass
class ChatResponse:
    answer: str
    followup_suggestions: Optional[List[str]] = None
    execution_time_ms: float = 0.0
    error: Optional[str] = None
    query_used: Optional[str] = None
    sources: Optional[List[str]] = None
