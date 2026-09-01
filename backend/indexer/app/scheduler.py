from datetime import datetime
from typing import Iterable, List
import logging

from domain.entities import Author, Book, Category, Listing, Store
from domain.repositories import BookstoreRepository
from usecases.book_usecases import UpsertBookUseCase, DeleteListingUseCase

logger = logging.getLogger(__name__)


class WeeklyUpdateScheduler:
    def __init__(self, repository: BookstoreRepository, scraper_runner):
        self.repository = repository
        self.scraper_runner = scraper_runner
        self.upsert_book = UpsertBookUseCase(repository)
        self.delete_listing = DeleteListingUseCase(repository)
        self._current_listing_ids: List[str] = []
        self._current_isbns: List[str] = []
        self._scraped_store_names = set()
        logger.info("WeeklyUpdateScheduler initialized")

    def run_weekly(
        self, scraped_listing_data: Iterable[dict], cleanup: bool = True
    ) -> None:
        # Convert to a list ONCE up front — the original code called
        # ``len(list(scraped_listing_data))`` which silently exhausted
        # the iterable before the loop ran, so nothing was ever indexed.
        data: List[dict] = list(scraped_listing_data)
        logger.info("Running weekly update for %d listings", len(data))

        current_listing_ids: List[str] = []
        current_isbns: List[str] = []

        for raw_listing in data:
            book = Book(
                isbn=raw_listing["isbn"],
                title=raw_listing["title"],
                normalized_title=raw_listing["normalized_title"],
                format=raw_listing["format"],
                cover_image=raw_listing.get("cover_image"),
                language=raw_listing.get("language"),
                publisher=raw_listing.get("publisher"),
                description=raw_listing.get("description"),
                in_stock=raw_listing.get("in_stock", False),
                text_embedding=raw_listing.get("text_embedding"),  # Include embedding if available
            )
            author = Author(name=raw_listing["author_name"])
            category = Category(name=raw_listing["category_name"])
            store = Store(
                name=raw_listing["store_name"],
                website=raw_listing["website"],
                currency=raw_listing["currency"],
            )
            listing = Listing(
                listing_id=raw_listing["listing_id"],
                price=raw_listing["price"],
                original_price=raw_listing.get("original_price"),
                in_stock=raw_listing["in_stock"],
                url=raw_listing["url"],
                currency=raw_listing["currency"],
                last_scraped=datetime.utcnow(),
                store_name=store.name,
                book_isbn=book.isbn,
            )
            
            # Pass embedding to upsert if available
            embedding = raw_listing.get("text_embedding")
            self.upsert_book.execute(book, author, category, store, listing, embedding=embedding)
            
            logger.debug("Upserted listing %s for store %s", listing.listing_id, store.name)
            current_listing_ids.append(listing.listing_id)
            current_isbns.append(book.isbn)

        self._current_listing_ids.extend(current_listing_ids)
        self._current_isbns.extend(current_isbns)
        self._scraped_store_names.update(item["store_name"] for item in data)

        if cleanup:
            self._cleanup_removed_listings(data, current_listing_ids)
        logger.info("Finished weekly update run")

    def finish_weekly_update(self) -> None:
        """Remove listings and books not seen during the complete batched refresh."""
        data = [{"store_name": name} for name in self._scraped_store_names]
        self._cleanup_removed_listings(data, self._current_listing_ids)
        
        # Remove books not found in this scrape cycle (incremental cleanup)
        if self._current_isbns:
            logger.info("Cleaning up books not found in scrape (keeping %d ISBNs)", len(self._current_isbns))
            self.repository.delete_books_not_in_list(list(set(self._current_isbns)))
        
        self._current_listing_ids = []
        self._current_isbns = []
        self._scraped_store_names.clear()

    def _cleanup_removed_listings(
        self, data: List[dict], current_listing_ids: List[str]
    ) -> None:
        current_set = set(current_listing_ids)
        store_names = {item["store_name"] for item in data}
        for store_name in store_names:
            stored_ids = set(self.repository.list_listing_ids_for_store(store_name))
            removed = stored_ids - current_set
            if removed:
                logger.info(
                    "Store %s: removing %d stale listing(s)", store_name, len(removed)
                )
            for listing_id in removed:
                logger.debug("Deleting stale listing %s", listing_id)
                self.delete_listing.execute(listing_id)
