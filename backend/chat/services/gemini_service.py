import re
import json
import time
import hashlib
import logging
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional

try:
    from models.chat_message import ChatMessage
except ImportError:
    try:
        from chat.models.chat_message import ChatMessage
    except ImportError:
        from backend.chat.models.chat_message import ChatMessage

try:
    from services.embedding_service import QueryEmbeddingService
except ImportError:
    try:
        from chat.services.embedding_service import QueryEmbeddingService
    except ImportError:
        from backend.chat.services.embedding_service import QueryEmbeddingService

logger = logging.getLogger(__name__)


class ILLMServiceClient(ABC):
    """
    Interface for LLM operations (Text-to-Cypher generation, Response synthesis).
    Allows plugging in Google Gemini, mock LLMs, or other providers.
    """

    @abstractmethod
    def generate_cypher(
        self,
        user_query: str,
        chat_history: List[ChatMessage],
        schema_context: str,
    ) -> str:
        """Translates natural language user question into an accurate read-only Cypher query or 'OUT_OF_SCOPE'."""
        pass

    @abstractmethod
    def synthesize_response_with_suggestions(
        self,
        user_query: str,
        cypher_query: str,
        query_results: List[Dict[str, Any]],
        chat_history: List[ChatMessage],
    ) -> tuple[str, List[str]]:
        """Generates a helpful markdown response and 3 follow-up suggestions in a single call."""
        pass

    @abstractmethod
    def synthesize_response_stream(
        self,
        user_query: str,
        cypher_query: str,
        query_results: List[Dict[str, Any]],
        chat_history: List[ChatMessage],
    ):
        """Yields (token, None) chunks during generation, and finally ('', suggestions_list)."""
        pass

    @abstractmethod
    def synthesize_response(
        self,
        user_query: str,
        cypher_query: str,
        query_results: List[Dict[str, Any]],
        chat_history: List[ChatMessage],
    ) -> str:
        """Generates a helpful, user-friendly markdown response from the graph query results."""
        pass

    @abstractmethod
    def generate_followup_suggestions(
        self,
        user_query: str,
        assistant_response: str,
        query_results: List[Dict[str, Any]],
    ) -> List[str]:
        """Generates relevant follow-up questions for the user to explore further."""
        pass

    @abstractmethod
    def generate_search_cypher(
        self,
        search_type: str,
        schema: Dict[str, Any],
        top_k: int,
        **kwargs,
    ) -> str:
        """
        Generates an index-based search Cypher query (vector or fulltext) from the live schema.

        Args:
            search_type: One of 'vector' or 'fulltext'.
            schema: The live Neo4j schema dict (from get_live_schema).
            top_k: Number of results to return.
            **kwargs: Extra context (e.g., query_text for fulltext, index_name for vector).

        Returns:
            A read-only Cypher query string, or a fallback marker string on failure.
        """
        pass


# ---------------------------------------------------------------------------
# Static portion of the Cypher-generation system prompt.
# This never changes at runtime — only the dynamic schema block (injected
# per call) changes, and only when Neo4j schema actually mutates.
# ---------------------------------------------------------------------------
_CYPHER_SYSTEM_PROMPT_STATIC = """\
You are an expert Neo4j Cypher query generator and domain classifier for a Sri Lankan Bookstore Inventory & Price Comparison Knowledge Graph.

DOMAIN SCOPE:
- IN-SCOPE: Books, authors, genres, publishers, bookstore inventory, prices, availability, store comparisons.
- OUT-OF-SCOPE: Weather, politics, coding/programming, sports, science, recipes — anything unrelated to books or bookstores.

CRITICAL GUARDRAIL:
If the question is OUT-OF-SCOPE, output EXACTLY: OUT_OF_SCOPE

GRAPH SCHEMA:
{schema_block}

RULES (strict):
- Output ONE valid read-only Cypher query (no CREATE/MERGE/DELETE/SET/ALTER).
- Case-insensitive matching: toLower(b.title) CONTAINS toLower('keyword').
- ALWAYS return: b.title, b.isbn, author name(s), category name(s), s.name, r.price, r.originalPrice, r.currency, r.inStock, r.url.
- For cheapest/best-price queries: ORDER BY r.price ASC, WHERE r.price > 0.
- Include LIMIT (default 20).
- Output ONLY the Cypher query in a ```cypher block (or OUT_OF_SCOPE).
"""


