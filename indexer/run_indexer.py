from datetime import datetime
import argparse
import logging

from config import (
    NEO4J_CONFIG,
    SCRAPE_BATCH_SIZE,
    SCRAPE_LIMIT,
    SCRAPE_WORKERS,
    STORE_CONFIGS,
)
from app.scraper import ScraperRunner, StoreScraper
from cleaner import clean_listing
from app.scheduler import WeeklyUpdateScheduler
from infra.neo4j_repository import Neo4jBookstoreRepository

logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Bookstore indexer")
    parser.add_argument(
        "--full-refresh",
        action="store_true",
        help="Clear the existing database before scraping",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    logger.info(
        "Starting bookstore indexer (scrape_limit=%s, full_refresh=%s)",
        SCRAPE_LIMIT,
        args.full_refresh,
    )

    repository = Neo4jBookstoreRepository(
        NEO4J_CONFIG["uri"],
        NEO4J_CONFIG["user"],
        NEO4J_CONFIG["password"],
        database=NEO4J_CONFIG.get("database"),
    )

    scrapers = [
        StoreScraper(store["name"], store["base_url"], store["currency"])
        for store in STORE_CONFIGS
    ]

    logger.info("Configured %d store scraper(s)", len(scrapers))

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

        for scraped_batch in scraper_runner.run_batches():
            cleaned = [clean_listing(item) for item in scraped_batch]

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
            return

        scheduler.finish_weekly_update()

        logger.info(
            "Completed update for %d listing(s)",
            total_scraped,
        )

        print(
            f"Completed update for {total_scraped} listings "
            f"at {datetime.utcnow().isoformat()}."
        )

    except Exception:
        logger.exception("Indexer failed during execution")
        raise

    finally:
        repository.close()


if __name__ == "__main__":
    main()