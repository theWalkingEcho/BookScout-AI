"""
CoverImageService — Generalized multi-source cover image provider.
Uses Open Library Cover API and Google Books API as fallback sources
when catalog cover images are missing or broken.
"""

from typing import Optional
import re
import logging
import requests

logger = logging.getLogger(__name__)


class CoverImageService:
    """Service to fetch book cover image URLs from Open Library and Google Books API."""

    OPEN_LIBRARY_ISBN_URL = "https://covers.openlibrary.org/b/isbn/{isbn}-L.jpg?default=false"
    GOOGLE_BOOKS_API_URL = "https://www.googleapis.com/books/v1/volumes"
    
    DEFAULT_FALLBACK_SVG = (
        "data:image/svg+xml;utf8,"
        "<svg xmlns='http://www.w3.org/2000/svg' width='180' height='260' viewBox='0 0 180 260'>"
        "<rect width='100%' height='100%' fill='%231e293b' rx='10'/>"
        "<rect x='10' y='10' width='160' height='240' fill='none' stroke='%23334155' stroke-width='2' rx='6'/>"
        "<text x='90' y='120' font-family='sans-serif' font-size='32' fill='%2364748b' text-anchor='middle'>📚</text>"
        "<text x='90' y='155' font-family='sans-serif' font-size='12' fill='%2394a3b8' text-anchor='middle'>No Cover Available</text>"
        "</svg>"
    )

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) BookScout/1.0"
        })

    def fetch_cover(
        self,
        isbn: Optional[str] = None,
        title: Optional[str] = None,
        author: Optional[str] = None
    ) -> Optional[str]:
        """
        Fetch a book cover image URL from Open Library or Google Books API.
        """
        clean_isbn = re.sub(r"[^0-9X]", "", (isbn or "").upper())
        
        # 1. Try Open Library by ISBN (Direct high-res cover URL)
        if len(clean_isbn) in (10, 13):
            ol_url = f"https://covers.openlibrary.org/b/isbn/{clean_isbn}-L.jpg?default=false"
            try:
                head_res = self.session.head(ol_url, timeout=3, allow_redirects=True)
                if head_res.status_code == 200 and "image" in head_res.headers.get("Content-Type", ""):
                    return ol_url
            except Exception:
                pass

        # 2. Try Google Books API by ISBN or Title + Author
        if len(clean_isbn) in (10, 13):
            cover = self._query_google_books(f"isbn:{clean_isbn}")
            if cover:
                return cover

        if title:
            query = f"intitle:{title}"
            if author and author.lower() != "unknown":
                query += f"+inauthor:{author}"
            cover = self._query_google_books(query)
            if cover:
                return cover

        return None

    def _query_google_books(self, q: str) -> Optional[str]:
        try:
            params = {"q": q, "maxResults": 1}
            if self.api_key:
                params["key"] = self.api_key

            resp = self.session.get(self.GOOGLE_BOOKS_API_URL, params=params, timeout=4)
            if resp.status_code != 200:
                return None

            data = resp.json()
            items = data.get("items")
            if not items or not isinstance(items, list):
                return None

            volume_info = items[0].get("volumeInfo", {})
            image_links = volume_info.get("imageLinks", {})
            if not image_links:
                return None

            raw_url = (
                image_links.get("extraLarge")
                or image_links.get("large")
                or image_links.get("medium")
                or image_links.get("thumbnail")
                or image_links.get("smallThumbnail")
            )

            if raw_url:
                return raw_url.replace("http://", "https://").replace("&edge=curl", "")

        except Exception as exc:
            logger.debug("Google Books cover lookup failed for query '%s': %s", q, exc)

        return None
