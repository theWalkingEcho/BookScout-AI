import json
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

NEO4J_CONFIG = {
    "uri": os.getenv("NEO4J_URI", ""),
    "user": os.getenv("NEO4J_USER", ""),
    "password": os.getenv("NEO4J_PASSWORD", ""),
    "database": os.getenv("NEO4J_DATABASE", "bookstore-inventory"),
}

# Maximum number of product pages to scrape per run.
# Set to None to scrape everything found (may be slow).
_scrape_limit_env = os.getenv("SCRAPE_LIMIT")
SCRAPE_LIMIT = int(_scrape_limit_env) if _scrape_limit_env and _scrape_limit_env.strip().lower() not in ("none", "") else None
SCRAPE_BATCH_SIZE = int(os.getenv("SCRAPE_BATCH_SIZE", "50"))
SCRAPE_WORKERS = int(os.getenv("SCRAPE_WORKERS", "8"))

# Embedding settings
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "gemini-embedding-004")
EMBEDDING_DIMENSIONS = 1536  # Fixed: gemini-embedding-004 output dimension

import ast

_store_configs_env = os.getenv("STORE_CONFIGS_JSON")
if _store_configs_env:
    try:
        STORE_CONFIGS = json.loads(_store_configs_env)
    except json.JSONDecodeError as json_err:
        try:
            STORE_CONFIGS = ast.literal_eval(_store_configs_env)
        except Exception as ast_err:
            raise ValueError(
                f"STORE_CONFIGS_JSON is not valid JSON or Python literal. "
                f"JSON Error: {json_err}. AST Error: {ast_err}"
            ) from json_err
else:
    STORE_CONFIGS = None