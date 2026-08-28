from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class BookDTO:
    isbn: str
    title: str
    normalized_title: str
    format: str
    cover_image: Optional[str] = None
    language: Optional[str] = None
    publisher: Optional[str] = None
    description: Optional[str] = None
    in_stock: bool = False


@dataclass
class StoreDTO:
    name: str
    website: str
    currency: str


@dataclass
class ListingDTO:
    listing_id: str
    price: float
    original_price: Optional[float]
    in_stock: bool
    url: str
    currency: str
    last_scraped: datetime
    store_name: str
    book_isbn: str
