import time
import json
import re
import logging
import concurrent.futures
from typing import List, Dict, Any, Optional

try:
    from models.chat_message import ChatMessage
    from models.chat_response import ChatResponse
    from models.cypher_query_result import CypherQueryResult
except ImportError:
    try:
        from chat.models.chat_message import ChatMessage
        from chat.models.chat_response import ChatResponse
        from chat.models.cypher_query_result import CypherQueryResult
    except ImportError:
        from backend.chat.models.chat_message import ChatMessage
        from backend.chat.models.chat_response import ChatResponse
        from backend.chat.models.cypher_query_result import CypherQueryResult

try:
    from repositories.neo4j_reader import IGraphDatabaseReader
    from repositories.neo4j_search import Neo4jSearchService
    from services.gemini_service import ILLMServiceClient
except ImportError:
    try:
        from chat.repositories.neo4j_reader import IGraphDatabaseReader
        from chat.repositories.neo4j_search import Neo4jSearchService
        from chat.services.gemini_service import ILLMServiceClient
    except ImportError:
        from backend.chat.repositories.neo4j_reader import IGraphDatabaseReader
        from backend.chat.repositories.neo4j_search import Neo4jSearchService
        from backend.chat.services.gemini_service import ILLMServiceClient

logger = logging.getLogger(__name__)

_SCHEMA_CACHE_TTL_SECONDS = 3600


def _normalize_title_key(title: str) -> str:
    """Helper to produce a clean alphanumeric key for grouping titles across stores."""
    if not title:
        return ""
    # Remove apostrophes, punctuation, lowercase
    cleaned = re.sub(r"[^\w\s]", "", title.lower())
    return " ".join(cleaned.split())


