import re
import time
import logging
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional

try:
    from models.entities import ChatMessage, CypherQueryResult
except ImportError:
    try:
        from chat.models.entities import ChatMessage, CypherQueryResult
    except ImportError:
        from backend.chat.models.entities import ChatMessage, CypherQueryResult

from neo4j import GraphDatabase, Driver
from neo4j.exceptions import ServiceUnavailable, DriverError

logger = logging.getLogger(__name__)


class IGraphDatabaseReader(ABC):
    """
    Interface for read-only interactions with the Neo4j graph database.
    Adheres to the Dependency Inversion Principle (DIP).
    """

    @abstractmethod
    def execute_read_query(self, query: str, parameters: Optional[Dict[str, Any]] = None) -> CypherQueryResult:
        """Executes a safe read-only Cypher query and returns structured results."""
        pass

    @abstractmethod
    def get_schema_summary(self) -> Dict[str, Any]:
        """Returns node labels, relationships, and property keys."""
        pass

    @abstractmethod
    def get_store_names(self) -> List[str]:
        """Returns the list of indexed bookstore names."""
        pass

    @abstractmethod
    def get_stats(self) -> Dict[str, int]:
        """Returns counts of Books, Authors, Categories, Stores, and Listings."""
        pass

    @abstractmethod
    def health_check(self) -> bool:
        """Checks if the database is reachable and active."""
        pass

    @abstractmethod
    def vector_search(self, query_embedding: List[float], top_k: int = 10) -> CypherQueryResult:
        """Performs a vector similarity search using a pre-computed query embedding."""
        pass

    @abstractmethod
    def fulltext_search(self, query_text: str, top_k: int = 10) -> CypherQueryResult:
        """Performs a fulltext keyword search using Lucene index."""
        pass

    @abstractmethod
    def hybrid_search(
        self,
        query_text: str,
        query_embedding: Optional[List[float]] = None,
        top_k: int = 15,
    ) -> List[Dict[str, Any]]:
        """Combines vector similarity search and fulltext keyword search."""
        pass

    @abstractmethod
    def get_live_schema(self) -> Dict[str, Any]:
        """Introspects and returns the actual live graph schema from Neo4j."""
        pass


