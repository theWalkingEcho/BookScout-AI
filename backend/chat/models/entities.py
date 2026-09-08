from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from datetime import datetime


@dataclass
class ChatMessage:
    role: str  # "user" | "assistant" | "system"
    content: str
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass
class CypherQueryResult:
    query: str
    records: List[Dict[str, Any]]
    summary: Optional[Dict[str, Any]] = None
    execution_time_ms: float = 0.0
    error: Optional[str] = None


@dataclass
class BookListingOffer:
    store_name: str
    store_website: Optional[str]
    price: float
    original_price: Optional[float]
    currency: str
    in_stock: bool
    url: Optional[str]
    last_scraped: Optional[datetime] = None


@dataclass
class BookDetail:
    isbn: str
    title: str
    normalized_title: Optional[str] = None
    authors: List[str] = field(default_factory=list)
    categories: List[str] = field(default_factory=list)
    publisher: Optional[str] = None
    format: Optional[str] = None
    cover_image: Optional[str] = None
    description: Optional[str] = None
    offers: List[BookListingOffer] = field(default_factory=list)


@dataclass
class ChatResponse:
    answer: str
    followup_suggestions: Optional[List[str]] = None
    execution_time_ms: float = 0.0
    error: Optional[str] = None
    query_used: Optional[str] = None
    sources: Optional[List[str]] = None
