import logging
from typing import Optional

logger = logging.getLogger(__name__)


class GenerateAndStoreEmbeddingsUseCase:
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
            books = list(self._books_without_embeddings())

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

    def _books_without_embeddings(self):
        """Yields Book objects that do not yet have a textEmbedding property."""
        result = self.repository._fetch_all(
            """
            MATCH (b:Book)
            WHERE b.textEmbedding IS NULL
            RETURN b.isbn AS isbn, b.title AS title,
                   b.normalizedTitle AS normalized_title, b.format AS format,
                   b.coverImage AS cover_image, b.language AS language,
                   b.publisher AS publisher, b.description AS description,
                   b.inStock AS in_stock
            """
        )
        from domain.entities import Book
        for row in result:
            yield Book(
                isbn=row["isbn"],
                title=row["title"],
                normalized_title=row["normalized_title"],
                format=row["format"],
                cover_image=row["cover_image"],
                language=row["language"],
                publisher=row["publisher"],
                description=row["description"],
                in_stock=row.get("in_stock", False),
            )

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
