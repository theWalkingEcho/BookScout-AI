import re
import json
import time
import random
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


def sanitize_authors(authors: Any) -> List[str]:
    """
    Cleans raw author strings/lists by removing technical specs (Format, Publisher, ISBN, Dimensions, Weight),
    placeholder text ('(s) n/a', 'Nill', 'Unknown', 'N/A'), and invalid tokens.
    Returns a clean list of valid author names.
    """
    if not authors:
        return []

    if isinstance(authors, str):
        candidates = [authors]
    elif isinstance(authors, (list, tuple, set)):
        candidates = [str(a) for a in authors if a]
    else:
        return []

    clean_authors = []
    spec_pattern = re.compile(
        r'\b(?:Format|Publisher|ISBN|ISBN-13|Dimensions|Weight|Page count|Binding)\b.*',
        re.IGNORECASE
    )
    invalid_terms = {"n/a", "(s) n/a", "unknown", "nill", "nil", "none", "null", "author", "n/a format publisher nill"}

    for candidate in candidates:
        # Cut off any specs metadata part
        cleaned = spec_pattern.split(candidate)[0].strip()
        # Clean leading labels like Author:, By:, (s), etc.
        cleaned = re.sub(r'^\s*(?:Author|By|\(s\))\s*[:\-–—]?\s*', '', cleaned, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r'^\(s\)\s*', '', cleaned, flags=re.IGNORECASE).strip()

        # Split by comma if "Unknown, E. Nesbit, Holly Jackson"
        parts = [p.strip() for p in cleaned.split(',') if p.strip()]
        for p in parts:
            p_lower = p.lower()
            if p_lower not in invalid_terms and not p_lower.startswith("(s)") and len(p) >= 2:
                if p not in clean_authors:
                    clean_authors.append(p)

    return clean_authors



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
- ALWAYS return: b.title, b.isbn, b.coverImage AS coverImage, author name(s), category name(s), s.name, r.price, r.originalPrice, r.currency, r.inStock, r.url.
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
                        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                    )
                    chat = self._client.chats.create(
                        model=self.model_name,
                        config=config,
                    )
                    response = chat.send_message(prompt)
                    return response.text.strip() if response.text else ""
                else:
                    full_prompt = f"{system_instruction}\n\n{prompt}" if system_instruction else prompt
                    response = self._client.generate_content(
                        full_prompt,
                        generation_config={"temperature": self.temperature},
                    )
                    return response.text.strip() if response.text else ""
            except Exception as e:
                wait_time = min(2 ** attempt, 8) + random.uniform(0.1, 0.5)
                logger.warning("Gemini invocation attempt %d/%d error: %s. Retrying in %.1fs...", attempt, max_retries, e, wait_time)
                if attempt < max_retries:
                    time.sleep(wait_time)
                else:
                    logger.error("Gemini API invocation failed after %d attempts: %s", max_retries, e)
                    raise e

    def _call_gemini_stream(self, prompt: str, system_instruction: Optional[str] = None, max_retries: int = 3):
        if not self._client:
            self._init_client()
            if not self._client:
                raise ValueError("Google Gemini API Key is missing. Please set GEMINI_API_KEY or GOOGLE_API_KEY in your .env file.")

        response = None
        for attempt in range(1, max_retries + 1):
            try:
                if self._sdk_type == "google-genai":
                    from google.genai import types
                    config = types.GenerateContentConfig(
                        temperature=self.temperature,
                        system_instruction=system_instruction,
                        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                    )
                    chat = self._client.chats.create(
                        model=self.model_name,
                        config=config,
                    )
                    response = chat.send_message_stream(prompt)
                else:
                    full_prompt = f"{system_instruction}\n\n{prompt}" if system_instruction else prompt
                    response = self._client.generate_content(
                        full_prompt,
                        stream=True,
                        generation_config={"temperature": self.temperature},
                    )
                break
            except Exception as e:
                wait_time = min(2 ** attempt, 8) + random.uniform(0.1, 0.5)
                logger.warning("Gemini stream init attempt %d/%d error: %s. Retrying in %.1fs...", attempt, max_retries, e, wait_time)
                if attempt < max_retries:
                    time.sleep(wait_time)
                else:
                    logger.error("Gemini stream init failed after %d attempts: %s", max_retries, e)
                    raise e

        if response:
            try:
                for chunk in response:
                    if chunk.text:
                        yield chunk.text
            except Exception as e:
                logger.error("Error while receiving Gemini stream chunks: %s", e)
                raise e

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

