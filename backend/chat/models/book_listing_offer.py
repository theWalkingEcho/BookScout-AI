from dataclasses import dataclass
from typing import Optional
from datetime import datetime


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
