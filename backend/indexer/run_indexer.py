from datetime import datetime
import argparse
import logging
from typing import Dict, Any

from config import (
    NEO4J_CONFIG,
    SCRAPE_BATCH_SIZE,
    SCRAPE_LIMIT,
    SCRAPE_WORKERS,
    STORE_CONFIGS,
    GEMINI_API_KEY,
    EMBEDDING_MODEL,
)
from app.scraper import ScraperRunner, StoreScraper
from cleaner import clean_listing
from services.scheduler import WeeklyUpdateScheduler
from repositories.neo4j_bookstore_repository import Neo4jBookstoreRepository
from services.embedding_service import EmbeddingService
from services.book_service import GenerateAndStoreEmbeddingsService

logger = logging.getLogger(__name__)


def add_embedding_to_listing(
    listing: Dict[str, Any],
    embedding_service: EmbeddingService,
) -> Dict[str, Any]:
    """
    Generate and add a text embedding to a cleaned listing dict.
    
    Args:
        listing: The cleaned listing dictionary
        embedding_service: The embedding service to use
        
    Returns:
        The listing dict with text_embedding added (or None if generation fails)
    """
    try:
        title = listing.get("title", "")
        description = listing.get("description", "")
        author = listing.get("author_name", "")
        category = listing.get("category_name", "")
        
        embedding_text = embedding_service.build_embedding_text(
            title=title,
            description=description,
            authors=[author] if author else [],
            categories=[category] if category else [],
        )
        
        embedding = embedding_service.generate(embedding_text)
        if embedding:
            listing["text_embedding"] = embedding
            logger.debug("Generated embedding for book isbn=%s", listing.get("isbn", ""))
        else:
            logger.warning("Failed to generate embedding for isbn=%s title=%s", listing.get("isbn"), title)
            listing["text_embedding"] = None
    except Exception as e:
        logger.warning("Error generating embedding for isbn=%s: %s", listing.get("isbn"), e)
        listing["text_embedding"] = None
    
    return listing


