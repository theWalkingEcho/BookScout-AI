"""
FastAPI entry point for the Book Inventory Chat & Hybrid Search API.

Run with:
    cd backend/chat
    uvicorn app:app --reload --port 8000
or from project root:
    uvicorn backend.chat.app:app --reload --port 8000
"""

import os
import sys
import logging
from pathlib import Path
from typing import List, Optional, Dict, Any

# Ensure backend/chat and backend directories are in sys.path
_chat_dir = Path(__file__).resolve().parent
_backend_dir = _chat_dir.parent
_root_dir = _backend_dir.parent

for p in [_chat_dir, _backend_dir, _root_dir]:
    p_str = str(p)
    if p_str not in sys.path:
        sys.path.insert(0, p_str)

import json
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

try:
    from config import config
    from repositories.neo4j_reader import Neo4jGraphReader
    from services.gemini_service import GeminiLLMService
    from services.embedding_service import QueryEmbeddingService
    from services.chat_service import ChatQueryService
    from models.entities import ChatMessage, ChatResponse
except ImportError:
    from backend.chat.config import config
    from backend.chat.repositories.neo4j_reader import Neo4jGraphReader
    from backend.chat.services.gemini_service import GeminiLLMService
    from backend.chat.services.embedding_service import QueryEmbeddingService
    from backend.chat.services.chat_service import ChatQueryService
    from backend.chat.models.entities import ChatMessage, ChatResponse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Book Inventory Finder – Chat & Search API",
    description="Natural-language and hybrid semantic search over the Sri Lankan bookstore inventory knowledge graph.",
    version="1.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Dependency wiring (done once at startup)
# ---------------------------------------------------------------------------

_service: Optional[ChatQueryService] = None
_db_reader: Optional[Neo4jGraphReader] = None
_embedding_service: Optional[QueryEmbeddingService] = None


@app.on_event("startup")
def startup():
    global _service, _db_reader, _embedding_service
    logger.info("Initializing services with Neo4j URI: %s", config.neo4j.uri)
    
    _db_reader = Neo4jGraphReader(
        uri=config.neo4j.uri,
        user=config.neo4j.user,
        password=config.neo4j.password,
        database=config.neo4j.database,
        embedding_dimensions=config.gemini.embedding_dimensions,
    )
    
    llm_service = GeminiLLMService(
        api_key=config.gemini.api_key,
        model_name=config.gemini.model_name,
        temperature=config.gemini.temperature,
    )
    
    _embedding_service = QueryEmbeddingService(
        api_key=config.gemini.api_key,
        model=config.gemini.embedding_model,
    )
    
    _service = ChatQueryService(
        db_reader=_db_reader,
        llm_service=llm_service,
        embedding_service=_embedding_service,
        semantic_top_k=config.gemini.semantic_search_top_k,
    )
    
    logger.info(
        "Chat API initialised successfully (Neo4j: %s, LLM: %s, Embedding: %s)",
        config.neo4j.uri, config.gemini.model_name, config.gemini.embedding_model,
    )


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class MessageIn(BaseModel):
    role: str = "user"
    content: str


class ChatRequest(BaseModel):
    query: str
    history: List[MessageIn] = []


class ChatResponseOut(BaseModel):
    answer: str
    cypher_query: Optional[str] = None
    records_count: int = 0
    suggestions: List[str] = []
    execution_time_ms: float = 0.0
    error: Optional[str] = None
    sources: List[str] = []


class DirectSearchRequest(BaseModel):
    query: str
    top_k: int = 10


class DirectSearchResponse(BaseModel):
    query: str
    results: List[Dict[str, Any]]
    total_found: int
    execution_time_ms: float = 0.0


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health", tags=["ops"])
def health():
    """Quick liveness and database health check."""
    is_alive = _db_reader.health_check() if _db_reader else False
    stats = _db_reader.get_stats() if _db_reader else {}
    return {
        "status": "healthy" if is_alive else "degraded",
        "neo4j_connected": is_alive,
        "stats": stats,
    }


