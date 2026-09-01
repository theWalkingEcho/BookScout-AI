from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class Author:
    name: str


@dataclass(frozen=True)
class Category:
    name: str


@dataclass(frozen=True)
class Book:
    isbn: str
    title: str
    normalized_title: str
    format: str
    cover_image: Optional[str] = None
    language: Optional[str] = None
    publisher: Optional[str] = None
    description: Optional[str] = None
    in_stock: bool = False
    text_embedding: Optional[list] = None  # Vector embedding for semantic search


@dataclass(frozen=True)
class Store:
    name: str
    website: str
    currency: str


@dataclass(frozen=True)
class Listing:
    """
    A first-class node in the graph:
      (:Store)-[:SELLS]->(:Listing)-[:FOR_BOOK]->(:Book)
    """
    listing_id: str
    price: float
    original_price: Optional[float]
    in_stock: bool
    url: str
    currency: str
    last_scraped: datetime
    store_name: str
    book_isbn: str
