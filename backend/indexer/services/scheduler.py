from datetime import datetime
from typing import Iterable, List
import logging

from models.entities import Author, Book, Category, Listing, Store
from repositories.bookstore_repository import BookstoreRepository
from services.book_service import BookService

logger = logging.getLogger(__name__)


class WeeklyUpdateScheduler:
    def __init__(self, repository: BookstoreRepository, scraper_runner):
        self.repository = repository
        self.scraper_runner = scraper_runner
        self.book_service = BookService(repository)
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
            self.book_service.upsert_book(book, author, category, store, listing, embedding=embedding)
            
            logger.debug("Upserted listing %s for store %s", listing.listing_id, store.name)
            current_listing_ids.append(listing.listing_id)
            current_isbns.append(book.isbn)

        self._current_listing_ids.extend(current_listing_ids)
        self._current_isbns.extend(current_isbns)
        self._scraped_store_names.update(item["store_name"] for item in data)

        if cleanup:
            self._cleanup_removed_listings(data, current_listing_ids)

    def _cleanup_removed_listings(self, data: List[dict], current_listing_ids: List[str]):
        """Clean up listings from *data* that no longer exist.

        For example, if a scrape update finds listings A, B, C for Store X,
        but previously Store X had listings A, B, C, D, then we delete D.
        """
        scraped_stores = {item["store_name"] for item in data}
        for store_name in scraped_stores:
            existing_ids = list(self.repository.list_listing_ids_for_store(store_name))
            to_delete = set(existing_ids) - set(current_listing_ids)
            if to_delete:
                logger.info("Cleaning up %d removed listing(s) from store %s", len(to_delete), store_name)
                for listing_id in to_delete:
                    self.book_service.delete_listing(listing_id)

    def finish_weekly_update(self) -> None:
        """Mark the end of a full (multi-store) scrape update cycle.

        Triggers cleanup of books that are not present in any store,
        to handle incremental updates correctly.
        """
        if self._current_isbns:
            logger.info("Finishing weekly update — cleaning orphaned books")
            self.repository.delete_books_not_in_list(self._current_isbns)
        else:
            logger.info("No ISBNs were tracked during this update cycle")
        # Reset tracking for next cycle
        self._current_isbns = []
        self._current_listing_ids = []
        self._scraped_store_names = set()
