import os
from pathlib import Path
from dataclasses import dataclass
from dotenv import load_dotenv

# Search for .env in current directory, parent directory, and project root
_possible_env_paths = [
    Path.cwd() / ".env",
    Path(__file__).resolve().parent / ".env",
    Path(__file__).resolve().parent.parent / ".env",
    Path(__file__).resolve().parent.parent.parent / ".env",
]

for p in _possible_env_paths:
    if p.exists():
        load_dotenv(dotenv_path=p)
        break
else:
    load_dotenv()


@dataclass(frozen=True)
class Neo4jConfig:
    uri: str = os.getenv("NEO4J_URI", "")
    user: str = os.getenv("NEO4J_USER", "neo4j")
    password: str = os.getenv("NEO4J_PASSWORD", "")
    database: str = os.getenv("NEO4J_DATABASE", "bookstore-inventory")


@dataclass(frozen=True)
class GeminiConfig:
    api_key: str = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or ""
    model_name: str = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
    temperature: float = float(os.getenv("GEMINI_TEMPERATURE", "0.2"))
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "gemini-embedding-2")
    embedding_dimensions: int = int(os.getenv("EMBEDDING_DIMENSIONS", "3072"))
    semantic_search_top_k: int = int(os.getenv("SEMANTIC_SEARCH_TOP_K", "15"))
    vector_search_top_k: int = int(os.getenv("VECTOR_SEARCH_TOP_K", "10"))
    fulltext_search_top_k: int = int(os.getenv("FULLTEXT_SEARCH_TOP_K", "10"))
    hybrid_search_top_k: int = int(os.getenv("HYBRID_SEARCH_TOP_K", "15"))


@dataclass(frozen=True)
class AppConfig:
    host: str = os.getenv("APP_HOST", "0.0.0.0")
    port: int = int(os.getenv("APP_PORT", "8000"))
    debug: bool = os.getenv("APP_DEBUG", "false").lower() in ("1", "true", "yes")
    neo4j: Neo4jConfig = Neo4jConfig()
    gemini: GeminiConfig = GeminiConfig()


config = AppConfig()
