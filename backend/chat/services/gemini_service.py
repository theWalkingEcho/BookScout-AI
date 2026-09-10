import re
import json
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


class GeminiLLMService(ILLMServiceClient):
    """
    Infrastructure implementation of ILLMServiceClient using Google Gemini API.
    Handles Text-to-Cypher translation, Schema grounding, and Response synthesis.
    """

    def __init__(self, api_key: str, model_name: str = "gemini-3.6-flash", temperature: float = 0.2):
        self.api_key = api_key
        self.model_name = model_name
        self.temperature = temperature
        self._client = None
        self._sdk_type: Optional[str] = None
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

    def generate_cypher(
        self,
        user_query: str,
        chat_history: List[ChatMessage],
        schema_context: str,
    ) -> str:
        system_instruction = f"""
You are an expert Neo4j Cypher query generator and domain classifier for a Sri Lankan Bookstore Inventory & Price Comparison Knowledge Graph.

DOMAIN SCOPE:
- IN-SCOPE: Questions regarding books, novels, literature, authors, genres, categories, publishers, bookstore inventory, book prices, availability, stock, store comparisons, and reading recommendations.
- OUT-OF-SCOPE: General knowledge trivia (e.g. weather, politics, non-book products, coding/programming, sports, general science, math equations, recipes) that is completely unrelated to books, bookstores, or authors.

CRITICAL GUARDRAIL:
If the user's question is OUT-OF-SCOPE and has nothing to do with books, authors, bookstores, reading, or book pricing, output EXACTLY:
OUT_OF_SCOPE

GRAPH SCHEMA:
1. Nodes:
   - `(:Book)`: properties [isbn, title, normalizedTitle, format, publisher, language, description, coverImage, inStock, textEmbedding]
   - `(:Author)`: properties [name]
   - `(:Category)`: properties [name]
   - `(:Store)`: properties [name, website, currency] (e.g. the bookstores indexed in the database)

2. Relationships:
   - `(:Book)-[:WRITTEN_BY]->(:Author)`
   - `(:Book)-[:IN_CATEGORY]->(:Category)`
   - `(:Book)-[r:HAS_LISTING]->(:Store)`:
     Properties on [r:HAS_LISTING]: [listingId, price (float), originalPrice (float), inStock (boolean), url, currency, lastScraped]

RULES:
- ONLY output a single valid, read-only Cypher query. Do NOT use CREATE, MERGE, DELETE, SET, or ALTER.
- Use case-insensitive matching: `toLower(b.title) CONTAINS toLower('keyword')` or `toLower(a.name) CONTAINS toLower('author')` or `toLower(c.name) CONTAINS toLower('genre')` or `toLower(s.name) CONTAINS toLower('store')`.
- ALWAYS RETURN rich informative fields:
  - Book title (`b.title`), ISBN (`b.isbn`), author name(s), category name(s)
  - Store name (`s.name`)
  - Listing price (`r.price`), original price (`r.originalPrice`), currency (`r.currency`), stock status (`r.inStock`), and direct url (`r.url`)
- When asking for "cheapest" or "best price", ORDER BY `r.price ASC` and filter `WHERE r.price > 0`.
- Include LIMIT (default 20, or specific limit requested by user).
- Output ONLY the Cypher query inside a ```cypher block or plain text without explanations (or OUT_OF_SCOPE).

DYNAMIC CONTEXT:
{schema_context}
"""

        history_text = ""
        if chat_history:
            recent = chat_history[-4:]
            history_text = "Recent Conversation History:\n" + "\n".join([f"{msg.role}: {msg.content}" for msg in recent]) + "\n\n"

        prompt = f"""
{history_text}User Question: {user_query}

Generate the most accurate Neo4j Cypher query to answer the user's question, or return OUT_OF_SCOPE if unrelated to books/bookstores.
"""
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

    def generate_title(self, messages: List[Dict[str, str]]) -> str:
        """
        Generate a short 4-6 word conversation title from the first few messages.
        Uses a fast, lightweight Gemini call.
        """
        if not messages:
            return "New Conversation"

        convo_snippet = "\n".join(
            f"{m.get('role', 'user').capitalize()}: {m.get('content', '')[:200]}"
            for m in messages[:3]
        )
        prompt = (
            f"Based on this short conversation snippet, generate a concise 4 to 6 word "
            f"title that captures the main topic. Output ONLY the title — no quotes, no punctuation at end.\n\n"
            f"{convo_snippet}"
        )
        try:
            raw = self._call_gemini(prompt, system_instruction=None, max_retries=2)
            # Clean up: strip quotes, periods, leading/trailing whitespace
            title = raw.strip().strip('"').strip("'").rstrip(".")
            # Truncate if too long
            if len(title) > 60:
                title = title[:57] + "..."
            return title or "New Conversation"
        except Exception as e:
            logger.warning("generate_title failed: %s", e)
            return "New Conversation"