@app.get("/schema", tags=["ops"])
def schema():
    """Returns the live graph schema fetched from Neo4j."""
    if _db_reader is None:
        raise HTTPException(status_code=503, detail="Database service not initialized.")
    try:
        live_schema = _db_reader.get_live_schema()
        return {"schema": live_schema}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/stores", tags=["data"])
def stores():
    """Returns the list of indexed bookstores."""
    if _db_reader is None:
        return []
    return _db_reader.get_store_names()


@app.post("/chat", response_model=ChatResponseOut, tags=["chat"])
def chat(request: ChatRequest):
    """
    Send a natural-language question about book inventory, pricing, or recommendations.
    Performs hybrid semantic + keyword search, multi-store comparison, and synthesis.
    """
    if _service is None:
        raise HTTPException(status_code=503, detail="Chat service not ready yet.")

    history = [ChatMessage(role=m.role, content=m.content) for m in request.history]
    result: ChatResponse = _service.execute(
        user_query=request.query,
        chat_history=history,
    )

    return ChatResponseOut(
        answer=result.answer,
        cypher_query=result.query_used,
        records_count=len(result.sources) if result.sources else 0,
        suggestions=result.followup_suggestions or [],
        execution_time_ms=result.execution_time_ms,
        error=result.error,
        sources=result.sources or [],
    )


@app.post("/chat/stream", tags=["chat"])
def chat_stream(request: ChatRequest):
    """
    Stream natural-language answer tokens and events via NDJSON.
    Events yielded:
      - {"type": "start", "cypher_query": "...", "sources": [...], "records_count": ...}
      - {"type": "token", "content": "..."}
      - {"type": "done", "suggestions": [...], "execution_time_ms": ..., "sources": ...}
    """
    if _service is None:
        raise HTTPException(status_code=503, detail="Chat service not ready yet.")

    history = [ChatMessage(role=m.role, content=m.content) for m in request.history]

    def event_generator():
        try:
            for event in _service.execute_stream(user_query=request.query, chat_history=history):
                yield json.dumps(event, ensure_ascii=False) + "\n"
        except Exception as e:
            logger.error("Streaming endpoint error: %s", e)
            err_event = {
                "type": "error",
                "error": str(e),
                "suggestions": [
                    "Which store has the lowest prices?",
                    "Compare prices for Atomic Habits",
                ],
            }
            yield json.dumps(err_event, ensure_ascii=False) + "\n"

    return StreamingResponse(
        event_generator(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


class TitleRequest(BaseModel):
    messages: List[MessageIn] = []


@app.post("/generate-title", tags=["chat"])
def generate_title(request: TitleRequest):
    """
    Generate a short AI conversation title from the first few chat messages.
    Returns {"title": "..."}.
    """
    if _service is None:
        return {"title": "New Conversation"}
    try:
        msgs = [{"role": m.role, "content": m.content} for m in request.messages[:3]]
        title = _service.llm_service.generate_title(msgs)
        return {"title": title or "New Conversation"}
    except Exception as e:
        logger.warning("Title generation failed: %s", e)
        return {"title": "New Conversation"}


@app.post("/search", response_model=DirectSearchResponse, tags=["search"])
def direct_search(request: DirectSearchRequest):
    """
    Perform direct hybrid search (vector similarity + keyword fulltext search)
    without LLM response synthesis, returning raw structured candidates.
    """
    if _db_reader is None:
        raise HTTPException(status_code=503, detail="Database service not ready.")

    import time
    start = time.perf_counter()
    query_vector = None
    if _embedding_service and _embedding_service.is_available:
        query_vector = _embedding_service.embed_query(request.query)

    records = _db_reader.hybrid_search(
        query_text=request.query,
        query_embedding=query_vector,
        top_k=request.top_k,
    )
    elapsed = (time.perf_counter() - start) * 1000.0

    return DirectSearchResponse(
        query=request.query,
        results=records,
        total_found=len(records),
        execution_time_ms=round(elapsed, 2),
    )