2. Structure & Visuals:
   - Provide a clear, direct natural-language summary.
   - EMBED BOOK COVER IMAGES: MANDATORY. Whenever presenting a book and a non-empty `coverImage` URL is available in the database results, embed the cover image using HTML `<img src="coverImageURL" alt="Book Title" width="110" style="border-radius:8px; margin-right:12px; vertical-align:top; object-fit:cover;" referrerpolicy="no-referrer">` or Markdown `![Book Title](coverImageURL)` right next to or above the book details/title. Do NOT embed an image if `coverImage` is missing or empty.
   - Example entry format:
     ### 📚 Pregnancy Guide
     <img src="https://images.example.com/storage/product/1599215128.jpg" alt="Pregnancy Guide" width="110" style="border-radius:8px; margin-right:12px; vertical-align:top; object-fit:cover;" referrerpolicy="no-referrer">
     **Author:** Dr. Smith | **Category:** Health
   - Do NOT include raw text hyperlinks or web page URLs in text, but ALWAYS embed book cover images when `coverImage` is provided.
   - Use Markdown tables for price comparisons.
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
    def _sanitize_error_message(self, e: Exception) -> str:
        """Extracts a user-friendly error string without exposing internal code, sensitive keys, or raw JSON dicts."""
        err_str = str(e)
        if "503" in err_str or "UNAVAILABLE" in err_str or "high demand" in err_str:
            return "Error 503 (High Demand / Service Temporarily Unavailable). Please try again in a few minutes."
        elif "429" in err_str or "RESOURCE_EXHAUSTED" in err_str or "Quota" in err_str:
            return "Error 429 (Rate Limit Exceeded). Please try again in a few minutes."
        elif "401" in err_str or "UNAUTHENTICATED" in err_str or "API_KEY" in err_str:
            return "Error 401 (Authentication Error)."
        elif "500" in err_str or "INTERNAL" in err_str:
            return "Error 500 (Internal AI Service Error)."
        return "Error: AI service is temporarily unavailable. Please try again later."

    def _build_fallback_markdown(self, query_results: List[Dict[str, Any]], error_context: str = "") -> str:
        """
        Builds a structured markdown response directly from database query results when
        LLM response synthesis is unavailable or fails (e.g. 503 Overloaded or 429 Rate Limit).
        """
        err_notice = error_context if error_context else "Error 503 (High Demand)"
        if not query_results:
            return (
                f"> ⚠️ **Notice:** Natural-language AI synthesis is temporarily limited ({err_notice}). "
                "No matching book inventory records were found in the database for your search query. "
                "Please try again in a few minutes or later."
            )

        lines = [
            f"> ⚠️ **Notice:** Natural-language AI synthesis is temporarily limited ({err_notice}). "
            "Below are the live inventory and price records retrieved from the database:\n",
        ]

        for r in query_results[:15]:
            title = r.get("title") or r.get("name") or "Book"
            raw_authors = r.get("authors") or []
            clean_authors = sanitize_authors(raw_authors)
            author_str = ", ".join(clean_authors)
            cover = r.get("coverImage") or r.get("cover_image") or ""

            listings = r.get("listings")
            if not isinstance(listings, list):
                listings = []
                if "store" in r or "price" in r:
                    listings.append({
                        "store": r.get("store") or r.get("store_name"),
                        "price": r.get("price"),
                        "originalPrice": r.get("originalPrice") or r.get("original_price"),
                        "currency": r.get("currency", "LKR"),
                        "inStock": r.get("inStock") or r.get("in_stock", True),
                    })

            lines.append(f"### {title}")
            if cover and str(cover).startswith("http"):
                lines.append(f'<img src="{cover}" alt="{title}" width="180" style="border-radius:8px; margin:10px 0 14px 0; display:block;" referrerpolicy="no-referrer">\n')

            if author_str:
                lines.append(f"**Author:** {author_str}\n")

            if listings:
                lines.append("| Store | Price | Original Price | Status |")
                lines.append("| :--- | :--- | :--- | :--- |")
                for l in listings:
                    store = l.get("store") or "Store"
                    price = l.get("price")
                    price_str = f"{l.get('currency', 'LKR')} {price:,.2f}" if isinstance(price, (int, float)) else str(price or "N/A")
                    orig = l.get("originalPrice")
                    orig_str = f"{l.get('currency', 'LKR')} {orig:,.2f}" if isinstance(orig, (int, float)) and orig else "-"
                    stock = "In Stock" if l.get("inStock", True) else "Out of Stock"
                    lines.append(f"| {store} | {price_str} | {orig_str} | {stock} |")
                lines.append("")

        return "\n".join(lines)

    def synthesize_response_with_suggestions(
        self,
        user_query: str,
        cypher_query: str,
        query_results: List[Dict[str, Any]],
        chat_history: List[ChatMessage],
    ) -> tuple[str, List[str]]:
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

