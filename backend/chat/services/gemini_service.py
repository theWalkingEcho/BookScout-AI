import re
import json
import logging
from typing import List, Dict, Any, Optional

from models.entities import ChatMessage
from repositories.neo4j_reader import ILLMServiceClient

logger = logging.getLogger(__name__)


class ILLMServiceClient:
    """
    Interface for LLM operations (Text-to-Cypher generation, Response synthesis).
    Allows plugging in Google Gemini, mock LLMs, or other providers without affecting usecases.
    """

    def generate_cypher(
        self,
        user_query: str,
        chat_history: List[ChatMessage],
        schema_context: str,
    ) -> str:
        """Translates natural language user question into an accurate read-only Cypher query."""
        raise NotImplementedError

    def synthesize_response(
        self,
        user_query: str,
        cypher_query: str,
        query_results: List[Dict[str, Any]],
        chat_history: List[ChatMessage],
    ) -> str:
        """Generates a helpful, user-friendly markdown response from the graph query results."""
        raise NotImplementedError

    def generate_followup_suggestions(
        self,
        user_query: str,
        assistant_response: str,
        query_results: List[Dict[str, Any]],
    ) -> List[str]:
        """Generates relevant follow-up questions for the user to explore further."""
        raise NotImplementedError


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
        self._init_client()

    def _init_client(self):
        if not self.api_key:
            logger.warning("Gemini API key is not configured. LLM calls will fail until GEMINI_API_KEY is provided.")
            return

        # Attempt modern google-genai SDK first, fallback to google-generativeai
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

    def _call_gemini(self, prompt: str, system_instruction: Optional[str] = None) -> str:
        if not self._client:
            self._init_client()
            if not self._client:
                raise ValueError("Google Gemini API Key is missing. Please set GEMINI_API_KEY or GOOGLE_API_KEY in your .env file.")

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
                # Legacy SDK
                full_prompt = f"{system_instruction}\n\n{prompt}" if system_instruction else prompt
                response = self._client.generate_content(
                    full_prompt,
                    generation_config={"temperature": self.temperature},
                )
                return response.text.strip() if response.text else ""
        except Exception as e:
            logger.error("Gemini API invocation failed: %s", e)
            raise e

    def _clean_cypher_output(self, raw_output: str) -> str:
        """Strips markdown code fences and extraneous text from the LLM output."""
        cleaned = raw_output.strip()
        # Match ```cypher ... ``` or ``` ... ```
        match = re.search(r"```(?:cypher)?\s*([\s\S]*?)\s*```", cleaned, re.IGNORECASE)
        if match:
            cleaned = match.group(1).strip()
        # Remove trailing semicolon if present
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
You are an expert Neo4j Cypher query generator for a Sri Lankan Bookstore Inventory & Price Comparison Knowledge Graph.

GRAPH SCHEMA:
1. Nodes:
   - `(:Book)`: properties [isbn, title, normalizedTitle, format, publisher, language, description, coverImage, inStock]
   - `(:Author)`: properties [name]
   - `(:Category)`: properties [name]
   - `(:Store)`: properties [name, website, currency] (e.g. 'Sarasavi Bookshop', 'Jump Books LK', 'Book Bazaar LK', 'Vijitha Yapa', 'Makeen Books')

2. Relationships:
   - `(:Book)-[:WRITTEN_BY]->(:Author)`
   - `(:Book)-[:IN_CATEGORY]->(:Category)`
   - `(:Book)-[r:HAS_LISTING]->(:Store)`:
     Properties on [r:HAS_LISTING]: [listingId, price (float), originalPrice (float), inStock (boolean), url, currency, lastScraped]

RULES:
- ONLY output a single valid, read-only Cypher query. Do NOT use CREATE, MERGE, DELETE, SET, or ALTER.
- Use case-insensitive matching for titles/authors/categories: e.g. `toLower(b.title) CONTAINS toLower('keyword')` or `toLower(a.name) CONTAINS toLower('author')` or `toLower(s.name) CONTAINS toLower('store')`.
- ALWAYS RETURN rich informative fields:
  - Book title, ISBN, author name(s), category name(s)
  - Store name (`s.name`)
  - Listing price (`r.price`), original price (`r.originalPrice`), currency (`r.currency`), stock status (`r.inStock`), and direct url (`r.url`)