class Neo4jGraphReader(IGraphDatabaseReader):
    """
    Infrastructure implementation of IGraphDatabaseReader using the official Neo4j Python SDK.
    Executes read-only queries with validation and connection management.
    """

    FORBIDDEN_KEYWORDS = [
        r"\bCREATE\b",
        r"\bMERGE\b",
        r"\bDELETE\b",
        r"\bDETACH\b",
        r"\bSET\b",
        r"\bREMOVE\b",
        r"\bDROP\b",
        r"\bALTER\b",
        r"\bCALL\s+dbms\b",
    ]

    def __init__(
        self,
        uri: str,
        user: str,
        password: str,
        database: Optional[str] = None,
        embedding_dimensions: int = 3072,
    ):
        self.uri = uri
        self.user = user
        self.password = password
        self.database = database or None
        self.embedding_dimensions = embedding_dimensions
        self._driver: Optional[Driver] = None
        self._init_driver()
        self._ensure_indexes()

    def _init_driver(self) -> None:
        if not self.uri or not self.password:
            logger.warning("Neo4j credentials not fully provided. Driver not initialized.")
            return
        try:
            self._driver = GraphDatabase.driver(
                self.uri,
                auth=(self.user, self.password),
                connection_timeout=30,
                max_connection_lifetime=3600,
            )
            logger.info("Neo4j driver connected to %s (database=%s)", self.uri, self.database)
        except Exception as e:
            logger.error("Failed to connect to Neo4j: %s", e)
            self._driver = None

    def _ensure_indexes(self) -> None:
        """Ensures vector and fulltext indexes exist in Neo4j."""
        if not self._driver:
            return

        session_kwargs = {}
        if self.database:
            session_kwargs["database"] = self.database

        # 1. Fulltext index on Book title, normalizedTitle, and description
        try:
            with self._driver.session(**session_kwargs) as session:
                session.run("""
                    CREATE FULLTEXT INDEX book_fulltext_index IF NOT EXISTS
                    FOR (b:Book) ON EACH [b.title, b.normalizedTitle, b.description]
                """).consume()
                logger.info("Ensured fulltext index 'book_fulltext_index'")
        except Exception as e:
            logger.warning("Could not ensure fulltext index: %s", e)

        # 2. Vector index on Book.textEmbedding
        try:
            with self._driver.session(**session_kwargs) as session:
                session.run(f"""
                    CREATE VECTOR INDEX book_title_embedding IF NOT EXISTS
                    FOR (b:Book) ON (b.textEmbedding)
                    OPTIONS {{indexConfig: {{`vector.dimensions`: {self.embedding_dimensions}, `vector.similarity_function`: 'cosine'}}}}
                """).consume()
                logger.info("Ensured vector index 'book_title_embedding' (dim=%d)", self.embedding_dimensions)
        except Exception as e:
            logger.warning("Could not ensure vector index: %s", e)

    def _is_safe_read_query(self, query: str) -> bool:
        """Ensures query is read-only and does not mutate graph data."""
        clean_q = re.sub(r"//.*", "", query)  # Remove single line comments
        clean_q = re.sub(r"/\*.*?\*/", "", clean_q, flags=re.DOTALL)  # Remove block comments
        for pattern in self.FORBIDDEN_KEYWORDS:
            if re.search(pattern, clean_q, re.IGNORECASE):
                return False
        return True

    def execute_read_query(self, query: str, parameters: Optional[Dict[str, Any]] = None) -> CypherQueryResult:
        if not self._driver:
            self._init_driver()
            if not self._driver:
                return CypherQueryResult(
                    query=query,
                    records=[],
                    error="Database connection is not available. Please verify your NEO4J_URI and credentials.",
                )

        if not self._is_safe_read_query(query):
            return CypherQueryResult(
                query=query,
                records=[],
                error="Query safety violation: Only read queries (MATCH, RETURN) are permitted.",
            )

        start_time = time.perf_counter()
        session_kwargs = {}
        if self.database:
            session_kwargs["database"] = self.database

        try:
            with self._driver.session(**session_kwargs) as session:
                result = session.run(query, parameters or {})
                records = [record.data() for record in result]
                elapsed_ms = (time.perf_counter() - start_time) * 1000.0
                return CypherQueryResult(
                    query=query,
                    records=records,
                    execution_time_ms=round(elapsed_ms, 2),
                )
        except Exception as e:
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            logger.warning("Cypher query execution error: %s | Query: %s", e, query)
            return CypherQueryResult(
                query=query,
                records=[],
                execution_time_ms=round(elapsed_ms, 2),
                error=str(e),
            )

    def get_schema_summary(self) -> Dict[str, Any]:
        return self.get_live_schema()

    def get_live_schema(self) -> Dict[str, Any]:
        """Introspects the actual graph schema from Neo4j at runtime."""
        node_labels: Dict[str, list] = {}
        relationships: Dict[str, str] = {}

        try:
            node_res = self.execute_read_query(
                "CALL db.schema.nodeTypeProperties() "
                "YIELD nodeType, nodeLabels, propertyName "
                "RETURN nodeLabels, propertyName"
            )
            for record in node_res.records:
                labels = record.get("nodeLabels") or []
                prop = record.get("propertyName")
                for label in labels:
                    if label not in node_labels:
                        node_labels[label] = []
                    if prop and prop not in node_labels[label]:
                        node_labels[label].append(prop)
        except Exception as e:
            logger.warning("db.schema.nodeTypeProperties() failed: %s", e)

        try:
            rel_res = self.execute_read_query(
                "CALL db.schema.relTypeProperties() "
                "YIELD relType, propertyName "
                "RETURN relType, propertyName"
            )
            rel_props: Dict[str, list] = {}
            for record in rel_res.records:
                rel_type = (record.get("relType") or "").strip("`").lstrip(":")
                prop = record.get("propertyName")
                if rel_type:
                    if rel_type not in rel_props:
                        rel_props[rel_type] = []
                    if prop and prop not in rel_props[rel_type]:
                        rel_props[rel_type].append(prop)
            for rel_type, props in rel_props.items():
                relationships[rel_type] = f"(:{rel_type}) props: {props}" if props else rel_type
        except Exception as e:
            logger.warning("db.schema.relTypeProperties() failed: %s", e)

        if not node_labels:
            node_labels = {
                "Book": ["isbn", "title", "normalizedTitle", "format", "publisher",
                         "language", "description", "coverImage", "inStock", "textEmbedding"],
                "Author": ["name"],
                "Category": ["name"],
                "Store": ["name", "website", "currency"],
            }
            relationships = {
                "WRITTEN_BY": "(Book)-[:WRITTEN_BY]->(Author)",
                "IN_CATEGORY": "(Book)-[:IN_CATEGORY]->(Category)",
                "HAS_LISTING": "(Book)-[:HAS_LISTING {listingId, price, originalPrice, inStock, url, currency, lastScraped}]->(Store)",
            }

        return {
            "node_labels": node_labels,
            "relationships": relationships,
            "store_names": self.get_store_names(),
            "stats": self.get_stats(),
        }

    def get_store_names(self) -> List[str]:
        query = "MATCH (s:Store) RETURN DISTINCT s.name AS name ORDER BY s.name"
        res = self.execute_read_query(query)
        if res.records:
            return [r["name"] for r in res.records if "name" in r]
        return []

    def get_stats(self) -> Dict[str, int]:
        query = """
        CALL () {
            MATCH (b:Book) RETURN count(b) AS books
        }
        CALL () {
            MATCH (a:Author) RETURN count(a) AS authors
        }
        CALL () {
            MATCH (c:Category) RETURN count(c) AS categories
        }
        CALL () {
            MATCH (s:Store) RETURN count(s) AS stores
        }
        CALL () {
            MATCH ()-[r:HAS_LISTING]->() RETURN count(r) AS listings
        }
        RETURN books, authors, categories, stores, listings
        """
        res = self.execute_read_query(query)
        if res.records and len(res.records) > 0:
            row = res.records[0]
            return {
                "books": row.get("books", 0),
                "authors": row.get("authors", 0),
                "categories": row.get("categories", 0),
                "stores": row.get("stores", 0),
                "listings": row.get("listings", 0),
            }
        return {"books": 0, "authors": 0, "categories": 0, "stores": 0, "listings": 0}

    def health_check(self) -> bool:
        if not self._driver:
            self._init_driver()
        if not self._driver:
            return False
        try:
            with self._driver.session(database=self.database or None) as session:
                res = session.run("RETURN 1 AS alive").single()
                return res and res["alive"] == 1
        except Exception:
            return False

    def close(self):
        if self._driver:
            self._driver.close()

    # ------------------------------------------------------------------
    # Search Operations (Vector, Fulltext, Hybrid)
    # ------------------------------------------------------------------

    def vector_search(
        self,
        query_embedding: List[float],
        top_k: int = 10,
    ) -> CypherQueryResult:
        """Run a vector similarity search using the book_title_embedding index."""
        query = """
        CALL db.index.vector.queryNodes('book_title_embedding', $top_k, $embedding)
        YIELD node AS b, score
        OPTIONAL MATCH (b)-[:WRITTEN_BY]->(a:Author)
        OPTIONAL MATCH (b)-[:IN_CATEGORY]->(c:Category)
        OPTIONAL MATCH (b)-[r:HAS_LISTING]->(s:Store)
        WITH b, score, collect(DISTINCT a.name) AS authors, 
             collect(DISTINCT c.name) AS categories,
             collect(DISTINCT {
               store: s.name,
               price: r.price,
               originalPrice: r.originalPrice,
               currency: r.currency,
               inStock: r.inStock,
               url: r.url
             }) AS listings
        RETURN b.title         AS title,
               b.isbn          AS isbn,
               b.description   AS description,
               b.coverImage    AS coverImage,
               authors         AS authors,
               categories      AS categories,
               listings        AS listings,
               score           AS similarity_score
        ORDER BY score DESC
        """
        return self.execute_read_query(query, {"top_k": top_k, "embedding": query_embedding})

    def fulltext_search(
        self,
        query_text: str,
        top_k: int = 10,
    ) -> CypherQueryResult:
        """Run a Lucene full-text keyword search using the book_fulltext_index."""
        # Sanitize query for Lucene: extract significant words
        words = [
            w for w in re.findall(r"\w+", query_text)
            if len(w) >= 2 and w.lower() not in (
                "the", "a", "an", "of", "in", "for", "to", "is", "at",
                "what", "price", "book", "books", "store", "stores",
                "available", "show", "me", "find", "how", "much", "tell"
            )
        ]
        lucene_query = " ".join(words) if words else query_text

        query = """
        CALL db.index.fulltext.queryNodes('book_fulltext_index', $query_text)
        YIELD node AS b, score
        OPTIONAL MATCH (b)-[:WRITTEN_BY]->(a:Author)
        OPTIONAL MATCH (b)-[:IN_CATEGORY]->(c:Category)
        OPTIONAL MATCH (b)-[r:HAS_LISTING]->(s:Store)
        WITH b, score, collect(DISTINCT a.name) AS authors, 
             collect(DISTINCT c.name) AS categories,
             collect(DISTINCT {
               store: s.name,
               price: r.price,
               originalPrice: r.originalPrice,
               currency: r.currency,
               inStock: r.inStock,
               url: r.url
             }) AS listings
        RETURN b.title         AS title,
               b.isbn          AS isbn,
               b.description   AS description,
               b.coverImage    AS coverImage,
               authors         AS authors,
               categories      AS categories,
               listings        AS listings,
               score           AS fulltext_score
        ORDER BY score DESC
        LIMIT $top_k
        """
        return self.execute_read_query(query, {"query_text": lucene_query, "top_k": top_k})

    def hybrid_search(
        self,
        query_text: str,
        query_embedding: Optional[List[float]] = None,
        top_k: int = 15,
    ) -> List[Dict[str, Any]]:
        """
        Executes a hybrid search combining Vector Similarity Search and Full-Text Keyword Search.
        Merges and deduplicates candidates, aggregating multi-store listings for cross-store price comparison.
        """
        merged_by_key: Dict[str, Dict[str, Any]] = {}

        # 1. Full-text Keyword Search
        try:
            ft_res = self.fulltext_search(query_text=query_text, top_k=top_k)
            if not ft_res.error and ft_res.records:
                for rank, record in enumerate(ft_res.records):
                    key = record.get("isbn") or record.get("title") or f"ft_{rank}"
                    rec_copy = dict(record)
                    rec_copy["_source"] = "keyword"
                    rec_copy["_ft_rank"] = rank + 1
                    merged_by_key[key] = rec_copy
        except Exception as e:
            logger.warning("Fulltext search step failed: %s", e)

        # 2. Semantic Vector Search
        if query_embedding:
            try:
                vec_res = self.vector_search(query_embedding=query_embedding, top_k=top_k)
                if not vec_res.error and vec_res.records:
                    for rank, record in enumerate(vec_res.records):
                        key = record.get("isbn") or record.get("title") or f"vec_{rank}"
                        if key in merged_by_key:
                            merged_by_key[key]["_source"] = "hybrid"
                            merged_by_key[key]["similarity_score"] = record.get("similarity_score")
                            merged_by_key[key]["_vec_rank"] = rank + 1
                        else:
                            rec_copy = dict(record)
                            rec_copy["_source"] = "semantic"
                            rec_copy["_vec_rank"] = rank + 1
                            merged_by_key[key] = rec_copy
            except Exception as e:
                logger.warning("Vector search step failed: %s", e)

        # 3. Fallback Substring Search if both fulltext and vector returned few or 0 results
        if len(merged_by_key) < 2:
            clean_terms = [
                w for w in re.findall(r"\w+", query_text)
                if len(w) >= 3 and w.lower() not in (
                    "what", "price", "book", "books", "store", "stores",
                    "available", "show", "find", "much", "tell"
                )
            ]
            if clean_terms:
                term = clean_terms[0]
                fallback_query = """
                MATCH (b:Book)
                WHERE toLower(b.title) CONTAINS toLower($term)
                   OR toLower(b.normalizedTitle) CONTAINS toLower($term)
                OPTIONAL MATCH (b)-[:WRITTEN_BY]->(a:Author)
                OPTIONAL MATCH (b)-[:IN_CATEGORY]->(c:Category)
                OPTIONAL MATCH (b)-[r:HAS_LISTING]->(s:Store)
                WITH b, collect(DISTINCT a.name) AS authors,
                     collect(DISTINCT c.name) AS categories,
                     collect(DISTINCT {
                       store: s.name,
                       price: r.price,
                       originalPrice: r.originalPrice,
                       currency: r.currency,
                       inStock: r.inStock,
                       url: r.url
                     }) AS listings
                RETURN b.title       AS title,
                       b.isbn        AS isbn,
                       b.description AS description,
                       b.coverImage  AS coverImage,
                       authors       AS authors,
                       categories    AS categories,
                       listings      AS listings
                LIMIT 10
                """
                fb_res = self.execute_read_query(fallback_query, {"term": term})
                if not fb_res.error and fb_res.records:
                    for rank, record in enumerate(fb_res.records):
                        key = record.get("isbn") or record.get("title") or f"fb_{rank}"
                        if key not in merged_by_key:
                            rec_copy = dict(record)
                            rec_copy["_source"] = "substring"
                            merged_by_key[key] = rec_copy

        # Calculate hybrid rank score (RRF: 1 / (60 + rank))
        for key, item in merged_by_key.items():
            ft_rank = item.get("_ft_rank", 999)
            vec_rank = item.get("_vec_rank", 999)
            rrf_score = (1.0 / (60.0 + ft_rank)) + (1.0 / (60.0 + vec_rank))
            item["_rrf_score"] = rrf_score

        # Sort by RRF score descending
        sorted_results = sorted(merged_by_key.values(), key=lambda x: x.get("_rrf_score", 0.0), reverse=True)
        return sorted_results[:top_k]
