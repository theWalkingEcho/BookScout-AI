from dataclasses import dataclass, field
from typing import List, Optional

from chat.models.book_listing_offer import BookListingOffer


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