class GeminiLLMService(ILLMServiceClient):
    """
    Infrastructure implementation of ILLMServiceClient using Google Gemini API.
    Handles Text-to-Cypher translation, Schema grounding, and Response synthesis.
    """

    def __init__(self, api_key: str, model_name: str, temperature: float = 0.2):
        self.api_key = api_key
        self.model_name = model_name
        self.temperature = temperature
        self._client = None
        self._sdk_type: Optional[str] = None
        # Schema fingerprint cache: rebuilt only when Neo4j schema changes.
        self._schema_fingerprint: Optional[str] = None
        self._cached_cypher_system_prompt: Optional[str] = None
        self._init_client()

    def _init_client(self):
        if not self.api_key:
            logger.warning("Gemini API key is not configured. LLM calls will fail until GEMINI_API_KEY is provided.")
            return

        try:
            from google import genai
            self._client = genai.Client(api_key=self.api_key)
            self._sdk_type = "google-genai"
            logger.info("Initialized Google GenAI client with model %s", self.model_name)
        except Exception as e:
            try:
                import google.generativeai as genai_legacy
                genai_legacy.configure(api_key=self.api_key)
                self._client = genai_legacy.GenerativeModel(self.model_name)
                self._sdk_type = "google-generativeai"
                logger.info("Initialized legacy google.generativeai with model %s", self.model_name)
            except Exception as e2:
                logger.error("Failed to initialize Google GenAI SDK: %s | %s", e, e2)
                self._client = None
                self._sdk_type = None

    def _call_gemini(self, prompt: str, system_instruction: Optional[str] = None, max_retries: int = 3) -> str:
        if not self._client:
            self._init_client()
            if not self._client:
                raise ValueError("Google Gemini API Key is missing. Please set GEMINI_API_KEY or GOOGLE_API_KEY in your .env file.")

        for attempt in range(1, max_retries + 1):
            try:
                if self._sdk_type == "google-genai":
                    from google.genai import types
                    config = types.GenerateContentConfig(
                        temperature=self.temperature,
                        system_instruction=system_instruction,
                    )
                    response = self._client.models.generate_content(
                        model=self.model_name,
                        contents=prompt,
                        config=config,
                    )
                    return response.text.strip() if response.text else ""
                else:
                    full_prompt = f"{system_instruction}\n\n{prompt}" if system_instruction else prompt
                    response = self._client.generate_content(
                        full_prompt,
                        generation_config={"temperature": self.temperature},
                    )
                    return response.text.strip() if response.text else ""
            except Exception as e:
                wait_time = 2 ** attempt
                logger.warning("Gemini invocation attempt %d/%d error: %s. Retrying in %ds...", attempt, max_retries, e, wait_time)
                if attempt < max_retries:
                    time.sleep(wait_time)
                else:
                    logger.error("Gemini API invocation failed after %d attempts: %s", max_retries, e)
                    raise e

    def _call_gemini_stream(self, prompt: str, system_instruction: Optional[str] = None):
        if not self._client:
            self._init_client()
            if not self._client:
                raise ValueError("Google Gemini API Key is missing. Please set GEMINI_API_KEY or GOOGLE_API_KEY in your .env file.")

        if self._sdk_type == "google-genai":
            from google.genai import types
            config = types.GenerateContentConfig(
                temperature=self.temperature,
                system_instruction=system_instruction,
            )
            response = self._client.models.generate_content_stream(
                model=self.model_name,
                contents=prompt,
                config=config,
            )
            for chunk in response:
                if chunk.text:
                    yield chunk.text
        else:
            full_prompt = f"{system_instruction}\n\n{prompt}" if system_instruction else prompt
            response = self._client.generate_content(
                full_prompt,
                stream=True,
                generation_config={"temperature": self.temperature},
            )
            for chunk in response:
                if chunk.text:
                    yield chunk.text

    def _clean_cypher_output(self, raw_output: str) -> str:
        """Strips markdown code fences and extraneous text from the LLM output."""
        cleaned = raw_output.strip()
        if cleaned.upper().startswith("OUT_OF_SCOPE"):
            return "OUT_OF_SCOPE"
        match = re.search(r"```(?:cypher)?\s*([\s\S]*?)\s*```", cleaned, re.IGNORECASE)
        if match:
            cleaned = match.group(1).strip()
        if cleaned.endswith(";"):
            cleaned = cleaned[:-1].strip()
        return cleaned

    # ------------------------------------------------------------------
    # Schema-fingerprint-based system prompt caching
    # ------------------------------------------------------------------

    def _build_cypher_system_prompt(self, schema_context: str) -> str:
        """
        Returns the full Cypher-generation system prompt.

        The expensive static portion is built once and cached.  It is only
        rebuilt when the schema_context string actually changes (detected via
        an MD5 fingerprint), so repeated calls with the same schema pay
        zero string-formatting cost.
        """
        fingerprint = hashlib.md5(schema_context.encode("utf-8")).hexdigest()
        if fingerprint != self._schema_fingerprint or self._cached_cypher_system_prompt is None:
            logger.info(
                "Schema fingerprint changed (%s → %s) — rebuilding Cypher system prompt.",
                self._schema_fingerprint,
                fingerprint,
            )
            self._schema_fingerprint = fingerprint
            self._cached_cypher_system_prompt = _CYPHER_SYSTEM_PROMPT_STATIC.format(
                schema_block=schema_context
            )
        return self._cached_cypher_system_prompt

    def generate_cypher(
        self,
        user_query: str,
        chat_history: List[ChatMessage],
        schema_context: str,
    ) -> str:
        # Use fingerprint-cached system prompt — rebuilt only on schema change.
        system_instruction = self._build_cypher_system_prompt(schema_context)

        history_text = ""
        if chat_history:
            recent = chat_history[-4:]
            history_text = "Recent Conversation History:\n" + "\n".join(
                [f"{msg.role}: {msg.content}" for msg in recent]
            ) + "\n\n"

        prompt = (
            f"{history_text}"
            f"User Question: {user_query}\n\n"
            "Generate the Cypher query, or return OUT_OF_SCOPE if unrelated to books/bookstores."
        )
        raw_response = self._call_gemini(prompt, system_instruction=system_instruction)
        return self._clean_cypher_output(raw_response)

    def synthesize_response_with_suggestions(
        self,
        user_query: str,
        cypher_query: str,
        query_results: List[Dict[str, Any]],
        chat_history: List[ChatMessage],
    ) -> tuple[str, List[str]]:
        """
        Synthesize natural-language answer and follow-up suggestions in a single LLM invocation.
        Saves 10-15 seconds per request.
        """
        system_instruction = """
You are a friendly, intelligent, and knowledgeable Book Inventory & Price Comparison Assistant for Sri Lankan Bookstores.
Your goal is to answer the user's question clearly and accurately using the Neo4j database results provided.

CRITICAL GUIDELINES:
1. When a user asks for the price or availability of a specific book or genre (e.g. "A Good Girl's Guide to Murder", "thriller books"):
   - Extract and list ALL available listings and prices across all bookstores present in the results.
   - Present a clean Markdown price comparison table:
     | Store Name | Price (LKR) | Status |
     | :--- | :--- | :--- |
     | Store A | LKR 1,896.00 | In Stock |
     | Store B | LKR 2,800.00 | In Stock |
   - Highlight the **cheapest option** (e.g. "🏆 **Best Deal:** Store A offers the lowest price at LKR 1,896.00, saving you LKR 904.00 compared to Store B.").
   - Mention the book author, genre, or format if known.

2. Structure:
   - Provide a clear, direct natural-language summary.
   - Use Markdown tables for comparisons.
   - NEVER include any URLs, hyperlinks, or web addresses in your response. Do NOT use markdown link syntax [text](url) under any circumstances.
   - Format prices clearly (e.g., `LKR 1,896.00` or `Rs. 1,896`).

3. Accuracy:
   - ONLY cite information present in the database results.
   - If no records were found, politely state that no matching books or stores were found in the current inventory, and suggest alternative titles or categories.

4. Follow-up Suggestions:
   - At the very end of your response, output exactly three follow-up questions for the user, enclosed in a JSON block at the bottom:
     ```json_suggestions
     ["Suggestion 1", "Suggestion 2", "Suggestion 3"]
     ```
"""

        prompt = f"""
User Question: {user_query}

Executed Cypher / Search Context:
```cypher
{cypher_query}
```

Database Query Results ({len(query_results)} records found):
{json.dumps(query_results[:40], default=str, indent=2)}

Please synthesize an engaging, well-formatted markdown response with comparison table, followed by ```json_suggestions block.
"""
        raw_output = self._call_gemini(prompt, system_instruction=system_instruction)

        # Parse suggestions from raw_output
        suggestions = []
        answer = raw_output
        match = re.search(r"```json_suggestions\s*([\s\S]*?)\s*```", raw_output, re.IGNORECASE)
        if match:
            try:
                parsed = json.loads(match.group(1).strip())
                if isinstance(parsed, list):
                    suggestions = [str(s).strip() for s in parsed if s][:4]
                answer = raw_output[:match.start()].strip()
            except Exception as e:
                logger.debug("Failed to parse json_suggestions: %s", e)

        if not suggestions:
            # Fallback default suggestions based on context
            suggestions = [
                "Which store has the lowest prices?",
                "Compare prices for Atomic Habits",
                "Show available thriller books",
            ]

        return answer, suggestions

    def synthesize_response_stream(
        self,
        user_query: str,
        cypher_query: str,
        query_results: List[Dict[str, Any]],
        chat_history: List[ChatMessage],
    ):
        """
        Stream response tokens in real-time, cleanly extracting suggestions at the end.
        Yields (token_text, None) during generation, and ('', suggestions_list) at completion.
        """
        system_instruction = """
You are a friendly, intelligent, and knowledgeable Book Inventory & Price Comparison Assistant for Sri Lankan Bookstores.
Your goal is to answer the user's question clearly and accurately using the Neo4j database results provided.

CRITICAL GUIDELINES:
1. When a user asks for the price or availability of a specific book or genre (e.g. "A Good Girl's Guide to Murder", "thriller books"):
   - Extract and list ALL available listings and prices across all bookstores present in the results.
   - Present a clean Markdown price comparison table:
     | Store Name | Price (LKR) | Status |
     | :--- | :--- | :--- |
     | Store A | LKR 1,896.00 | In Stock |
     | Store B | LKR 2,800.00 | In Stock |
   - Highlight the **cheapest option** (e.g. "🏆 **Best Deal:** Store A offers the lowest price at LKR 1,896.00, saving you LKR 904.00 compared to Store B.").
   - Mention the book author, genre, or format if known.

2. Structure:
   - Provide a clear, direct natural-language summary.
   - Use Markdown tables for comparisons.
   - NEVER include any URLs, hyperlinks, or web addresses in your response. Do NOT use markdown link syntax [text](url) under any circumstances.
   - Format prices clearly (e.g., `LKR 1,896.00` or `Rs. 1,896`).

3. Accuracy:
   - ONLY cite information present in the database results.
   - If no records were found, politely state that no matching books or stores were found in the current inventory, and suggest alternative titles or categories.

4. Follow-up Suggestions:
   - At the very end of your response, output exactly three follow-up questions for the user, enclosed in a JSON block at the bottom:
     ```json_suggestions
     ["Suggestion 1", "Suggestion 2", "Suggestion 3"]
     ```
"""

        prompt = f"""
User Question: {user_query}

Executed Cypher / Search Context:
```cypher
{cypher_query}
```

Database Query Results ({len(query_results)} records found):
{json.dumps(query_results[:40], default=str, indent=2)}

Please synthesize an engaging, well-formatted markdown response with comparison table, followed by ```json_suggestions block.
"""
        emitted_len = 0
        full_buffer = ""
        marker = "```json_suggestions"
        marker_alt = "```json"
        in_marker = False

        for chunk in self._call_gemini_stream(prompt, system_instruction=system_instruction):
            full_buffer += chunk
            if in_marker:
                continue

            idx = full_buffer.find(marker)
            if idx == -1:
                idx_alt = full_buffer.find(marker_alt)
                if idx_alt != -1 and "[" in full_buffer[idx_alt:]:
                    idx = idx_alt

            if idx != -1:
                in_marker = True
                to_emit = full_buffer[emitted_len:idx].rstrip()
                if to_emit:
                    yield (to_emit, None)
                    emitted_len = idx
            else:
                # Safety hold-back if buffer ends with prefix of marker
                safe_len = len(full_buffer)
                for i in range(1, len(marker) + 1):
                    if i <= len(full_buffer):
                        suffix = full_buffer[-i:]
                        if marker.startswith(suffix) or marker_alt.startswith(suffix):
                            safe_len = len(full_buffer) - i
                            break
                if safe_len > emitted_len:
                    yield (full_buffer[emitted_len:safe_len], None)
                    emitted_len = safe_len

        if not in_marker and emitted_len < len(full_buffer):
            yield (full_buffer[emitted_len:], None)

        # Parse suggestions from full_buffer
        suggestions = []
        match = re.search(r"```(?:json_suggestions|json)?\s*(\[[\s\S]*?\])\s*```", full_buffer, re.IGNORECASE)
        if match:
            try:
                parsed = json.loads(match.group(1).strip())
                if isinstance(parsed, list):
                    suggestions = [str(s).strip() for s in parsed if s][:4]
            except Exception as e:
                logger.debug("Failed to parse json_suggestions from stream: %s", e)

        if not suggestions:
            suggestions = [
                "Which store has the lowest prices?",
                "Compare prices for Atomic Habits",
                "Show available thriller books",
            ]

        yield ("", suggestions)

    def synthesize_response(
        self,
        user_query: str,
        cypher_query: str,
        query_results: List[Dict[str, Any]],
        chat_history: List[ChatMessage],
    ) -> str:
        answer, _ = self.synthesize_response_with_suggestions(
            user_query=user_query,
            cypher_query=cypher_query,
            query_results=query_results,
            chat_history=chat_history,
        )
        return answer

    def generate_followup_suggestions(
        self,
        user_query: str,
        assistant_response: str,
        query_results: List[Dict[str, Any]],
    ) -> List[str]:
        return [
            "Which store has the lowest prices?",
            "What books are available across the bookstores?",
            "Compare prices for Atomic Habits",
            "Show available thriller books",
        ]

    def generate_search_cypher(
        self,
        search_type: str,
        schema: Dict[str, Any],
        top_k: int,
        **kwargs,
    ) -> str:
        """
        Generates a Neo4j index-based search Cypher query (vector or fulltext)
        by passing the live schema to the LLM.

        Falls back to a safe default template if LLM is unavailable or returns invalid output.
        """
        node_labels = schema.get("node_labels", {})
        relationships = schema.get("relationships", {})
        store_names = schema.get("store_names", [])

        # Build a compact schema description for the LLM
        node_lines = [f"  - (:{label}): properties {props}" for label, props in node_labels.items()]
        rel_lines = [f"  - {pattern}" for pattern in relationships.values()]
        schema_text = (
            "Node Labels:\n" + "\n".join(node_lines) + "\n"
            + "Relationships:\n" + "\n".join(rel_lines) + "\n"
            + f"Indexed Stores: {', '.join(store_names)}\n"
        )

        if search_type == "vector":
            index_name = kwargs.get("index_name", "book_title_embedding")
            system_instruction = f"""\
You are a Neo4j Cypher expert. Generate a single read-only Cypher query that performs a vector similarity search.

RULES:
- Use CALL db.index.vector.queryNodes('{index_name}', $top_k, $embedding) YIELD node AS b, score
- After the CALL, use OPTIONAL MATCH to join Author, Category, and Store/Listing nodes.
- Return: b.title AS title, b.isbn AS isbn, b.description AS description, b.coverImage AS coverImage,
  collect(DISTINCT a.name) AS authors, collect(DISTINCT c.name) AS categories,
  collect(DISTINCT {{store: s.name, price: r.price, originalPrice: r.originalPrice, currency: r.currency, inStock: r.inStock, url: r.url}}) AS listings,
  score AS similarity_score
- ORDER BY score DESC
- Only output the raw Cypher query inside a ```cypher block, nothing else.
- Do NOT use CREATE, MERGE, DELETE, SET, ALTER, or LIMIT (top_k is handled by the vector index call).

GRAPH SCHEMA:
{schema_text}
"""
            prompt = (
                f"Generate the vector search Cypher query using the '{index_name}' index "
                f"with parameters $embedding (the query vector) and $top_k (integer={top_k})."
            )
        elif search_type == "fulltext":
            index_name = kwargs.get("index_name", "book_fulltext_index")
            system_instruction = f"""\
You are a Neo4j Cypher expert. Generate a single read-only Cypher query that performs a full-text keyword search.

RULES:
- Use CALL db.index.fulltext.queryNodes('{index_name}', $query_text) YIELD node AS b, score
- After the CALL, use OPTIONAL MATCH to join Author, Category, and Store/Listing nodes.
- Return: b.title AS title, b.isbn AS isbn, b.description AS description, b.coverImage AS coverImage,
  collect(DISTINCT a.name) AS authors, collect(DISTINCT c.name) AS categories,
  collect(DISTINCT {{store: s.name, price: r.price, originalPrice: r.originalPrice, currency: r.currency, inStock: r.inStock, url: r.url}}) AS listings,
  score AS fulltext_score
- ORDER BY score DESC
- Add LIMIT $top_k at the very end.
- Only output the raw Cypher query inside a ```cypher block, nothing else.
- Do NOT use CREATE, MERGE, DELETE, SET, or ALTER.

GRAPH SCHEMA:
{schema_text}
"""
            prompt = (
                f"Generate the fulltext search Cypher query using the '{index_name}' index "
                f"with parameters $query_text (the Lucene search string) and $top_k (integer={top_k})."
            )
        else:
            raise ValueError(f"Unknown search_type '{search_type}'. Must be 'vector' or 'fulltext'.")

        try:
            raw = self._call_gemini(prompt, system_instruction=system_instruction, max_retries=2)
            return self._clean_cypher_output(raw)
        except Exception as e:
            logger.warning("generate_search_cypher(%s) LLM call failed: %s", search_type, e)
            return ""  # Caller will use fallback

    def generate_title(self, messages: List[Dict[str, str]]) -> str:
        """
        Generate a short 3-5 word conversation title based on both the question and answer generated.
        Uses a fast, lightweight Gemini call.
        """
        if not messages:
            return "New Conversation"

        user_q = ""
        asst_a = ""
        for m in messages[:3]:
            role = m.get("role", "user")
            content = m.get("content", "").strip()
            if role == "user" and not user_q:
                user_q = content
            elif role == "assistant" and not asst_a:
                asst_a = content

        prompt = (
            f"Generate a concise, high-quality 3 to 5 word conversation title capturing the specific topic "
            f"based on BOTH the user's question and the AI assistant's response.\n\n"
            f"User Question: {user_q[:300]}\n"
            f"AI Response: {asst_a[:300]}\n\n"
            f"Requirements:\n"
            f"- 3 to 5 words max\n"
            f"- Specific to topic, author, genre, or query (e.g. 'Stephen King Books Under $20', 'Harry Potter Pricing', 'Python Machine Learning Guides')\n"
            f"- Output ONLY the title text — no quotes, no markdown, no period at end."
        )
        try:
            raw = self._call_gemini(prompt, system_instruction=None, max_retries=2)
            title = raw.strip().strip('"').strip("'").rstrip(".")
            if len(title) > 60:
                title = title[:57] + "..."
            return title or "New Conversation"
        except Exception as e:
            logger.warning("generate_title failed: %s", e)
            return "New Conversation"


