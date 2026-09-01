import logging
import time
from typing import List, Optional

logger = logging.getLogger(__name__)


class EmbeddingService:
    """
    Generates text embeddings using Google Gemini embedding models.
    Used by the indexer to create vector representations of Book nodes.
    """

    def __init__(self, api_key: str, model: str = "gemini-embedding-2"):
        self.api_key = api_key
        self.model = model
        self._client = None
        self._sdk_type: Optional[str] = None
        self._init_client()

    def _init_client(self) -> None:
        if not self.api_key:
            logger.warning("GEMINI_API_KEY not set. EmbeddingService will not function.")
            return
        try:
            from google import genai
            self._client = genai.Client(api_key=self.api_key)
            self._sdk_type = "google-genai"
            logger.info("EmbeddingService initialised with model %s (google-genai)", self.model)
        except Exception as e:
            logger.error("Failed to initialise Google GenAI client for embeddings: %s", e)
            self._client = None

    def build_embedding_text(
        self,
        title: str,
        description: Optional[str] = None,
        authors: Optional[List[str]] = None,
        categories: Optional[List[str]] = None,
    ) -> str:
        """
        Constructs a rich natural-language string from book metadata to embed.
        Richer context produces higher-quality semantic vectors.
        """
        parts = [title.strip()]
        if description:
            parts.append(description.strip()[:500])
        if authors:
            authors_str = ", ".join(authors)
            parts.append("Authors: " + authors_str)
        if categories:
            categories_str = ", ".join(categories)
            parts.append("Categories: " + categories_str)
        return ". ".join(parts)

    def generate(self, text: str, retries: int = 3) -> Optional[List[float]]:
        """
        Generate an embedding vector for *text*.
        Returns None if the service is unavailable or all retries are exhausted.
        """
        if not self._client:
            logger.warning("EmbeddingService client not initialised. Skipping embedding.")
            return None

        for attempt in range(1, retries + 1):
            try:
                if self._sdk_type == "google-genai":
                    response = self._client.models.embed_content(
                        model=self.model,
                        contents=text,
                    )
                    embedding = response.embeddings[0].values
                    return list(embedding)
            except Exception as e:
                wait = 2 ** attempt
                logger.warning(
                    "Embedding attempt %d/%d failed: %s. Retrying in %ds",
                    attempt, retries, e, wait,
                )
                if attempt < retries:
                    time.sleep(wait)
                else:
                    logger.error("All %d embedding attempts exhausted for text: %.80s", retries, text)
        return None
