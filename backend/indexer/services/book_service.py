from datetime import datetime
from typing import Optional
import logging

from models.entities import Author, Book, Category, Listing, Store
from repositories.bookstore_repository import BookstoreRepository

logger = logging.getLogger(__name__)


class BookService:
    """Consolidated service for all book-related operations"""

    def __init__(self, repository: BookstoreRepository):
        self.repository = repository

    # Upsert operations
    def upsert_book(
        self,
        book: Book,
        author: Author,
        category: Category,
        store: Store,
        listing: Listing,
        embedding: list = None,
    ) -> None:
        """
        Upsert a book with all its relationships and optional embedding.
        
        Args:
            book: The Book entity
            author: The Author entity
            category: The Category entity
            store: The Store entity
            listing: The Listing entity
            embedding: Optional text embedding vector for semantic search
        """
        # Create or update book with embedding if provided
        book_with_embedding = Book(
            isbn=book.isbn,
            title=book.title,
            normalized_title=book.normalized_title,
            format=book.format,
            cover_image=book.cover_image,
            language=book.language,
            publisher=book.publisher,
            description=book.description,
            in_stock=book.in_stock,
            text_embedding=embedding,
        )
        
        logger.info("Upserting book %s and listing %s", book.isbn, listing.listing_id)
        self.repository.add_or_update_author(author)
        self.repository.add_or_update_category(category)
        self.repository.add_or_update_book(book_with_embedding)
        self.repository.link_book_author(book.isbn, author.name)
        self.repository.link_book_category(book.isbn, category.name)
        self.repository.add_or_update_store(store)
        self.repository.add_or_update_listing(listing)

    # Delete operations
    def delete_listing(self, listing_id: str) -> None:
        logger.info("Deleting listing %s", listing_id)
        self.repository.delete_listing(listing_id)

    def delete_book(self, isbn: str) -> None:
        logger.info("Deleting book %s", isbn)
        self.repository.delete_book(isbn)

    def delete_store(self, store_name: str) -> None:
        logger.info("Deleting store %s", store_name)
        self.repository.delete_store(store_name)

    # Read operations
    def get_book(self, isbn: str) -> Optional[Book]:
        return self.repository.get_book(isbn)

    def list_books(self):
        return list(self.repository.list_books())


class GenerateAndStoreEmbeddingsService:
    """
    Fetches all Book nodes that do not yet have a textEmbedding vector,
    generates embeddings via the EmbeddingService, and stores them back
    in Neo4j on the matching Book node.

    Run this after a full scrape / index cycle to keep vectors up to date.
    """

    def __init__(self, repository, embedding_service):
        """
        Args:
            repository: Neo4jBookstoreRepository (or any BookstoreRepository with
                        list_books() and set_book_embedding() methods).
            embedding_service: EmbeddingService instance.
        """
        self.repository = repository
        self.embedding_service = embedding_service

    def execute(self, reembed_all: bool = False, batch_size: int = 50) -> int:
        """
        Generate and store embeddings for books.

        Args:
            reembed_all: If True, regenerate embeddings for ALL books, not just
                         those missing a vector.
            batch_size:  How many books to process before logging progress.

        Returns:
            Number of books successfully embedded.
        """
        logger.info(
            "Starting embedding generation (reembed_all=%s, batch_size=%d)",
            reembed_all, batch_size,
        )

        if reembed_all:
            books = list(self.repository.list_books())
        else:
            books = list(self.repository.list_books_without_embedding())

        total = len(books)
        logger.info("Found %d book(s) to embed.", total)

        success_count = 0
        for idx, book in enumerate(books, start=1):
            # Build the embedding context text from available metadata
            authors = list(self._get_book_authors(book.isbn))
            categories = list(self._get_book_categories(book.isbn))

            embedding_text = self.embedding_service.build_embedding_text(
                title=book.title,
                description=book.description,
                authors=authors,
                categories=categories,
            )

            vector = self.embedding_service.generate(embedding_text)
            if vector is None:
                logger.warning("Failed to embed book isbn=%s title=%r — skipping.", book.isbn, book.title)
                continue

            self.repository.set_book_embedding(book.isbn, vector)
            success_count += 1

            if idx % batch_size == 0 or idx == total:
                logger.info("Embedded %d/%d books…", idx, total)

        logger.info(
            "Embedding generation complete: %d/%d succeeded.",
            success_count, total,
        )
        return success_count

    def _get_book_authors(self, isbn: str):
        """Returns a list of author names for a given book ISBN."""
        result = self.repository._fetch_all(
            "MATCH (b:Book {isbn: $isbn})-[:WRITTEN_BY]->(a:Author) RETURN a.name AS name",
            {"isbn": isbn},
        )
        return [row["name"] for row in result if row.get("name")]

    def _get_book_categories(self, isbn: str):
        """Returns a list of category names for a given book ISBN."""
        result = self.repository._fetch_all(
            "MATCH (b:Book {isbn: $isbn})-[:IN_CATEGORY]->(c:Category) RETURN c.name AS name",
            {"isbn": isbn},
        )
        return [row["name"] for row in result if row.get("name")]