2. Structure & Visuals:
   - Provide a clear, direct natural-language summary.
   - EMBED BOOK COVER IMAGES: MANDATORY. Whenever presenting a book and a non-empty `coverImage` URL is available in the database results, embed the cover image on its OWN STANDALONE LINE immediately below the book title header (with NO other text on that line), using HTML:
     <img src="coverImageURL" alt="Book Title" width="180" style="border-radius:8px; margin:10px 0; display:block;" referrerpolicy="no-referrer">
     Do NOT embed an image if `coverImage` is missing or empty.
   - Example entry format:
     ### 📚 Pregnancy Guide
     <img src="https://images.example.com/storage/product/1599215128.jpg" alt="Pregnancy Guide" width="180" style="border-radius:8px; margin:10px 0; display:block;" referrerpolicy="no-referrer">

     **Author:** Dr. Smith | **Category:** Health
   - Clean Metadata: Do NOT output technical specs, dimensions, weights, or placeholder strings such as '(s) n/a', 'Format Publisher Nill', 'ISBN-13', 'Dimensions', 'Weight', or 'Unknown'. If author information is missing or invalid, omit the Author label.
   - Do NOT include raw text hyperlinks or web page URLs in text, but ALWAYS embed book cover images when `coverImage` is provided.
   - Use Markdown tables for price comparisons.
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
        default_suggestions = [
            "Which store has the lowest prices?",
            "Compare prices for Atomic Habits",
            "Show available thriller books",
        ]

        try:
            raw_output = self._call_gemini(prompt, system_instruction=system_instruction)

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
                suggestions = default_suggestions

            return answer, suggestions
        except Exception as e:
            logger.error("synthesize_response_with_suggestions LLM call failed: %s", e)
            fallback_answer = self._build_fallback_markdown(query_results)
            return fallback_answer, default_suggestions

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

2. Structure & Visuals:
   - Provide a clear, direct natural-language summary.
   - EMBED BOOK COVER IMAGES: MANDATORY. Whenever presenting a book and a non-empty `coverImage` URL is available in the database results, embed the cover image on its OWN STANDALONE LINE immediately below the book title header (with NO other text on that line), using HTML:
     <img src="coverImageURL" alt="Book Title" width="180" style="border-radius:8px; margin:10px 0; display:block;" referrerpolicy="no-referrer">
     Do NOT embed an image if `coverImage` is missing or empty.
   - Example entry format:
     ### 📚 Pregnancy Guide
     <img src="https://images.example.com/storage/product/1599215128.jpg" alt="Pregnancy Guide" width="180" style="border-radius:8px; margin:10px 0; display:block;" referrerpolicy="no-referrer">

     **Author:** Dr. Smith | **Category:** Health
   - Clean Metadata: Do NOT output technical specs, dimensions, weights, or placeholder strings such as '(s) n/a', 'Format Publisher Nill', 'ISBN-13', 'Dimensions', 'Weight', or 'Unknown'. If author information is missing or invalid, omit the Author label.
   - Do NOT include raw text hyperlinks or web page URLs in text, but ALWAYS embed book cover images when `coverImage` is provided.
   - Use Markdown tables for price comparisons.
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
        default_suggestions = [
            "Which store has the lowest prices?",
            "Compare prices for Atomic Habits",
            "Show available thriller books",
        ]

        try:
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
        except Exception as e:
            logger.error("Gemini streaming failed during response synthesis: %s", e)
            safe_err = self._sanitize_error_message(e)
            if not full_buffer:
                fallback_text = self._build_fallback_markdown(query_results, error_context=safe_err)
                yield (fallback_text, None)
            else:
                yield (f"\n\n> ⚠️ *Note: Response generation was interrupted ({safe_err}).*", None)

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
            suggestions = default_suggestions

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
