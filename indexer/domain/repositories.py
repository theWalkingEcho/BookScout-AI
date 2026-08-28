from abc import ABC, abstractmethod
from typing import Iterable, List, Optional
from .entities import Author, Book, Category, Listing, Store


class BookstoreRepository(ABC):
    @abstractmethod
    def add_or_update_author(self, author: Author) -> None:
        raise NotImplementedError

    @abstractmethod
    def add_or_update_category(self, category: Category) -> None:
        raise NotImplementedError

    @abstractmethod
    def add_or_update_book(self, book: Book) -> None:
        raise NotImplementedError

    @abstractmethod
    def add_or_update_store(self, store: Store) -> None:
        raise NotImplementedError

    @abstractmethod
    def add_or_update_listing(self, listing: Listing) -> None:
        raise NotImplementedError

    @abstractmethod
    def link_book_author(self, isbn: str, author_name: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def link_book_category(self, isbn: str, category_name: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def list_listing_ids_for_store(self, store_name: str) -> Iterable[str]:
        raise NotImplementedError

    @abstractmethod
    def delete_listing(self, listing_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def delete_book(self, isbn: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def delete_store(self, store_name: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_listing(self, listing_id: str) -> Optional[Listing]:
        raise NotImplementedError

    @abstractmethod
    def get_book(self, isbn: str) -> Optional[Book]:
        raise NotImplementedError

    @abstractmethod
    def get_store(self, store_name: str) -> Optional[Store]:
        raise NotImplementedError

    @abstractmethod
    def list_books(self) -> Iterable[Book]:
        raise NotImplementedError

    @abstractmethod
    def list_stores(self) -> Iterable[Store]:
        raise NotImplementedError

    @abstractmethod
    def list_books_with_listings(self) -> List[dict]:
        raise NotImplementedError

    @abstractmethod
    def find_cheapest_listing_for_book(self, title: str) -> Optional[dict]:
        raise NotImplementedError

    @abstractmethod
    def clear_database(self) -> None:
        raise NotImplementedError
