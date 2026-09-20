import logging
import time
from typing import List, Optional

logger = logging.getLogger(__name__)


class QueryEmbeddingService:
    """
    Generates text embeddings for natural language user queries using Google Gemini.
    Embeddings are used for semantic vector similarity search against Neo4j.
    """

    def __init__(self, api_key: str, model: str = "gemini-embedding-2"):
        self.api_key = api_key
        self.model = model
        self._client = None
        self._sdk_type: Optional[str] = None
        self._init_client()

    def _init_client(self) -> None:
        if not self.api_key:
            logger.warning("GEMINI_API_KEY is not set. Vector search will be disabled.")
            return
        try:
            from google import genai
            self._client = genai.Client(api_key=self.api_key)
            self._sdk_type = "google-genai"
            logger.info("QueryEmbeddingService initialized with model %s (google-genai)", self.model)
        except Exception as e:
            try:
                import google.generativeai as genai_legacy
                genai_legacy.configure(api_key=self.api_key)
                self._client = genai_legacy
                self._sdk_type = "google-generativeai"
                logger.info("QueryEmbeddingService initialized with model %s (legacy SDK)", self.model)
            except Exception as e2:
                logger.error("Failed to initialize Google GenAI SDK for embeddings: %s | %s", e, e2)
                self._client = None
                self._sdk_type = None

    @property
    def is_available(self) -> bool:
        return self._client is not None

    def embed_query(self, query_text: str, retries: int = 3) -> Optional[List[float]]:
        """
        Generates an embedding vector for a natural language user query.
        Returns None if embedding fails or service is unconfigured.
        """
        if not self._client or not query_text or not query_text.strip():
            return None

        clean_text = query_text.strip()
        for attempt in range(1, retries + 1):
            try:
                if self._sdk_type == "google-genai":
                    response = self._client.models.embed_content(
                        model=self.model,
                        contents=clean_text,
                    )
                    embedding = response.embeddings[0].values
                    return list(embedding)
                else:
                    import google.generativeai as genai_legacy
                    response = genai_legacy.embed_content(
                        model=f"models/{self.model}",
                        content=clean_text,
                    )
                    return list(response["embedding"])
            except Exception as e:
                wait_time = min(2 ** attempt, 8) + (attempt * 0.2)
                logger.warning(
                    "Query embedding attempt %d/%d failed: %s. Retrying in %.1fs...",
                    attempt, retries, e, wait_time,
                )
                if attempt < retries:
                    time.sleep(wait_time)
                else:
                    logger.error("All %d embedding attempts failed for query: %.60s", retries, clean_text)
        return None