class ChatQueryService:
    """
    Use case orchestrator for natural language book inventory queries.

    Implements a multi-phase search strategy:
      1. Hybrid Search (Semantic Vector + Full-text Keyword Search) against Neo4j.
      2. Structured Cypher query generation via LLM for analytical/filtered queries.
      3. Cross-store consolidation & deduplication of store listings.
      4. Natural-language response synthesis with multi-store price comparisons.
    """

    def __init__(
        self,
        db_reader: IGraphDatabaseReader,
        llm_service: ILLMServiceClient,
        embedding_service=None,
        semantic_top_k: int = 15,
        search_service: Optional["Neo4jSearchService"] = None,
    ):
        self.db_reader = db_reader
        self.llm_service = llm_service
        self.embedding_service = embedding_service
        self.semantic_top_k = semantic_top_k
        # Use the injected search service, or fall back to creating one inline
        self.search_service: Neo4jSearchService = search_service or Neo4jSearchService(
            db_reader=db_reader, llm_service=llm_service
        )

        self._cached_schema: Optional[str] = None
        self._schema_cached_at: float = 0.0

    # ------------------------------------------------------------------
    # Schema context
    # ------------------------------------------------------------------

    def _get_schema_context(self) -> str:
        """Returns a live schema context string, refreshed at most once per hour."""
        now = time.time()
        if self._cached_schema and (now - self._schema_cached_at) < _SCHEMA_CACHE_TTL_SECONDS:
            return self._cached_schema

        try:
            schema = self.db_reader.get_schema_summary()
            stores = schema.get("store_names", [])
            stats = schema.get("stats", {})
            node_labels = schema.get("node_labels", {})
            relationships = schema.get("relationships", {})

            node_summary_lines = []
            for label, props in node_labels.items():
                node_summary_lines.append(f" - {label}: {', '.join(props)}")

            rel_summary_lines = []
            for rel, pattern in relationships.items():
                rel_summary_lines.append(f" - {pattern}")

            schema_text = (
                f"Indexed Stores in Database: {', '.join(stores)}\n"
                f"Graph Stats: {stats.get('books', 0)} Books, "
                f"{stats.get('listings', 0)} Listings across "
                f"{stats.get('stores', 0)} Stores.\n"
                "Node Properties (live from Neo4j):\n"
                + "\n".join(node_summary_lines) + "\n"
                + "Relationship Patterns:\n"
                + "\n".join(rel_summary_lines)
            )

            self._cached_schema = schema_text
            self._schema_cached_at = now
        except Exception as e:
            logger.warning("Failed to load live schema context from db: %s", e)
            if not self._cached_schema:
                self._cached_schema = "Standard Bookstore Graph Schema."
        return self._cached_schema

    # ------------------------------------------------------------------
    # Hybrid search phase
    # ------------------------------------------------------------------

    def _hybrid_search(self, user_query: str) -> List[Dict[str, Any]]:
        """
        Embeds user_query (if embedding service available) and calls Neo4jSearchService.hybrid_search
        (combining keyword Lucene fulltext search + cosine vector search with LLM-generated Cypher).
        """
        query_vector = None
        if self.embedding_service and self.embedding_service.is_available:
            query_vector = self.embedding_service.embed_query(user_query)

        try:
            records = self.search_service.hybrid_search(
                query_text=user_query,
                query_embedding=query_vector,
                top_k=self.semantic_top_k,
            )
            logger.info("Hybrid search returned %d record(s) for query: %.60s", len(records), user_query)
            return records
        except Exception as e:
            logger.warning("Hybrid search execution error: %s", e)
            return []

    # ------------------------------------------------------------------
    # Result consolidation across stores
    # ------------------------------------------------------------------

    @staticmethod
    def _consolidate_and_merge_results(
        hybrid_records: List[Dict[str, Any]],
        cypher_records: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        Merge hybrid and Cypher results, consolidating store listings by normalized title and ISBN.
        """
        consolidated: Dict[str, Dict[str, Any]] = {}

        def add_record(record: Dict[str, Any], default_source: str):
            title = record.get("title") or ""
            isbn = record.get("isbn") or ""
            norm_key = _normalize_title_key(title) or isbn
            if not norm_key:
                return

            if norm_key not in consolidated:
                rec_copy = dict(record)
                rec_copy["_source"] = record.get("_source", default_source)
                # Ensure listings is a list of dicts
                existing_listings = rec_copy.get("listings")
                if not isinstance(existing_listings, list):
                    if "store" in rec_copy or "price" in rec_copy:
                        existing_listings = [{
                            "store": rec_copy.get("store") or rec_copy.get("store_name"),
                            "price": rec_copy.get("price"),
                            "originalPrice": rec_copy.get("originalPrice") or rec_copy.get("original_price"),
                            "currency": rec_copy.get("currency", "LKR"),
                            "inStock": rec_copy.get("inStock") or rec_copy.get("in_stock", True),
                            "url": rec_copy.get("url"),
                        }]
                    else:
                        existing_listings = []
                rec_copy["listings"] = existing_listings
                consolidated[norm_key] = rec_copy
            else:
                # Merge listings into the existing book entry
                target = consolidated[norm_key]
                existing_stores = {
                    l.get("store") for l in target.get("listings", []) if isinstance(l, dict) and l.get("store")
                }

                new_listings = record.get("listings")
                if isinstance(new_listings, list):
                    for l in new_listings:
                        if isinstance(l, dict) and l.get("store") and l.get("store") not in existing_stores:
                            target["listings"].append(l)
                            existing_stores.add(l.get("store"))
                elif "store" in record or "price" in record:
                    st_name = record.get("store") or record.get("store_name")
                    if st_name and st_name not in existing_stores:
                        target["listings"].append({
                            "store": st_name,
                            "price": record.get("price"),
                            "originalPrice": record.get("originalPrice") or record.get("original_price"),
                            "currency": record.get("currency", "LKR"),
                            "inStock": record.get("inStock") or record.get("in_stock", True),
                            "url": record.get("url"),
                        })
                        existing_stores.add(st_name)

        for r in hybrid_records:
            add_record(r, default_source="hybrid")

        for r in cypher_records:
            add_record(r, default_source="cypher")

        return list(consolidated.values())

    # ------------------------------------------------------------------
    # Parallel retrieval phase
    # ------------------------------------------------------------------

    def _retrieve_records_parallel(
        self,
        user_query: str,
        history: List[ChatMessage],
        schema_context: str,
    ) -> tuple[bool, str, List[Dict[str, Any]], List[str]]:
        """
        Executes Cypher LLM generation and Hybrid Search concurrently in a thread pool.
        Returns (is_out_of_scope, cypher_query, merged_records, sources).
        """
        cypher_query = ""
        cypher_records: List[Dict[str, Any]] = []
        hybrid_records: List[Dict[str, Any]] = []

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            future_cypher = executor.submit(
                self.llm_service.generate_cypher,
                user_query=user_query,
                chat_history=history,
                schema_context=schema_context,
            )
            future_hybrid = executor.submit(
                self._hybrid_search,
                user_query=user_query,
            )

            # Wait for hybrid search
            try:
                hybrid_records = future_hybrid.result()
            except Exception as e:
                logger.warning("Hybrid search in parallel thread failed: %s", e)
                hybrid_records = []

            # Wait for Cypher generation
            try:
                cypher_query = future_cypher.result()
            except Exception as e:
                logger.error("LLM Cypher generation in parallel thread failed: %s", e)
                cypher_query = ""

        if cypher_query and cypher_query.strip().upper() == "OUT_OF_SCOPE":
            logger.info("Query classified as OUT_OF_SCOPE: %s", user_query)
            return True, "OUT_OF_SCOPE", [], []

        if cypher_query and cypher_query.strip().upper() != "OUT_OF_SCOPE":
            logger.info("Generated Cypher query: %s", cypher_query)
            try:
                query_result: CypherQueryResult = self.db_reader.execute_read_query(cypher_query)
                if query_result.error:
                    # Self-repair disabled: avoid a costly second LLM call.
                    # Hybrid search results (already fetched in parallel) are used as fallback.
                    logger.warning(
                        "Cypher query failed — falling back to hybrid-only results. Error: %s",
                        query_result.error,
                    )
                    query_result = type(query_result)(query=cypher_query, records=[], error=None)

                if not query_result.error and query_result.records:
                    cypher_records = query_result.records
            except Exception as e:
                logger.error("Cypher execution failed: %s", e)

        # Merge & consolidate results across stores
        merged_records = self._consolidate_and_merge_results(hybrid_records, cypher_records)[:self.semantic_top_k]
        sources = [r.get("_source", "hybrid") for r in merged_records]
        return False, cypher_query, merged_records, sources

    @staticmethod
    def _compact_records_for_prompt(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Trims verbose descriptions and raw vectors from book records to minimize synthesis prompt token count."""
        compacted = []
        for r in records:
            entry = {
                "title": r.get("title"),
                "isbn": r.get("isbn"),
                "authors": r.get("authors"),
                "categories": r.get("categories"),
            }
            listings = r.get("listings")
            if isinstance(listings, list):
                entry["listings"] = [
                    {
                        "store": l.get("store"),
                        "price": l.get("price"),
                        "originalPrice": l.get("originalPrice"),
                        "currency": l.get("currency", "LKR"),
                        "inStock": l.get("inStock", True),
                    }
                    for l in listings if isinstance(l, dict)
                ]
            elif "store" in r or "price" in r:
                entry["listings"] = [{
                    "store": r.get("store") or r.get("store_name"),
                    "price": r.get("price"),
                    "originalPrice": r.get("originalPrice") or r.get("original_price"),
                    "currency": r.get("currency", "LKR"),
                    "inStock": r.get("inStock") or r.get("in_stock", True),
                }]
            compacted.append(entry)
        return compacted

    # ------------------------------------------------------------------
    # Main execute method (synchronous)
    # ------------------------------------------------------------------

    def execute(
        self,
        user_query: str,
        chat_history: Optional[List[ChatMessage]] = None,
    ) -> ChatResponse:
        start_time = time.perf_counter()
        history = chat_history or []

        # 1. Obtain live schema context (cached, re-fetched hourly)
        schema_context = self._get_schema_context()

        # 2. Parallel retrieval: Cypher generation + Hybrid search
        t_retrieval_start = time.perf_counter()
        is_out_of_scope, cypher_query, merged_records, sources = self._retrieve_records_parallel(
            user_query=user_query,
            history=history,
            schema_context=schema_context,
        )
        retrieval_ms = (time.perf_counter() - t_retrieval_start) * 1000.0

        # If detected out of scope, return fast out-of-scope response
        if is_out_of_scope:
            total_ms = (time.perf_counter() - start_time) * 1000.0
            out_of_scope_msg = (
                "⚠️ **Out of Scope Query**\n\n"
                "I am an AI assistant specialized exclusively in **Sri Lankan Bookstore Inventory, Book Pricing, Availability, and Authors** "
                "(covering multiple Sri Lankan bookstores).\n\n"
                "Your question is outside the scope of book inventory and literature search. "
                "Please ask about book titles, authors, genres, store price comparisons, or book availability in Sri Lanka!"
            )
            return ChatResponse(
                answer=out_of_scope_msg,
                followup_suggestions=[
                    "Which store has the cheapest thriller books?",
                    "Compare prices for Atomic Habits across stores",
                    "What books are available across the bookstores?",
                    "Books by Colleen Hoover under 3000 LKR",
                ],
                execution_time_ms=round(total_ms, 2),
                latency_breakdown={
                    "retrieval_ms": round(retrieval_ms, 2),
                    "synthesis_ms": 0.0,
                    "total_ms": round(total_ms, 2),
                },
                error=None,
                query_used="OUT_OF_SCOPE (Domain Guardrail)",
                sources=[],
            )

        logger.info(
            "Consolidated %d book record(s) for response synthesis.",
            len(merged_records),
        )

        # 3. Compact records for synthesis prompt token efficiency
        compact_records = self._compact_records_for_prompt(merged_records)

        # 4. Synthesize natural language response & suggestions in one LLM call
        t_synth_start = time.perf_counter()
        suggestions = []
        try:
            if hasattr(self.llm_service, "synthesize_response_with_suggestions"):
                answer, suggestions = self.llm_service.synthesize_response_with_suggestions(
                    user_query=user_query,
                    cypher_query=cypher_query or "Hybrid Search (Vector + Full-text Keyword)",
                    query_results=compact_records,
                    chat_history=history,
                )
            else:
                answer = self.llm_service.synthesize_response(
                    user_query=user_query,
                    cypher_query=cypher_query or "Hybrid Search (Vector + Full-text Keyword)",
                    query_results=compact_records,
                    chat_history=history,
                )
                suggestions = self.llm_service.generate_followup_suggestions(
                    user_query=user_query,
                    assistant_response=answer,
                    query_results=compact_records,
                )
        except Exception as e:
            logger.error("Response synthesis failed: %s", e)
            answer = (
                f"Found {len(merged_records)} record(s) in the database, "
                f"but encountered an error during response synthesis: {str(e)}"
            )
            suggestions = [
                "Which store has the lowest prices?",
                "Compare prices for Atomic Habits",
                "Show available thriller books",
            ]

        synthesis_ms = (time.perf_counter() - t_synth_start) * 1000.0
        total_ms = (time.perf_counter() - start_time) * 1000.0

        breakdown = {
            "retrieval_ms": round(retrieval_ms, 2),
            "synthesis_ms": round(synthesis_ms, 2),
            "total_ms": round(total_ms, 2),
        }

        logger.info(
            "Query completed in %.2fms (Retrieval: %.2fms, Synthesis: %.2fms)",
            total_ms, retrieval_ms, synthesis_ms,
        )

        return ChatResponse(
            answer=answer,
            followup_suggestions=suggestions,
            execution_time_ms=round(total_ms, 2),
            latency_breakdown=breakdown,
            error=None,
            query_used=cypher_query or "Hybrid Search (Vector + Full-text Keyword)",
            sources=sources,
        )

    # ------------------------------------------------------------------
    # Streaming execute method
    # ------------------------------------------------------------------

    def execute_stream(
        self,
        user_query: str,
        chat_history: Optional[List[ChatMessage]] = None,
    ):
        """
        Stream response tokens and events (NDJSON compatible).
        Yields dicts with types:
          - {"type": "start", "cypher_query": ..., "sources": ..., "records_count": ...}
          - {"type": "token", "content": ...}
          - {"type": "done", "suggestions": ..., "execution_time_ms": ..., "latency_breakdown": ..., "sources": ...}
        """
        start_time = time.perf_counter()
        history = chat_history or []
        schema_context = self._get_schema_context()

        # Step 1: Searching inventories and vector DB
        yield {
            "type": "status",
            "stage": "searching",
            "message": "Searching bookstore inventories & vector index...",
        }

        # Concurrent retrieval: Cypher generation + Hybrid search
        t_retrieval_start = time.perf_counter()
        is_out_of_scope, cypher_query, merged_records, sources = self._retrieve_records_parallel(
            user_query=user_query,
            history=history,
            schema_context=schema_context,
        )
        retrieval_ms = (time.perf_counter() - t_retrieval_start) * 1000.0

        if is_out_of_scope:
            total_ms = (time.perf_counter() - start_time) * 1000.0
            out_of_scope_msg = (
                "⚠️ **Out of Scope Query**\n\n"
                "I am an AI assistant specialized exclusively in **Sri Lankan Bookstore Inventory, Book Pricing, Availability, and Authors** "
                "(covering multiple Sri Lankan bookstores).\n\n"
                "Your question is outside the scope of book inventory and literature search. "
                "Please ask about book titles, authors, genres, store price comparisons, or book availability in Sri Lanka!"
            )
            yield {
                "type": "start",
                "cypher_query": "OUT_OF_SCOPE (Domain Guardrail)",
                "sources": [],
                "records_count": 0,
            }
            yield {"type": "token", "content": out_of_scope_msg}
            yield {
                "type": "done",
                "suggestions": [
                    "Which store has the cheapest thriller books?",
                    "Compare prices for Atomic Habits across stores",
                    "What books are available across the bookstores?",
                    "Books by Colleen Hoover under 3000 LKR",
                ],
                "execution_time_ms": round(total_ms, 2),
                "latency_breakdown": {
                    "retrieval_ms": round(retrieval_ms, 2),
                    "synthesis_ms": 0.0,
                    "total_ms": round(total_ms, 2),
                },
                "sources": [],
                "cypher_query": "OUT_OF_SCOPE",
            }
            return

        # Step 2: Analyzing / Thinking status
        if merged_records:
            yield {
                "type": "status",
                "stage": "analyzing",
                "message": f"Comparing {len(merged_records)} matching books across stores...",
            }
        else:
            yield {
                "type": "status",
                "stage": "thinking",
                "message": "Formulating best response...",
            }

        yield {
            "type": "start",
            "cypher_query": cypher_query or "Hybrid Search (Vector + Full-text Keyword)",
            "sources": sources,
            "records_count": len(merged_records),
        }

        # Stream response synthesis with compacted records
        compact_records = self._compact_records_for_prompt(merged_records)
        t_synth_start = time.perf_counter()

        try:
            stream_gen = self.llm_service.synthesize_response_stream(
                user_query=user_query,
                cypher_query=cypher_query or "Hybrid Search (Vector + Full-text Keyword)",
                query_results=compact_records,
                chat_history=history,
            )
            final_suggestions = []
            for token, suggestions in stream_gen:
                if token:
                    yield {"type": "token", "content": token}
                if suggestions:
                    final_suggestions = suggestions

            synthesis_ms = (time.perf_counter() - t_synth_start) * 1000.0
            total_ms = (time.perf_counter() - start_time) * 1000.0
            breakdown = {
                "retrieval_ms": round(retrieval_ms, 2),
                "synthesis_ms": round(synthesis_ms, 2),
                "total_ms": round(total_ms, 2),
            }

            yield {
                "type": "done",
                "suggestions": final_suggestions,
                "execution_time_ms": round(total_ms, 2),
                "latency_breakdown": breakdown,
                "sources": sources,
                "cypher_query": cypher_query or "Hybrid Search (Vector + Full-text Keyword)",
            }
        except Exception as e:
            logger.error("Response synthesis stream error: %s", e)
            err_msg = f"Found {len(merged_records)} record(s) in the database, but encountered an error during streaming: {str(e)}"
            yield {"type": "token", "content": err_msg}
            synthesis_ms = (time.perf_counter() - t_synth_start) * 1000.0
            total_ms = (time.perf_counter() - start_time) * 1000.0
            breakdown = {
                "retrieval_ms": round(retrieval_ms, 2),
                "synthesis_ms": round(synthesis_ms, 2),
                "total_ms": round(total_ms, 2),
            }
            yield {
                "type": "done",
                "suggestions": [
                    "Which store has the lowest prices?",
                    "Compare prices for Atomic Habits",
                    "Show available thriller books",
                ],
                "execution_time_ms": round(total_ms, 2),
                "latency_breakdown": breakdown,
                "sources": sources,
                "cypher_query": cypher_query or "Hybrid Search",
            }
