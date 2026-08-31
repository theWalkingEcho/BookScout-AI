from datetime import datetime
from typing import Iterable, List, Optional
import logging
import os

from neo4j import GraphDatabase

from domain.entities import Author, Book, Category, Listing, Store
from domain.repositories import BookstoreRepository

logger = logging.getLogger(__name__)


class Neo4jBookstoreRepository(BookstoreRepository):
    def __init__(self, uri: str, user: str, password: str, database: str = None):
        logger.info("Connecting to Neo4j at %s, database=%s", uri, database)
        self.driver = GraphDatabase.driver(uri, auth=(user, password))
        self.database = database
        self._ensure_constraints()
        self._setup_vector_index()

    # ------------------------------------------------------------------
    # Infrastructure helpers
    # ------------------------------------------------------------------

    def _ensure_constraints(self) -> None:
        constraints = [
            "CREATE CONSTRAINT IF NOT EXISTS FOR (b:Book) REQUIRE b.isbn IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (a:Author) REQUIRE a.name IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (c:Category) REQUIRE c.name IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (s:Store) REQUIRE s.name IS UNIQUE",
        ]
        for query in constraints:
            self._execute(query)

    def _setup_vector_index(self) -> None:
        """Creates a Neo4j vector index on Book.textEmbedding if it does not exist.

        Requires Neo4j >= 5.11. Silently skips on older versions.
        """
        embedding_dimensions = 768  # Fixed: must match EMBEDDING_DIMENSIONS in config.py
        query = (
            "CREATE VECTOR INDEX book_title_embedding IF NOT EXISTS "
            "FOR (b:Book) ON (b.textEmbedding) "
            f"OPTIONS {{indexConfig: {{`vector.dimensions`: {embedding_dimensions}, `vector.similarity_function`: 'cosine'}}}}"
        )
        try:
            self._execute(query)
            logger.info("Vector index 'book_title_embedding' ensured (dimensions=%d).", embedding_dimensions)
        except Exception as e:
            logger.warning(
                "Could not create vector index (Neo4j < 5.11 or index already exists): %s", e
            )

    def clear_database(self) -> None:
        logger.info("Clearing Neo4j database before refresh")
        self._execute("MATCH (n) DETACH DELETE n")

    def close(self):
        logger.info("Closing Neo4j driver")
        self.driver.close()

    def _execute(self, query: str, parameters: dict = None) -> None:
        session_kwargs = {}
        if self.database is not None:
            session_kwargs["database"] = self.database
        logger.debug("Running Cypher query: %s | params=%s", query, parameters)
        with self.driver.session(**session_kwargs) as session:
            session.run(query, parameters or {}).consume()

    def _fetch_all(self, query: str, parameters: dict = None) -> list:
        session_kwargs = {}
        if self.database is not None:
            session_kwargs["database"] = self.database
        logger.debug("Running Cypher query: %s | params=%s", query, parameters)
        with self.driver.session(**session_kwargs) as session:
            result = session.run(query, parameters or {})
            return list(result)

    def _fetch_single(self, query: str, parameters: dict = None):
        session_kwargs = {}
        if self.database is not None:
            session_kwargs["database"] = self.database
        logger.debug("Running Cypher query: %s | params=%s", query, parameters)
        with self.driver.session(**session_kwargs) as session:
            return session.run(query, parameters or {}).single()

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def add_or_update_author(self, author: Author) -> None:
        logger.debug("Upserting author %s", author.name)
        self._execute(
            "MERGE (a:Author {name: $name}) SET a.name = $name",
            {"name": author.name},
        )

    def add_or_update_category(self, category: Category) -> None:
        logger.debug("Upserting category %s", category.name)
        self._execute(
            "MERGE (c:Category {name: $name}) SET c.name = $name",
            {"name": category.name},
        )

    def add_or_update_book(self, book: Book) -> None:
        logger.debug("Upserting book isbn=%s title=%s", book.isbn, book.title)
        self._execute(
            """
            MERGE (b:Book {isbn: $isbn})
            SET b.title            = $title,
                b.normalizedTitle  = $normalized_title,
                b.format           = $format,
                b.coverImage       = $cover_image,
                b.language         = $language,
                b.publisher        = $publisher,
                b.description      = $description,
                b.inStock          = $in_stock
            """,
            {
                "isbn": book.isbn,
                "title": book.title,
                "normalized_title": book.normalized_title,
                "format": book.format,
                "cover_image": book.cover_image,
                "language": book.language,
                "publisher": book.publisher,
                "description": book.description,
                "in_stock": book.in_stock,
            },
        )

    def add_or_update_store(self, store: Store) -> None:
        logger.debug("Upserting store %s", store.name)
        self._execute(
            """
            MERGE (s:Store {name: $name})
            SET s.website  = $website,
                s.currency = $currency
            """,
            {"name": store.name, "website": store.website, "currency": store.currency},
        )

    def add_or_update_listing(self, listing: Listing) -> None:
        """
        Persists one offer relationship per book/store listing:

            (:Book)-[:HAS_LISTING {listingId, price, originalPrice, inStock, ...}]->(:Store)
        """
        logger.debug(
            "Upserting listing %s for book %s at store %s",
            listing.listing_id,
            listing.book_isbn,
            listing.store_name,
        )
        query = """
            MATCH (s:Store {name: $store_name})
            MATCH (b:Book  {isbn: $book_isbn})
            MERGE (b)-[r:HAS_LISTING]->(s)
            SET r.listingId     = $listing_id,
                r.price         = $price,
                r.originalPrice = $original_price,
                r.inStock       = $in_stock,
                r.url           = $url,
                r.currency      = $currency,
                r.lastScraped   = datetime($last_scraped),
                r.updatedAt     = datetime($last_scraped)
        """
        self._execute(
            query,
            {
                "listing_id": listing.listing_id,
                "price": listing.price,
                "original_price": listing.original_price,
                "in_stock": listing.in_stock,
                "url": listing.url,
                "currency": listing.currency,
                "last_scraped": listing.last_scraped.isoformat(),
                "store_name": listing.store_name,
                "book_isbn": listing.book_isbn,
            },
        )

    def set_book_embedding(self, isbn: str, embedding: List[float]) -> None:
        """Stores a pre-computed embedding vector on a Book node."""
        logger.debug("Storing embedding for book isbn=%s (dim=%d)", isbn, len(embedding))
        self._execute(
            "MATCH (b:Book {isbn: $isbn}) SET b.textEmbedding = $embedding",
            {"isbn": isbn, "embedding": embedding},
        )

    # ------------------------------------------------------------------
    # Link operations
    # ------------------------------------------------------------------

    def link_book_author(self, isbn: str, author_name: str) -> None:
        self._execute(
            """
            MATCH (b:Book   {isbn: $isbn})
            MATCH (a:Author {name: $author_name})
            MERGE (b)-[:WRITTEN_BY]->(a)
            """,
            {"isbn": isbn, "author_name": author_name},
        )

    def link_book_category(self, isbn: str, category_name: str) -> None:
        self._execute(
            """
            MATCH (b:Book     {isbn: $isbn})
            MATCH (c:Category {name: $category_name})
            MERGE (b)-[:IN_CATEGORY]->(c)
            """,
            {"isbn": isbn, "category_name": category_name},
        )

    # ------------------------------------------------------------------
    # Delete operations
    # ------------------------------------------------------------------

    def delete_listing(self, listing_id: str) -> None:
        self._execute(
            "MATCH ()-[r:HAS_LISTING {listingId: $listing_id}]->() DELETE r",
            {"listing_id": listing_id},
        )

    def delete_book(self, isbn: str) -> None:
        self._execute(
            "MATCH (b:Book {isbn: $isbn}) DETACH DELETE b",
            {"isbn": isbn},
        )

    def delete_store(self, store_name: str) -> None:
        self._execute(
            "MATCH (s:Store {name: $store_name}) DETACH DELETE s",
            {"store_name": store_name},
        )

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def list_listing_ids_for_store(self, store_name: str) -> Iterable[str]:
        logger.debug("Listing existing listings for store %s", store_name)
        result = self._fetch_all(
            """
            MATCH ()-[r:HAS_LISTING]->(:Store {name: $store_name})
            RETURN r.listingId AS listing_id
            """,
            {"store_name": store_name},
        )
        ids = [row["listing_id"] for row in result]
        logger.debug("Found %d listings for store %s", len(ids), store_name)
        return ids

    def get_listing(self, listing_id: str) -> Optional[Listing]:
        result = self._fetch_single(
            """
                 MATCH (b:Book)-[r:HAS_LISTING {listingId: $listing_id}]->(s:Store)
                 RETURN r.listingId AS listing_id,
                     r.price AS price,
                     r.originalPrice AS original_price,
                     r.inStock AS in_stock,
                     r.url AS url,
                     r.currency AS currency,
                     r.lastScraped AS last_scraped,
                     s.name AS store_name,
                     b.isbn AS book_isbn
            """,
            {"listing_id": listing_id},
        )
        if result is None:
            return None
        return Listing(
            listing_id=result["listing_id"],
            price=result["price"],
            original_price=result["original_price"],
            in_stock=result["in_stock"],
            url=result["url"],
            currency=result["currency"],
            last_scraped=result["last_scraped"],
            store_name=result["store_name"],
            book_isbn=result["book_isbn"],
        )

    def get_book(self, isbn: str) -> Optional[Book]:
        result = self._fetch_single(
            """
            MATCH (b:Book {isbn: $isbn})
            RETURN b.isbn AS isbn, b.title AS title,
                   b.normalizedTitle AS normalized_title, b.format AS format,
                   b.coverImage AS cover_image, b.language AS language,
                   b.publisher AS publisher, b.description AS description,
                   b.inStock AS in_stock
            """,
            {"isbn": isbn},
        )
        if result is None:
            return None
        return Book(
            isbn=result["isbn"],
            title=result["title"],
            normalized_title=result["normalized_title"],
            format=result["format"],
            cover_image=result["cover_image"],
            language=result["language"],
            publisher=result["publisher"],
            description=result["description"],
            in_stock=result.get("in_stock", False),
        )

    def get_store(self, store_name: str) -> Optional[Store]:
        result = self._fetch_single(
            "MATCH (s:Store {name: $name}) RETURN s.name AS name, s.website AS website, s.currency AS currency",
            {"name": store_name},
        )
        if result is None:
            return None
        return Store(name=result["name"], website=result["website"], currency=result["currency"])

    def list_books(self) -> Iterable[Book]:
        result = self._fetch_all(
            """
            MATCH (b:Book)
            RETURN b.isbn AS isbn, b.title AS title,
                   b.normalizedTitle AS normalized_title, b.format AS format,
                   b.coverImage AS cover_image, b.language AS language,
                   b.publisher AS publisher, b.description AS description,
                   b.inStock AS in_stock
            """
        )
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

    def list_stores(self) -> Iterable[Store]:
        result = self._fetch_all(
            "MATCH (s:Store) RETURN s.name AS name, s.website AS website, s.currency AS currency"
        )
        for row in result:
            yield Store(name=row["name"], website=row["website"], currency=row["currency"])

    # ------------------------------------------------------------------
    # Chatbot-friendly query helpers
    # ------------------------------------------------------------------

    def list_books_with_listings(self) -> List[dict]:
        """Return every book together with all its store listings.

        Useful for the chatbot to answer price/availability questions.
        """
        result = self._fetch_all(
            """
            MATCH (b:Book)-[r:HAS_LISTING]->(s:Store)
            OPTIONAL MATCH (b)-[:WRITTEN_BY]->(a:Author)
            OPTIONAL MATCH (b)-[:IN_CATEGORY]->(c:Category)
            RETURN b.title        AS title,
                   b.isbn         AS isbn,
                   a.name         AS author,
                   c.name         AS category,
                   s.name         AS store,
                     r.price        AS price,
                     r.originalPrice AS original_price,
                     r.inStock      AS available,
                     r.currency     AS currency,
                     r.url          AS url,
                     r.lastScraped  AS last_scraped
                 ORDER BY b.normalizedTitle, r.price
            """
        )
        return [dict(row) for row in result]

    def find_cheapest_listing_for_book(self, title: str) -> Optional[dict]:
        """Return the cheapest in-stock listing for a book matching *title*.

        Uses case-insensitive substring matching on normalizedTitle.
        """
        result = self._fetch_single(
            """
                        MATCH (b:Book)-[r:HAS_LISTING]->(s:Store)
            WHERE toLower(b.normalizedTitle) CONTAINS toLower($title)
                            AND r.inStock = true
            RETURN b.title   AS title,
                   s.name    AS store,
                                     r.price   AS price,
                                     r.currency AS currency,
                                     r.url     AS url
                        ORDER BY r.price ASC
            LIMIT 1
            """,
            {"title": title},
        )
        return dict(result) if result else None