def main():
    parser = argparse.ArgumentParser(description="Bookstore indexer")
    parser.add_argument(
        "--full-refresh",
        action="store_true",
        help="Clear the existing database before scraping",
    )
    parser.add_argument(
        "--embed",
        action="store_true",
        default=True,
        help="Generate and store text embeddings for books after scraping (default: True)",
    )
    parser.add_argument(
        "--no-embed",
        action="store_false",
        dest="embed",
        help="Disable generating and storing text embeddings",
    )
    parser.add_argument(
        "--check-embeddings",
        "--embed-only",
        action="store_true",
        dest="check_embeddings",
        help="Check database for books missing text embeddings and generate them without scraping",
    )
    parser.add_argument(
        "--reembed",
        action="store_true",
        help="Regenerate embeddings for ALL books (use with --embed or --check-embeddings)",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    repository = Neo4jBookstoreRepository(
        NEO4J_CONFIG["uri"],
        NEO4J_CONFIG["user"],
        NEO4J_CONFIG["password"],
        database=NEO4J_CONFIG.get("database"),
    )

    # ---------------------------------------------------------
    # Mode: Embeddings Check & Missing Embeddings Generation Only
    # ---------------------------------------------------------
    if args.check_embeddings:
        logger.info("Running in EMBEDDINGS CHECK mode (no scraping).")
        if not GEMINI_API_KEY:
            logger.error("GEMINI_API_KEY is not set. Cannot check or generate embeddings.")
            repository.close()
            return

        try:
            embedding_service = EmbeddingService(
                api_key=GEMINI_API_KEY,
                model=EMBEDDING_MODEL,
            )
            embed_service = GenerateAndStoreEmbeddingsService(
                repository=repository,
                embedding_service=embedding_service,
            )

            if args.reembed:
                logger.info("Forced re-embedding requested for all book nodes.")
                embedded_count = embed_service.execute(reembed_all=True)
                print(f"Re-embedded {embedded_count} book(s) with text vectors.")
            else:
                missing_books = list(repository.list_books_without_embedding())
                missing_count = len(missing_books)
                if missing_count == 0:
                    logger.info("All book nodes already have embeddings. No missing embeddings found.")
                    print("All book nodes already have embeddings. No missing embeddings found.")
                else:
                    logger.info("Found %d book node(s) missing embeddings. Generating embeddings...", missing_count)
                    print(f"Found {missing_count} book node(s) missing embeddings. Generating embeddings...")
                    embedded_count = embed_service.execute(reembed_all=False)
                    print(f"Successfully generated and stored embeddings for {embedded_count}/{missing_count} book(s).")
        finally:
            repository.close()
        return

    logger.info(
        "Starting bookstore indexer (scrape_limit=%s, full_refresh=%s)",
        SCRAPE_LIMIT,
        args.full_refresh,
    )

    if STORE_CONFIGS is None:
        if args.embed:
            logger.info("STORE_CONFIGS_JSON not set — running in embed-only mode (no scraping).")
            scrapers = []
        else:
            logger.error(
                "STORE_CONFIGS_JSON is not set. "
                "Set it in .env or pass --embed / --check-embeddings to run embedding only."
            )
            repository.close()
            return
    else:
        scrapers = [
            StoreScraper(store["name"], store["base_url"], store["currency"])
            for store in STORE_CONFIGS
        ]

    logger.info("Configured %d store scraper(s)", len(scrapers))

    # Initialize embedding service early if needed
    embedding_service = None
    if args.embed and GEMINI_API_KEY:
        logger.info("Initializing embedding service for during-scrape embedding generation")
        embedding_service = EmbeddingService(
            api_key=GEMINI_API_KEY,
            model=EMBEDDING_MODEL,
        )
    elif args.embed and not GEMINI_API_KEY:
        logger.warning("Embedding requested but GEMINI_API_KEY not set — embeddings will be skipped during scraping")

    scraper_runner = ScraperRunner(
        scrapers,
        max_items=SCRAPE_LIMIT,
        batch_size=SCRAPE_BATCH_SIZE,
        workers=SCRAPE_WORKERS,
    )

    scheduler = WeeklyUpdateScheduler(repository, scraper_runner)

    try:
        if args.full_refresh:
            logger.info("Full refresh enabled - clearing database")
            repository.clear_database()
        else:
            logger.info("Full refresh disabled - keeping existing database")

        logger.info("Starting scraping run for %d store(s)", len(scrapers))

        total_scraped = 0

        if scrapers:
            for scraped_batch in scraper_runner.run_batches():
                cleaned = [clean_listing(item) for item in scraped_batch]
                
                # Add embeddings to listings if embedding service is available
                if embedding_service:
                    logger.info("Generating embeddings for %d listings in batch…", len(cleaned))
                    cleaned = [add_embedding_to_listing(item, embedding_service) for item in cleaned]
                else:
                    # Mark all listings as having no embedding
                    for item in cleaned:
                        item["text_embedding"] = None

                scheduler.run_weekly(cleaned, cleanup=False)

                total_scraped += len(cleaned)

                logger.info(
                    "Persisted batch of %d listing(s)",
                    len(cleaned),
                )

            logger.info("Scraper produced %d raw listing(s)", total_scraped)

            if total_scraped == 0:
                logger.warning(
                    "No scraped listings found. Check scraper selectors and store URLs."
                )
                if not args.embed:
                    return

            else:
                scheduler.finish_weekly_update()

                logger.info(
                    "Completed update for %d listing(s)",
                    total_scraped,
                )

                print(
                    f"Completed update for {total_scraped} listings "
                    f"at {datetime.utcnow().isoformat()}."
                )
        else:
            logger.info("No scrapers configured — skipping scrape phase.")

        # --- Embedding generation (retroactive for books without embeddings) ---
        if args.embed:
            if not GEMINI_API_KEY:
                logger.warning(
                    "Retroactive embedding generation skipped: GEMINI_API_KEY is not set."
                )
            else:
                logger.info("Starting retroactive embedding generation for books without embeddings…")
                if not embedding_service:
                    embedding_service = EmbeddingService(
                        api_key=GEMINI_API_KEY,
                        model=EMBEDDING_MODEL,
                    )
                embed_service = GenerateAndStoreEmbeddingsService(
                    repository=repository,
                    embedding_service=embedding_service,
                )
                # Only re-embed if explicitly requested, otherwise just fill in missing embeddings
                embedded_count = embed_service.execute(reembed_all=args.reembed)
                print(f"Embedded {embedded_count} book(s) with text vectors.")

    except Exception:
        logger.exception("Indexer failed during execution")
        raise

    finally:
        repository.close()


if __name__ == "__main__":
    main()