# 📚 BookScout AI — AI-Powered Book Discovery

> **An intelligent, multi-store bookstore inventory aggregator, knowledge graph, visual book discoverer, and AI shopping assistant powered by Neo4j, Google Gemini, FastAPI, and Streamlit.**

---

## 📑 Table of Contents

1. [Overview](#-overview)
2. [Key Features](#-key-features)
3. [Tech Stack](#-tech-stack)
4. [System Architecture & How It Works](#-system-architecture--how-it-works)
5. [Project Structure](#-project-structure)
6. [Environment Variables & Secrets Configuration](#-environment-variables--secrets-configuration)
7. [Prerequisites](#-prerequisites)
8. [Step-by-Step Setup Guide](#-step-by-step-setup-guide)
9. [Running the Application](#-running-the-application)
   - [Phase 1: Scraping & Indexing Knowledge Graph](#phase-1-scraping--indexing-the-knowledge-graph)
   - [Phase 2: Launching Backend Chat API](#phase-2-launching-the-backend-chat-api)
   - [Phase 3: Launching Frontend Chatbot UI](#phase-3-launching-the-frontend-chatbot-ui)
10. [API Endpoints & Reference](#-api-endpoints--reference)
11. [Database Graph Schema & Vector Indexes](#-database-graph-schema--vector-indexes)
12. [Sample Queries & Chatbot Capabilities](#-sample-queries--chatbot-capabilities)
13. [Troubleshooting & FAQ](#-troubleshooting--faq)
14. [License](#-license)

---

## 🌟 Overview

**BookScout AI** is an end-to-end solution designed to scrape, structure, visually present, and query book catalogs across multiple online bookstores. It unifies fragmented bookstore inventories into a centralized **Neo4j Knowledge Graph**, enriches catalog items with **Google Gemini Vector Embeddings** (3072-dimensional) and **Cover Image Harvesting**, and provides a conversational AI assistant that helps readers:

- **Compare live book prices and stock availability** across multiple retailers.
- **View high-quality book cover images** rendered dynamically inline in conversational responses.
- **Discover books using natural-language semantic concepts** (e.g., *"gripping historical fiction set during wartime"*).
- **Filter by exact author, price threshold, publication genre, and currency**.
- **Receive clean, sanitized metadata** without low-quality placeholder noise.

---

## ✨ Key Features

- 🖼️ **Automated Cover Image Extraction & Live Visual Rendering**:
  - Scrapes high-resolution cover images across storefront engines (Next.js `__NEXT_DATA__`, OpenGraph image meta tags, JSON-LD schemas, product HTML elements).
  - Renders cover image cards inline in real time within the Streamlit chat UI using a custom token stream parser (`_render_custom_stream`).
- 🕷️ **Intelligent Multi-Store Scraping Pipeline**:
  - Supports multi-threaded BFS crawling, XML sitemap discovery, structured data extraction (`cloudscraper`, `beautifulsoup4`).
- 🧹 **Robust Data Cleaning & Metadata Sanitization**:
  - Standardizes ISBNs, parses complex currency strings, normalizes titles, and filters promotional noise or invalid placeholder metadata (e.g. `(s) n/a`, `Format Publisher Nill`).
- 🧬 **Graph-Native Storage (Neo4j)**:
  - Models books, authors, categories, stores, cover images, and individual store listings as connected entities with property graphs.
- 🧠 **Hybrid Semantic + Keyword Search Engine**:
  - Combines **Neo4j Vector Index (3072-dimensional cosine similarity)** powered by Google Gemini with **Neo4j Fulltext Lucene Search** and LLM-generated Cypher queries.
- ⚡ **High-Performance FastAPI REST & NDJSON Streaming Backend**:
  - Supports synchronous `/chat`, real-time NDJSON streaming `/chat/stream`, direct candidate search `/search`, graph schema introspection `/schema`, and `/health` checks.
- 🎨 **Modern Streamlit Chat Interface**:
  - Premium dark-mode UI with live token streaming, custom HTML image cards, follow-up suggestion chips, and responsive layout.
- 🔄 **Incremental & Scheduled Sync (GitHub Actions)**:
  - Supports full database refreshes, incremental price/stock syncs, missing embedding backfills, and automated cron updates via GitHub Actions.

---

## 💻 Tech Stack

- **Backend Framework**: [FastAPI](https://fastapi.tiangolo.com/) - High-performance async REST & NDJSON streaming APIs.
- **Database & Vector Search**: [Neo4j](https://neo4j.com/) - Graph native storage and 3072-dimensional vector indexing.
- **AI & Embeddings**: [Google Gemini](https://deepmind.google/technologies/gemini/) - LLM-generated Cypher queries, natural language response synthesis, conversation title generation, and 3072-dim embeddings (`text-embedding-004`).
- **Frontend / UI**: [Streamlit](https://streamlit.io/) - Real-time conversational chat interface with custom token stream image rendering.
- **Web Scraping**: `cloudscraper`, `beautifulsoup4` - Multi-threaded storefront crawling, cover image harvesting, and JSON-LD parsing.
- **Automation**: [GitHub Actions](https://github.com/features/actions) - Scheduled workflows for embedding generation and database synchronization.

---

## 🏗️ System Architecture & How It Works

The system consists of four distinct yet interconnected layers:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           1. INGESTION & INDEXER                            │
│  ┌──────────────────────┐    ┌─────────────────┐    ┌────────────────────┐  │
│  │   Bookstore Scraper  │───>│ Data Sanitizer  │───>│  Embedding Service │  │
│  │ (BFS/Sitemap/Images) │    │  (cleaner.py)   │    │(gemini-embedding-2)│  │
│  └──────────────────────┘    └─────────────────┘    └─────────┬──────────┘  │
└───────────────────────────────────────────────────────────────┼─────────────┘
                                                                ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           2. NEO4J KNOWLEDGE GRAPH                          │
│   (Book {coverImage}) ──[:WRITTEN_BY]──> (Author)                           │
│   (Book) ──[:IN_CATEGORY]──> (Category)                                     │
│   (Book) ──[:HAS_LISTING {price, stock, url}]──> (Store)                    │
│   Indexes: Vector Index (3072-dim Cosine) + Fulltext Keyword Index          │
└───────────────────────────────────────────────────────────────┬─────────────┘
                                                                ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           3. CHAT API (FASTAPI REST & STREAMING)            │
│  ┌────────────────────────────────────────────────────────────────────────┐ │
│  │ Hybrid Search Orchestrator (services/chat_service.py)                  │ │
│  │  ├─ 1. Vector Search (Embed Query -> Cosine Similarity Top-K)         │ │
│  │  ├─ 2. Full-Text Lucene Search (Keyword Fallback)                      │ │
│  │  ├─ 3. LLM Cypher Generation (Structured queries & aggregations)       │ │
│  │  ├─ 4. Listing Deduplication & Cover Image Enriched Aggregation        │ │
│  │  ├─ 5. Gemini Response & NDJSON Token Streaming Synthesis             │ │
│  │  └─ 6. Live Conversation Title Generator                               │ │
│  └────────────────────────────────────────────────────────────────────────┘ │
└───────────────────────────────────────────────────────────────┬─────────────┘
                                                                ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                        4. CLIENT UI (STREAMLIT APP)                         │
│  - Real-time NDJSON token streaming with custom image card parser           │
│  - Live HTML book cover rendering with referrer-policy headers              │
│  - Price comparison tables & store availability details                     │
│  - Interactive follow-up suggestion chips                                   │
└─────────────────────────────────────────────────────────────────────────────┘
```

### End-to-End Workflow

1. **Scraping & Indexing Phase (`backend/indexer`)**:
   - `StoreScraper` crawls bookstore websites configured in `STORE_CONFIGS_JSON`.
   - Extracts title, author, category, price, stock status, product link, and **cover image URLs** via JSON-LD, Next.js state, or HTML parser.
   - `clean_listing()` strips promotional noise, normalizes ISBNs, and parses numeric prices.
   - `EmbeddingService` generates 3072-dimensional vector embeddings using Google Gemini.
   - `Neo4jBookstoreRepository` executes atomic Cypher `MERGE` and `SET` operations to write graph nodes and listings.

2. **Query & Hybrid Search Phase (`backend/chat`)**:
   - User inputs a query via synchronous `POST /chat` or streaming `POST /chat/stream`.
   - `ChatQueryService` runs vector cosine search and fulltext search in parallel.
   - `GeminiLLMService` generates target Cypher queries for exact filtering or aggregations when appropriate.
   - Candidates are merged, deduplicated, and enriched with `coverImage` properties.
   - Gemini synthesizes the response, mandating inline visual cover images and formatted price comparison tables while suppressing broken hyperlinks.

3. **User Interaction & Live Stream Phase (`frontend/streamlit_app.py`)**:
   - Streamlit consumes the NDJSON token stream through `_render_custom_stream`.
   - Inline `<img>` tags and markdown images are parsed live, creating visual cover cards with shadow styling and referrer policies.
   - Follow-up recommendation chips are rendered immediately after response completion.

---

## 📁 Project Structure

```
book-inventory-finder/
├── .env.example                     # Environment configuration template
├── .env                             # Active environment configuration (git-ignored)
├── README.md                        # Project documentation & setup guide
│
├── .github/
│   └── workflows/
│       └── indexer.yml              # Scheduled GitHub Actions (daily/weekly indexing)
│
├── backend/
│   ├── indexer/                     # Data scraping, cover harvesting & graph indexing
│   │   ├── app/
│   │   │   └── scraper.py           # Multi-threaded BFS, sitemap & Next.js scraper with cover image extraction
│   │   ├── models/
│   │   │   ├── dtos.py              # Data Transfer Objects
│   │   │   └── entities.py          # Domain dataclasses (Book, Author, Store, Listing)
│   │   ├── repositories/
│   │   │   ├── bookstore_repository.py       # Abstract repository interface
│   │   │   └── neo4j_bookstore_repository.py # Neo4j graph implementation with cover image support
│   │   ├── services/
│   │   │   ├── book_service.py      # Book upsert & batch vector embedding operations
│   │   │   ├── embedding_service.py # Gemini 3072-dim embedding service
│   │   │   └── scheduler.py         # Incremental sync & stale inventory cleanup
│   │   ├── cleaner.py               # Price, ISBN, title, and metadata sanitization
│   │   ├── config.py                # Indexer configuration loader
│   │   ├── requirements.txt         # Indexer dependencies
│   │   └── run_indexer.py           # CLI entry point for scraping & embedding management
│   │
│   └── chat/                        # Hybrid Search Engine & FastAPI REST/Streaming API
│       ├── models/
│       │   ├── book_detail.py       # API model for Book details
│       │   ├── book_listing_offer.py# API model for Store listing offers
│       │   ├── chat_message.py      # Chat payload models
│       │   ├── chat_response.py     # Chat response wrapper
│       │   └── cypher_query_result.py# Cypher execution wrapper
│       ├── repositories/
│       │   ├── neo4j_reader.py      # Graph reader & schema introspection client
│       │   └── neo4j_search.py      # Vector & hybrid search repository
│       ├── services/
│       │   ├── chat_service.py      # Hybrid search & NDJSON streaming orchestrator
│       │   ├── embedding_service.py # Query vector generation service
│       │   └── gemini_service.py    # LLM Cypher builder & visual synthesizer
│       ├── app.py                   # FastAPI application & REST/streaming routes
│       ├── config.py                # Chat API configuration loader
│       └── requirements.txt         # Chat service dependencies
│
└── frontend/                        # Interactive Web Interface
    ├── streamlit_app.py             # Streamlit app with custom live token stream & image rendering
    └── requirements.txt             # Frontend dependencies
```

---

## 🔑 Environment Variables & Secrets Configuration

Create a `.env` file in the root directory. All components (`backend/indexer`, `backend/chat`, and `frontend`) automatically load credentials from this root file.

Refer to `.env.example` for full options.

### Key Secrets & Settings:

```ini
# Neo4j Database Settings
NEO4J_URI=neo4j+s://your-neo4j-instance.databases.neo4j.io
NEO4J_USER=neo4j
NEO4J_PASSWORD=your_secure_password
NEO4J_DATABASE=neo4j

# Google Gemini API Key
GEMINI_API_KEY=your_gemini_api_key

# Search & Retrieval Configuration
HYBRID_SEARCH_TOP_K=10
VECTOR_SEARCH_TOP_K=5
FULLTEXT_SEARCH_TOP_K=5
SEMANTIC_SEARCH_TOP_K=5

# Scraper Settings
SCRAPE_LIMIT=500
SCRAPE_WORKERS=5
SCRAPE_BATCH_SIZE=50
```

---

## 🛠️ Prerequisites

1. **Python 3.9+** (`python --version`).
2. **Neo4j Database (5.11+)**:
   - **Cloud (Recommended)**: Free instance on [Neo4j AuraDB](https://neo4j.com/cloud/platform/aura-graph-database/).
   - **Local**: [Neo4j Desktop](https://neo4j.com/download/) or Docker (`docker run -p 7474:7474 -p 7687:7687 -e NEO4J_AUTH=neo4j/your_password neo4j:5.20-community`).
3. **Google Gemini API Key**:
   - Obtain a key from [Google AI Studio](https://aistudio.google.com/app/apikey).

---

## 🚀 Step-by-Step Setup Guide

### 1. Clone the Repository

```bash
git clone https://github.com/theWalkingEcho/BookScout-AI.git
cd book-inventory-finder
```

### 2. Create & Activate Virtual Environment

**On Windows (PowerShell):**
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

**On macOS / Linux:**
```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install Dependencies

```bash
# Install all required component dependencies
pip install -r backend/indexer/requirements.txt
pip install -r backend/chat/requirements.txt
pip install -r frontend/requirements.txt
```

### 4. Create Secrets File

```bash
cp .env.example .env
```
Fill in your `NEO4J_URI`, `NEO4J_PASSWORD`, and `GEMINI_API_KEY`.

---

## 🏃 Running the Application

### Phase 1: Scraping & Indexing the Knowledge Graph

```bash
cd backend/indexer
```

#### Option 1: Full Refresh (Fresh Database Setup)
*Clears existing graph data, sets constraints & vector indexes, scrapes configured stores including cover images, and embeds books.*
```bash
python run_indexer.py --full-refresh --embed
```

#### Option 2: Incremental Sync (Sync Prices & Stock)
*Preserves data, fetches new arrivals, updates prices and in-stock statuses, and embeds new items.*
```bash
python run_indexer.py --embed
```

#### Option 3: Backfill Missing Embeddings (No Scraping)
*Scans database for books lacking 3072-dimensional vector embeddings and generates them via Gemini.*
```bash
python run_indexer.py --check-embeddings
```

#### Option 4: Force Re-embedding All Books
```bash
python run_indexer.py --check-embeddings --reembed
```

---

### Phase 2: Launching the Backend Chat API

From project root:
```bash
uvicorn backend.chat.app:app --reload --port 8000
```

Once running:
- **API Base URL**: `http://localhost:8000`
- **Interactive Swagger Docs**: `http://localhost:8000/docs`
- **Health Check**: `http://localhost:8000/health`
- **Graph Schema**: `http://localhost:8000/schema`

---

### Phase 3: Launching the Frontend Chatbot UI

In a new terminal (with `.venv` activated), from project root:
```bash
streamlit run frontend/streamlit_app.py
```

The app will open automatically at `http://localhost:8501`.

---

## 📡 API Endpoints & Reference

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/health` | Liveness check, database connectivity, and graph node counts. |
| `GET` | `/schema` | Live Neo4j schema context, node labels, and relationship patterns. |
| `GET` | `/stores` | Returns array of all bookstore names indexed in graph. |
| `POST` | `/chat` | Natural language book search, multi-store comparison, and synthesis. |
| `POST` | `/chat/stream` | Real-time NDJSON streaming endpoint (yields `start`, `token`, and `done` events). |
| `POST` | `/search` | Direct hybrid search returning raw structured candidates without LLM synthesis. |

### `POST /chat` Example

#### Request
```json
{
  "query": "Show me available books by James Clear and compare prices",
  "history": []
}
```

#### Response
```json
{
  "answer": "### 📚 Atomic Habits\n<img src=\"https://images.example.com/cover/atomic-habits.jpg\" alt=\"Atomic Habits\" width=\"180\" style=\"border-radius:8px; margin:10px 0; display:block;\" referrerpolicy=\"no-referrer\">\n\n**Author:** James Clear | **Category:** Self-Help\n\n| Bookstore | Price | Stock Status |\n| :--- | :--- | :--- |\n| **Store A** | LKR 2,450.00 | ✅ In Stock |\n| **Store B** | LKR 2,800.00 | ✅ In Stock |\n\n🏆 **Best Deal:** Store A offers the lowest price at LKR 2,450.00.",
  "cypher_query": "MATCH (b:Book)-[r:HAS_LISTING]->(s:Store) WHERE b.normalizedTitle CONTAINS 'atomic habits' RETURN b, r, s",
  "records_count": 2,
  "suggestions": [
    "Which store has the lowest prices?",
    "Show books similar to Atomic Habits"
  ],
  "execution_time_ms": 312.45,
  "sources": [
    "Atomic Habits - Store A (LKR 2450.00)",
    "Atomic Habits - Store B (LKR 2800.00)"
  ]
}
```

---

## 🗄️ Database Graph Schema & Vector Indexes

### Cypher Graph Schema

```cypher
(:Book {
    isbn: STRING,                // Unique constraint
    title: STRING,
    normalizedTitle: STRING,
    description: STRING,
    format: STRING,
    language: STRING,
    publisher: STRING,
    coverImage: STRING,          // High-resolution cover image URL
    inStock: BOOLEAN,
    textEmbedding: LIST<FLOAT>   // 3072-dimensional Gemini vector
})

(:Author {
    name: STRING                 // Unique constraint
})

(:Category {
    name: STRING                 // Unique constraint
})

(:Store {
    name: STRING                 // Unique constraint
})

// Relationships
(:Book)-[:WRITTEN_BY]->(:Author)
(:Book)-[:IN_CATEGORY]->(:Category)
(:Book)-[:HAS_LISTING {
    listingId: STRING,
    price: FLOAT,
    originalPrice: FLOAT,
    inStock: BOOLEAN,
    url: STRING,
    currency: STRING,
    lastScraped: STRING,
    updatedAt: STRING
}]->(:Store)
```

### Automatic Constraints & Indexes

```cypher
// Unique Constraints
CREATE CONSTRAINT IF NOT EXISTS FOR (b:Book) REQUIRE b.isbn IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (a:Author) REQUIRE a.name IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (c:Category) REQUIRE c.name IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (s:Store) REQUIRE s.name IS UNIQUE;

// Vector Index (Cosine Similarity for Semantic Search)
CREATE VECTOR INDEX book_title_embedding IF NOT EXISTS
FOR (b:Book) ON (b.textEmbedding)
OPTIONS {
  indexConfig: {
    `vector.dimensions`: 3072,
    `vector.similarity_function`: 'cosine'
  }
};

// Fulltext Lucene Index (Keyword & Fuzzy Search)
CREATE FULLTEXT INDEX book_fulltext_index IF NOT EXISTS
FOR (b:Book) ON EACH [b.title, b.normalizedTitle, b.description];
```

---

## 💡 Sample Queries & Chatbot Capabilities

- **Price Comparisons**: *"Compare the prices of 'Atomic Habits' across all stores."*
- **Visual Discovery**: *"Show me popular fiction books with their cover images."*
- **Budget Search**: *"Find machine learning books under LKR 4,000 in stock."*
- **Author Filtering**: *"List all available books written by Walter Isaacson."*
- **Conversational Memory**:
  - Turn 1: *"Do you have books on finance?"*
  - Turn 2: *"Which one is the cheapest?"*

---

## ❓ Troubleshooting & FAQ

### 1. `neo4j.exceptions.AuthError` or `ServiceUnavailable`
- Check `NEO4J_URI`, `NEO4J_USER`, and `NEO4J_PASSWORD` in `.env`.
- For Neo4j AuraDB, use `neo4j+s://` and ensure the database is active.

### 2. `GEMINI_API_KEY is not set`
- Ensure your API key is in `.env` and valid on Google AI Studio.

### 3. Book Cover Images Not Rendering
- The app uses `referrerpolicy="no-referrer"` in HTML image elements to bypass third-party hotlinking restrictions. Ensure your browser is not blocking external image domain requests.

### 4. Vector index dimension mismatch
- Run `python backend/indexer/run_indexer.py --full-refresh --embed` to rebuild the 3072-dimensional vector index.

---

## 📄 License

This is a **personal portfolio project** — all rights reserved by the author.