- When asking for "cheapest" or "best price", ORDER BY `r.price ASC` and filter `WHERE r.price > 0`.
- When checking store inventory (e.g. "books in Sarasavi"), match `MATCH (b:Book)-[r:HAS_LISTING]->(s:Store)` with `WHERE toLower(s.name) CONTAINS toLower('store_name')`.
- Include LIMIT (default 20, or specific limit requested by user).
- Output ONLY the Cypher query inside a ```cypher block or plain text without explanations.

DYNAMIC CONTEXT:
{schema_context}
"""

        history_text = ""
        if chat_history:
            recent = chat_history[-4:]
            history_text = "Recent Conversation History:\n" + "\n".join([f"{msg.role}: {msg.content}" for msg in recent]) + "\n\n"

        prompt = f"""
{history_text}User Question: {user_query}

Generate the most accurate Neo4j Cypher query to answer the user's question.
"""
        raw_response = self._call_gemini(prompt, system_instruction=system_instruction)
        return self._clean_cypher_output(raw_response)

    def synthesize_response(
        self,
        user_query: str,
        cypher_query: str,
        query_results: List[Dict[str, Any]],
        chat_history: List[ChatMessage],
    ) -> str:
        system_instruction = """
You are a friendly, intelligent, and knowledgeable Book Inventory & Price Comparison Assistant.
Your goal is to answer the user's question clearly and accurately using the Neo4j database results provided.

GUIDELINES:
1. Structure:
   - Provide a clear, direct answer in natural language.
   - For book searches and price comparisons, use a well-formatted Markdown table:
     | Book Title | Author | Store | Price | In Stock? | Link |
   - Clearly highlight the **cheapest option** or **best deal** with a badge/emoji (e.g. 🏆 Lowest Price, 🏷️ Discounted).
   - If a book is available in multiple stores, compare the prices and state how much the user saves.
   - Include direct store links if available (e.g. `[Buy at Store](url)`).
2. Accuracy:
   - ONLY cite information present in the database results.
   - If no records were found, politely state that no matching books or stores were found in the current inventory, and suggest alternative titles or search keywords.
   - If price is in LKR, format as `LKR 1,500.00` or `Rs. 1,500.00`.
3. Tone:
   - Professional, enthusiastic, helpful, and concise.
"""

        prompt = f"""
User Question: {user_query}

Executed Cypher Query:
```cypher
{cypher_query}
```

Database Query Results ({len(query_results)} records found):
{json.dumps(query_results[:50], default=str, indent=2)}

Please synthesize an engaging, well-formatted markdown response for the user.
"""
        return self._call_gemini(prompt, system_instruction=system_instruction)

    def generate_followup_suggestions(
        self,
        user_query: str,
        assistant_response: str,
        query_results: List[Dict[str, Any]],
    ) -> List[str]:
        try:
            prompt = f"""
Based on the user's question: "{user_query}"
And the assistant's response with {len(query_results)} book/store results,
Generate 3 short, relevant, and engaging follow-up questions the user might want to click next.
Return ONLY a valid JSON array of 3 strings. Example: ["Compare prices across all stores", "Show top 5 cheapest books", "Check availability at Sarasavi"]
"""
            raw = self._call_gemini(prompt)
            match = re.search(r"\[.*\]", raw, re.DOTALL)
            if match:
                suggestions = json.loads(match.group(0))
                if isinstance(suggestions, list) and len(suggestions) > 0:
                    return [str(s) for s in suggestions[:4]]
        except Exception as e:
            logger.debug("Failed to generate custom suggestions: %s", e)

        # Default fallback suggestions
        return [
            "Which store has the cheapest books?",
            "What books are available at Sarasavi Bookshop?",
            "Compare prices for Atomic Habits",
            "Show available fiction books",
        ]
