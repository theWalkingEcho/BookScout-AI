from datetime import datetime
from typing import Optional
import logging

from domain.entities import Author, Book, Category, Listing, Store
from domain.repositories import BookstoreRepository

logger = logging.getLogger(__name__)


class UpsertBookUseCase:
    def __init__(self, repository: BookstoreRepository):
        self.repository = repository

    def execute(
        self,
        book: Book,
        author: Author,
        category: Category,
        store: Store,
        listing: Listing,
    ) -> None:
        logger.info("Upserting book %s and listing %s", book.isbn, listing.listing_id)
        self.repository.add_or_update_author(author)
        self.repository.add_or_update_category(category)
        self.repository.add_or_update_book(book)
        self.repository.link_book_author(book.isbn, author.name)
        self.repository.link_book_category(book.isbn, category.name)
        self.repository.add_or_update_store(store)
        self.repository.add_or_update_listing(listing)


class DeleteListingUseCase:
    def __init__(self, repository: BookstoreRepository):
        self.repository = repository

    def execute(self, listing_id: str) -> None:
        logger.info("Deleting listing %s", listing_id)
        self.repository.delete_listing(listing_id)


class DeleteBookUseCase:
    def __init__(self, repository: BookstoreRepository):
        self.repository = repository

    def execute(self, isbn: str) -> None:
        logger.info("Deleting book %s", isbn)
        self.repository.delete_book(isbn)


class DeleteStoreUseCase:
    def __init__(self, repository: BookstoreRepository):
        self.repository = repository

    def execute(self, store_name: str) -> None:
        logger.info("Deleting store %s", store_name)
        self.repository.delete_store(store_name)


class GetBookUseCase:
    def __init__(self, repository: BookstoreRepository):
        self.repository = repository

    def execute(self, isbn: str) -> Optional[Book]:
        return self.repository.get_book(isbn)


class ListBooksUseCase:
    def __init__(self, repository: BookstoreRepository):
        self.repository = repository

    def execute(self):
        return list(self.repository.list_books())
