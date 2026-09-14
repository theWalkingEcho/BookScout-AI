# 📚 BookScout AI — AI-Powered Book Discovery

> **An intelligent, multi-store bookstore inventory aggregator, knowledge graph, and AI shopping assistant powered by Neo4j, Google Gemini, FastAPI, and Streamlit.**

---

## 📑 Table of Contents

1. [Overview](#-overview)
2. [Key Features](#-key-features)
3. [System Architecture & How It Works](#-system-architecture--how-it-works)
4. [Project Structure](#-project-structure)
5. [Environment Variables & Secrets Configuration](#-environment-variables--secrets-configuration)
6. [Prerequisites](#-prerequisites)
7. [Step-by-Step Setup Guide](#-step-by-step-setup-guide)
8. [Running the Application](#-running-the-application)
   - [Phase 1: Scraping & Indexing Knowledge Graph](#phase-1-scraping--indexing-the-knowledge-graph)
   - [Phase 2: Launching Backend Chat API](#phase-2-launching-the-backend-chat-api)
   - [Phase 3: Launching Frontend Chatbot UI](#phase-3-launching-the-frontend-chatbot-ui)
9. [API Endpoints & Reference](#-api-endpoints--reference)
10. [Database Graph Schema & Vector Indexes](#-database-graph-schema--vector-indexes)
11. [Sample Queries & Chatbot Capabilities](#-sample-queries--chatbot-capabilities)
12. [Troubleshooting & FAQ](#-troubleshooting--faq)
13. [License](#-license)

---

## 🌟 Overview

**BookScout AI** is an end-to-end solution designed to scrape, structure, and query book catalogs across multiple online bookstores. It unifies fragmented bookstore inventories into a centralized **Neo4j Knowledge Graph**, enriches catalog items with **Google Gemini Vector Embeddings**, and provides a conversational AI assistant that helps readers:

- Compare live book prices and stock availability across multiple retailers.
- Discover books using natural-language semantic concepts (e.g., *"gripping historical fiction set during wartime"*).
- Filter by exact author, price threshold, publication genre, and currency.
- Receive direct purchase links with store-specific price breakdowns.

---

## ✨ Key Features

- 🕷️ **Intelligent Web Scraping Pipeline**: Supports multi-threaded BFS crawling, XML sitemap discovery, Next.js `__NEXT_DATA__` structured data extraction, JSON-LD schema parsing, etc.
- 🧹 **Robust Data Cleaning & Normalization**: Standardizes ISBNs, parses complex currency strings, normalizes book titles, filters author names, and detects duplicate listings.
- 🧬 **Graph-Native Storage (Neo4j)**: Models books, authors, categories, stores, and individual store listings as connected entities with property graphs.
- 🧠 **Hybrid Semantic + Keyword Search**: Combines **Neo4j Vector Index (3072-dimensional cosine similarity)** powered by Google Gemini with **Neo4j Fulltext Lucene Search** and LLM-generated Cypher queries.
- ⚡ **High-Performance FastAPI Backend**: REST API with real-time multi-store consolidation, health checks, live schema introspection, and conversational memory.
- 🎨 **Modern Streamlit Frontend UI**: Premium dark-mode interface featuring real-time streaming responses, price comparison tables, and dynamic follow-up suggestions.
- 🔄 **Incremental & Full Refresh Synchronization**: Sync new arrivals, update existing stock and prices, and auto-prune stale books.

---

## 🏗️ System Architecture & How It Works

The system consists of three distinct yet interconnected layers:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           1. INGESTION & INDEXER                            │
│  ┌──────────────────────┐    ┌─────────────────┐    ┌────────────────────┐  │
│  │   Bookstore Scraper  │───>│ Data Sanitizer  │───>│  Embedding Service │  │
│  │ (BFS / Sitemap / SSR)│    │  (cleaner.py)   │    │(gemini-embedding-2)│  │
│  └──────────────────────┘    └─────────────────┘    └─────────┬──────────┘  │
└───────────────────────────────────────────────────────────────┼─────────────┘
                                                                ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           2. NEO4J KNOWLEDGE GRAPH                          │
│   (Book) ──[:WRITTEN_BY]──> (Author)                                        │
│   (Book) ──[:IN_CATEGORY]──> (Category)                                     │
│   (Book) ──[:HAS_LISTING {price, stock, url}]──> (Store)                    │
│   Indexes: Vector Index (3072-dim Cosine) + Fulltext Keyword Index          │
└───────────────────────────────────────────────────────────────┬─────────────┘
                                                                ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           3. CHAT API (FASTAPI)                             │
│  ┌────────────────────────────────────────────────────────────────────────┐ │
│  │ Hybrid Search Orchestrator (services/chat_service.py)                  │ │
│  │  ├─ 1. Vector Search (Embed Query -> Cosine Similarity Top-K)         │ │
│  │  ├─ 2. Full-Text Lucene Search (Keyword Fallback)                      │ │
│  │  ├─ 3. LLM Cypher Generation (Structured queries & aggregations)       │ │
│  │  ├─ 4. Cross-Store Listing Deduplication & Price Comparison            │ │
│  │  └─ 5. Gemini Synthesis (Natural Response + Follow-up Suggestions)     │ │
│  └────────────────────────────────────────────────────────────────────────┘ │
└───────────────────────────────────────────────────────────────┬─────────────┘
                                                                ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                        4. CLIENT UI (STREAMLIT APP)                         │
│  - Single-session conversational chat interface                             │
│  - Live price comparison badges & markdown renderers                        │
│  - Instant follow-up chips & quick questions                                │
└─────────────────────────────────────────────────────────────────────────────┘
```

### End-to-End Workflow

1. **Scraping Phase (`backend/indexer`)**:
   - `StoreScraper` crawls bookstores configured in `STORE_CONFIGS_JSON`.
   - Raw HTML and JSON-LD metadata are transformed into standardized listing dictionaries.
   - `clean_listing()` strips promotional noise, normalizes ISBNs, and parses numeric prices.
   - `EmbeddingService` generates vector embeddings from title, author, category, and description using Google Gemini.
   - `Neo4jBookstoreRepository` executes atomic Cypher `MERGE` and `SET` operations to update graph nodes and relationships.

2. **Query & Hybrid Search Phase (`backend/chat`)**:
   - The user query is sent to `/chat`.
   - `ChatQueryService` simultaneously invokes semantic vector similarity against the Neo4j vector index and fulltext search.
   - If analytical filters (e.g. *"books under LKR 2000"*) are present, `GeminiLLMService` writes dynamic Cypher queries against the live schema.
   - Results from all candidate sets are deduplicated and merged by ISBN / normalized title.
   - Gemini formats the response with price tables, store availability, and interactive follow-up suggestions.

3. **User Interaction Phase (`frontend/streamlit_app.py`)**:
   - The user chats through a responsive, styled interface.
   - Conversation history is held in-memory for the duration of the session.
   - Follow-up suggestions are rendered after each AI response.

---

## 📁 Project Structure

```
book-inventory-finder/
├── .env.example                     # Environment secrets & config template
├── .env                             # Active environment configuration (git-ignored)
├── README.md                        # Documentation & setup guide
│
├── backend/
│   ├── indexer/                     # Data scraping, cleaning, and graph indexing
│   │   ├── app/
│   │   │   └── scraper.py           # Deep BFS, sitemap & Next.js web scraper
│   │   ├── models/
│   │   │   ├── dtos.py              # Data Transfer Objects
│   │   │   └── entities.py          # Domain dataclasses (Book, Author, Store, Listing)
│   │   ├── repositories/
│   │   │   ├── bookstore_repository.py       # Abstract repository interface
│   │   │   └── neo4j_bookstore_repository.py # Neo4j repository implementation
│   │   ├── services/
│   │   │   ├── book_service.py      # Book upsert & batch embedding operations
│   │   │   ├── embedding_service.py # Gemini embedding generation
│   │   │   └── scheduler.py         # Weekly update & stale inventory cleanup
│   │   ├── cleaner.py               # Price, ISBN, and text data sanitization
│   │   ├── config.py                # Indexer configuration loader
│   │   ├── requirements.txt         # Indexer Python dependencies
│   │   └── run_indexer.py           # Main CLI entry point for scraping & indexing
│   │
│   └── chat/                        # Natural Language Query API & Search Engine
│       ├── models/
│       │   ├── book_detail.py       # API DTO for Book details
│       │   ├── book_listing_offer.py# API DTO for store listings
│       │   ├── chat_message.py      # Chat message models
│       │   ├── chat_response.py     # Chat response wrapper
│       │   └── cypher_query_result.py# Cypher return type wrapper
│       ├── repositories/
│       │   ├── neo4j_reader.py      # Neo4j read client, basic queries
│       │   └── neo4j_search.py      # Vector & hybrid search implementations
│       ├── services/
│       │   ├── chat_service.py      # Multi-phase search & synthesis orchestrator
│       │   ├── embedding_service.py # Query vector generation
│       │   └── gemini_service.py    # LLM Cypher generator & response synthesizer
│       ├── app.py                   # FastAPI REST API application
│       ├── config.py                # Chat service configuration loader
│       └── requirements.txt         # Chat service Python dependencies
│
└── frontend/                        # Client-facing web interface
    ├── streamlit_app.py             # Streamlit chatbot web application
    └── requirements.txt             # Frontend Python dependencies
```

---

## 🔑 Environment Variables & Secrets Configuration

Create a `.env` file in the root directory of the project. Both `backend/indexer`, `backend/chat`, and `frontend` are configured to automatically load the root `.env` file.

To keep sensitive credentials secure, we do not list example API keys or passwords here. Please refer to the `.env.example` file in the root directory for a complete list of required environment variables and their formats.

**Core Secrets Needed:**
- `NEO4J_URI`
- `NEO4J_USER`
- `NEO4J_PASSWORD`
- `GEMINI_API_KEY`

Copy `.env.example` to `.env` and fill in your credentials.

---

## 🛠️ Prerequisites

Before getting started, make sure you have:

1. **Python 3.9+** installed (`python --version` or `python3 --version`).
2. **Neo4j Database (5.11+)**:
   - **Option A (Cloud - Recommended)**: Free instance on [Neo4j AuraDB](https://neo4j.com/cloud/platform/aura-graph-database/).
   - **Option B (Local)**: [Neo4j Desktop](https://neo4j.com/download/) or Docker (`docker run -p 7474:7474 -p 7687:7687 -e NEO4J_AUTH=neo4j/your_password neo4j:5.20-community`).
3. **Google Gemini API Key**:
   - Get a free key from [Google AI Studio](https://aistudio.google.com/app/apikey).

---

## 🚀 Step-by-Step Setup Guide

### 1. Clone the Repository

```bash
git clone https://github.com/your-username/book-inventory-finder.git
cd book-inventory-finder
```

### 2. Create and Activate a Virtual Environment

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

You can install the dependencies across all modules:

```bash
# 1. Install Indexer dependencies
pip install -r backend/indexer/requirements.txt

# 2. Install Chat API dependencies
pip install -r backend/chat/requirements.txt

# 3. Install Frontend UI dependencies
pip install -r frontend/requirements.txt
```

### 4. Create Your Environment Secrets File

```bash
# Copy the example file
cp .env.example .env
```

Open `.env` in your text editor and provide your actual `NEO4J_URI`, `NEO4J_PASSWORD`, and `GEMINI_API_KEY`.

---

## 🏃 Running the Application

### Phase 1: Scraping & Indexing the Knowledge Graph

The indexer CLI (`backend/indexer/run_indexer.py`) handles web crawling, data normalization, Gemini vector embedding generation, and Neo4j graph storage.

```bash
cd backend/indexer
```

#### Option 1: Full Refresh (Fresh Database)
*Clears the existing database, creates graph constraints & vector indexes, scrapes all stores, and embeds every book.*
```bash
python run_indexer.py --full-refresh --embed
```

#### Option 2: Incremental Sync (Daily / Weekly Updates)
*Preserves existing data, fetches new/updated listings, generates embeddings for new books, updates prices and in-stock statuses, and removes discontinued books.*
```bash
python run_indexer.py --embed
```

#### Option 3: Embed Missing Books Only (No Scraping)
*Scans the existing database for any books that lack vector embeddings and generates them using Gemini without scraping.*
```bash
python run_indexer.py --check-embeddings
```

#### Option 4: Force Re-embedding All Books
*Regenerates vector embeddings for all books in the database (useful when changing embedding dimensions or models).*
```bash
python run_indexer.py --check-embeddings --reembed
```

#### Option 5: Scrape Without Embeddings (Fast Crawl Test)
*Tests scraper extraction without consuming Gemini API tokens.*
```bash
python run_indexer.py --no-embed
```

### Automated Indexing (GitHub Actions)

This project includes a GitHub Actions workflow (`.github/workflows/indexer.yml`) that automates the scraper and embedding generation.

It is scheduled to run:
- **Daily (02:00 UTC)**: Checks for and generates any missing vector embeddings.
- **Weekly (Sun 00:00 UTC)**: Runs an incremental sync to update prices, stock, and new arrivals.
- **Monthly (1st at 00:00 UTC)**: Performs a full database refresh.

You can also trigger these tasks manually from the **Actions** tab in GitHub by selecting the "Bookstore Indexer" workflow.

**Required GitHub Secrets:**
To allow GitHub Actions to run, navigate to your repository's **Settings > Secrets and variables > Actions > New repository secret** and add the following keys from your `.env` file:
- `NEO4J_URI`
- `NEO4J_USER`
- `NEO4J_PASSWORD`
- `NEO4J_DATABASE`
- `GEMINI_API_KEY`
- `STORE_CONFIGS_JSON` (Optional, if overriding default stores)

---

### Phase 2: Launching the Backend Chat API

The FastAPI service powers the hybrid search engine, Cypher generation, and response synthesis.

From the project root:
```bash
uvicorn backend.chat.app:app --reload --port 8000
```

Or from the `backend/chat` directory:
```bash
cd backend/chat
uvicorn app:app --reload --port 8000
```

Once running:
- **API Base URL**: `http://localhost:8000`
- **Interactive Swagger Documentation**: `http://localhost:8000/docs`
- **Health & Liveness Check**: `http://localhost:8000/health`

---

### Phase 3: Launching the Frontend Chatbot UI

In a new terminal window (with the virtual environment activated):

From the project root:
```bash
streamlit run frontend/streamlit_app.py
```

The Streamlit UI will open automatically in your browser at `http://localhost:8501`.

---

## 📡 API Endpoints & Reference

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/health` | Service liveness check, database connectivity status, and node statistics. |
| `GET` | `/schema` | Live Neo4j schema context, active node labels, and relationship patterns. |
| `GET` | `/stores` | Array of all bookstore names indexed in the knowledge graph. |
| `POST` | `/chat` | Natural language multi-store book search, comparison, and synthesis. |
| `POST` | `/chat/stream` | Real-time NDJSON streaming endpoint for LLM tokens, sources, and suggestions. |
| `POST` | `/search` | Direct hybrid search returning structured candidates without LLM synthesis. |

### `POST /chat` Request & Response Example

#### Request Body
```json
{
  "query": "Which stores have 'Atomic Habits' in stock and what are their prices?",
  "history": []
}
```

#### Response Body
```json
{
  "answer": "I found **Atomic Habits** by James Clear available across 2 bookstores:\n\n| Bookstore | Price | Stock Status | Link |\n| :--- | :--- | :--- | :--- |\n| **Store 1** | LKR 2,450.00 | ✅ In Stock | [View on Store 1](https://store1.com/product/atomic-habits) |\n| **Store 2** | LKR 2,800.00 | ✅ In Stock | [View on Store 2](https://store2.com/product/atomic-habits) |\n\n💡 *Store 1 offers the lowest price, saving you LKR 350.00.*",
  "cypher_query": "MATCH (b:Book {normalizedTitle: 'atomic habits'})-[r:HAS_LISTING]->(s:Store) RETURN b, r, s",
  "records_count": 2,
  "suggestions": [
    "Are there other self-help books under LKR 2500?",
    "Show books by James Clear"
  ],
  "execution_time_ms": 342.15,
  "latency_breakdown": null,
  "error": null,
  "sources": [
    "Atomic Habits - Store 1 (LKR 2450.00)",
    "Atomic Habits - Store 2 (LKR 2800.00)"
  ]
}
```

---

## 🗄️ Database Graph Schema & Vector Indexes

### Graph Data Model

```cypher
(:Book {
    isbn: STRING,                // Unique constraint
    title: STRING,
    normalizedTitle: STRING,
    description: STRING,
    format: STRING,
    language: STRING,
    publisher: STRING,
    coverImage: STRING,
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

### Constraints & Indexes Created Automatically

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

Here are sample queries you can try in the Streamlit UI or via `POST /chat`:

### 1. Multi-Store Price Comparison
- *"Compare the prices of 'Harry Potter and the Philosopher's Stone' across all stores."*
- *"Where is the cheapest place to buy 'Thinking, Fast and Slow'?"*

### 2. Semantic & Thematic Book Discovery
- *"Recommend dystopian science fiction novels exploring artificial intelligence."*
- *"Find inspiring biographies of entrepreneurs and innovators."*

### 3. Budget & Filter-Based Queries
- *"Show me all available Python and Machine Learning books under LKR 3,500."*
- *"List Sinhala translation fiction books in stock."*

### 4. Conversational Follow-Ups & Memory
- Turn 1: *"Do you have books by Yuval Noah Harari?"*
- Turn 2: *"Which of those is the cheapest?"*
- Turn 3: *"Is it currently in stock?"*

---

## ❓ Troubleshooting & FAQ

### 1. `neo4j.exceptions.AuthError` or `ServiceUnavailable`
- **Cause**: Incorrect database credentials or network firewall blocking the Bolt port.
- **Fix**:
  1. Double check `NEO4J_URI`, `NEO4J_USER`, and `NEO4J_PASSWORD` in your `.env`.
  2. For Neo4j AuraDB, make sure the URI protocol is `neo4j+s://` and your instance is not paused.
  3. Ensure port `7687` is open and accessible.

### 2. `GEMINI_API_KEY is not set` or `GoogleAPICallError`
- **Cause**: Missing API key or API rate limits exceeded.
- **Fix**:
  1. Check that `GEMINI_API_KEY` is present in `.env`.
  2. Test your key at [Google AI Studio](https://aistudio.google.com/).
  3. If rate-limited, decrease `SCRAPE_WORKERS` to `2` or `4` in `.env`.

### 3. `Vector index dimension mismatch`
- **Cause**: The vector index was created with a different dimension (e.g. 768) than the current model (3072).
- **Fix**: Run a full refresh to rebuild the index with the current dimension:
  ```bash
  python backend/indexer/run_indexer.py --full-refresh --embed
  ```

### 4. Scraper returns 0 listings
- **Cause**: Website HTML layout changed.
- **Fix**:
  1. Verify target website URLs in `STORE_CONFIGS_JSON`.
  2. Ensure `cloudscraper` and `beautifulsoup4` are up to date:
     ```bash
     pip install --upgrade cloudscraper beautifulsoup4
     ```

### 5. Frontend shows "Backend Offline"
- **Cause**: The FastAPI server is not running or running on a different port.
- **Fix**: Start the backend server on `http://localhost:8000`:
  ```bash
  uvicorn backend.chat.app:app --reload --port 8000
  ```

---

## 📄 License

This is a **personal portfolio project** — all rights reserved by the author.
