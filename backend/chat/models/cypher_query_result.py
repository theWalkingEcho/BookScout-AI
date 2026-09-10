from dataclasses import dataclass
from typing import List, Dict, Any, Optional


@dataclass
class CypherQueryResult:
    query: str
    records: List[Dict[str, Any]]
    summary: Optional[Dict[str, Any]] = None
    execution_time_ms: float = 0.0
    error: Optional[str] = None
