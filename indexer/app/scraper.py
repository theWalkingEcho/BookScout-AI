"""
StoreScraper — deep BFS and sitemap web scraper for Sri Lankan bookstores.

Key design decisions
--------------------
* **Sitemap / Next.js and BFS discovery** — stores with XML sitemaps
  (such as Sarasavi Bookshop) discover product links directly via sitemaps
  to avoid slow or timing-out SSR category pages. Other stores use BFS
  crawling seeded from category navigation.
* **Link classification** — URLs are classified as ``product``,
  ``category``, ``pagination``, or ``ignore`` to prevent crawling
  unrelated pages, shopping carts, or Cloudflare protection endpoints.
* **Rich metadata extraction** — supports structured ``__NEXT_DATA__``
  JSON-LD, meta tags, specification tables (e.g. Vijitha Yapa, Makeen),
  and WooCommerce layouts.
* **Plausibility guards** — author names, ISBNs, and prices are validated
  and sanitized.
"""

from __future__ import annotations

import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.parse import urldefrag, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Author plausibility helpers
# ---------------------------------------------------------------------------

_CSS_CHARS_RE = re.compile(r"[{};@]|:root|\.bsdg-|\bpx\b|\brem\b", re.IGNORECASE)
_LETTERS_RE = re.compile(r"[a-zA-Z\u0D80-\u0DFF\u0B80-\u0BFF]")


def _clean_author_candidate(candidate: str) -> str:
    """Clean prefix labels, colons, and hyphens from author strings."""
    if not candidate:
        return ""
    cleaned = re.sub(r"^\s*(?:Author|By)\s*[:\-–—]?\s*", "", candidate, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"^[:\-–—\s]+", "", cleaned).strip()
    return cleaned


def _is_plausible_author(text: str) -> bool:
    """Return True only if *text* looks like a real author name, not CSS or punctuation."""
    if not text or len(text) > 120:
        return False
    # Must contain at least 2 alphabetic characters
    if len(_LETTERS_RE.findall(text)) < 2:
        return False
    if _CSS_CHARS_RE.search(text):
        return False
    # All-uppercase strings of > 3 words are more likely a heading/label
    words = text.split()
    if len(words) > 3 and text == text.upper():
        return False
    return True


# ---------------------------------------------------------------------------
# StoreScraper
# ---------------------------------------------------------------------------


class StoreScraper:
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    )
    DEFAULT_HEADERS = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    MAX_CRAWL_PAGES = 500
    MAX_PRODUCT_LINKS = 1000
    # HTTP back-off: retry once after this many seconds on 429/503
    RETRY_DELAY = 5.0

    def __init__(
        self,
        store_name: str,
        base_url: str,
        currency: str,
        request_delay: float = 0.2,
    ):
        self.store_name = store_name
        self.base_url = base_url
        self.currency = currency
        self.request_delay = request_delay

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def scrape_listing_links(self, max_links: Optional[int] = None) -> List[Tuple[str, str]]:
        """Collect ``(product_url, category)`` pairs using sitemaps or BFS."""
        limit = max_links if max_links is not None else self.MAX_PRODUCT_LINKS
        logger.info("Scraping listing links from %s (target limit=%d)", self.base_url, limit)

        # 1. Specialized sitemap discovery for stores like Sarasavi
        if "sarasavi.lk" in self.base_url.lower():
            sitemap_links = self._scrape_sarasavi_links(max_links=limit)
            if sitemap_links:
                logger.info(
                    "Discovered %d product links from Sarasavi sitemaps",
                    len(sitemap_links),
                )
                return sitemap_links[:limit]

        visited: set = set()
        product_url_map: Dict[str, str] = {}
        base_netloc = urlparse(self.base_url).netloc

        # ----------------------------------------------------------------
        # Step 1: fetch the base page and seed the BFS from sidebar or navigation
        # ----------------------------------------------------------------
        try:
            base_soup = self._fetch_soup(self.base_url)
        except Exception as exc:
            logger.error("Failed to fetch base URL %s: %s", self.base_url, exc)
            return []

        visited.add(self.base_url)

        # Extract any product links directly on the base page
        for anchor in base_soup.select("a[href]"):
            raw_href = anchor.get("href", "")
            link_url = self._normalize_link(raw_href, self.base_url)
            if not link_url or urlparse(link_url).netloc != base_netloc:
                continue
            if self._classify_link(link_url, anchor) == "product":
                if link_url not in product_url_map:
                    product_url_map[link_url] = self.store_name
                    if len(product_url_map) >= limit:
                        return list(product_url_map.items())

        sidebar_categories = self._extract_sidebar_categories(base_soup)

        if sidebar_categories:
            logger.info(
                "Seeding BFS from %d sidebar categories", len(sidebar_categories)
            )
            queue: List[Tuple[str, str]] = [
                (self._normalize_link(url, self.base_url), name)
                for name, url in sidebar_categories
                if self._normalize_link(url, self.base_url)
                and urlparse(self._normalize_link(url, self.base_url)).netloc == base_netloc
            ]
        else:
            # Fallback: start from base_url with menu / category links
            logger.info(
                "No sidebar categories found on %s — extracting menu/category links",
                self.base_url,
            )
            queue = []
            for anchor in base_soup.select("a[href]"):
                href = anchor.get("href", "")
                link_url = self._normalize_link(href, self.base_url)
                if not link_url or urlparse(link_url).netloc != base_netloc:
                    continue
                link_type = self._classify_link(link_url, anchor)
                if link_type in ("category", "pagination") and self._should_follow_link(link_url, visited):
                    queue.append((link_url, anchor.get_text(strip=True) or ""))

            if not queue:
                visited.discard(self.base_url)
                queue = [(self.base_url, "")]

        # ----------------------------------------------------------------
        # Step 2: BFS
        # ----------------------------------------------------------------
        pages_crawled = 0
        while (
            queue
            and len(visited) < self.MAX_CRAWL_PAGES
            and len(product_url_map) < limit
        ):
            page_url, page_category = queue.pop(0)
            if page_url in visited:
                continue
            visited.add(page_url)
            pages_crawled += 1

            try:
                soup = self._fetch_soup(page_url)
            except Exception as exc:
                logger.warning("Failed to fetch page %s: %s", page_url, exc)
                continue

            # Inspect every anchor on the page
            for anchor in soup.select("a[href]"):
                raw_href = anchor.get("href", "")
                link_url = self._normalize_link(raw_href, page_url)
                if not link_url:
                    continue

                parsed = urlparse(link_url)
                # Stay on the same domain
                if parsed.netloc != base_netloc:
                    continue

                link_type = self._classify_link(link_url, anchor)

                if link_type == "product":
                    current_category = page_category or self.store_name
                    if link_url not in product_url_map:
                        product_url_map[link_url] = current_category
                        if len(product_url_map) >= limit:
                            break
                    elif not product_url_map[link_url] and current_category:
                        product_url_map[link_url] = current_category

                elif link_type in ("category", "pagination"):
                    if self._should_follow_link(link_url, visited):
                        cat_name = anchor.get_text(strip=True) if link_type == "category" else page_category
                        queue.append((link_url, cat_name or page_category))

            if pages_crawled % 10 == 0 or len(product_url_map) >= limit:
                logger.info(
                    "Crawl progress on %s: %d pages visited, %d products found",
                    self.base_url,
                    pages_crawled,
                    len(product_url_map),
                )

        logger.info(
            "Found %d product links on %s (visited %d pages)",
            len(product_url_map),
            self.base_url,
            len(visited),
        )
        return list(product_url_map.items())[:limit]

    def _scrape_sarasavi_links(self, max_links: Optional[int] = None) -> List[Tuple[str, str]]:
        """Extract product links directly from Sarasavi sitemaps and homepage Next.js data."""
        limit = max_links if max_links is not None else self.MAX_PRODUCT_LINKS
        product_urls: List[Tuple[str, str]] = []
        seen = set()

        # 1. Check homepage __NEXT_DATA__ for featured/new/bestseller books
        try:
            home_soup = self._fetch_soup(self.base_url)
            nd = home_soup.find("script", id="__NEXT_DATA__")
            if nd and nd.string:
                data = json.loads(nd.string)
                page_props = data.get("props", {}).get("pageProps", {})
                for key in ["iniDataNew", "iniDataFeatured", "iniDataBestSellers"]:
                    items = page_props.get(key, {}).get("data", [])
                    if isinstance(items, list):
                        for item in items:
                            slug = item.get("slug")
                            if slug:
                                full_url = f"https://www.sarasavi.lk/product/{slug}"
                                if full_url not in seen:
                                    seen.add(full_url)
                                    product_urls.append((full_url, "Sarasavi Bookshop"))
                                    if len(product_urls) >= limit:
                                        return product_urls
        except Exception as exc:
            logger.debug("Failed to extract Sarasavi homepage Next.js data: %s", exc)

        # 2. Check XML sitemaps
        try:
            sitemap_url = "https://www.sarasavi.lk/sitemap.xml"
            resp = self._get_with_retry(sitemap_url)
            root = ET.fromstring(resp.content)
            locs = [
                el.text.strip()
                for el in root.findall(".//{http://www.sitemaps.org/schemas/sitemap/0.9}loc")
                if el.text
            ]
            product_sitemaps = [loc for loc in locs if "product" in loc]

            # Process product sitemaps in reverse (most recent first)
            for sm in reversed(product_sitemaps):
                if len(product_urls) >= limit:
                    break
                try:
                    sm_resp = self._get_with_retry(sm)
                    sm_root = ET.fromstring(sm_resp.content)
                    for loc_el in sm_root.findall(".//{http://www.sitemaps.org/schemas/sitemap/0.9}loc"):
                        url = loc_el.text.strip() if loc_el.text else ""
                        if url and url not in seen and "/product/" in url:
                            seen.add(url)
                            product_urls.append((url, "Sarasavi Bookshop"))
                            if len(product_urls) >= limit:
                                break
                except Exception as exc:
                    logger.warning("Failed to fetch/parse sitemap %s: %s", sm, exc)
        except Exception as exc:
            logger.warning("Failed to fetch Sarasavi sitemap index: %s", exc)

        return product_urls[:limit]

    def scrape_book_data(self, product_url: str, category_hint: str = "") -> Dict:
        logger.info("Scraping book page: %s", product_url)
        soup = self._fetch_soup(product_url)

        # Check for Next.js structured data first (e.g. Sarasavi)
        next_data_book = self._parse_next_data(soup, product_url)
        if next_data_book:
            return next_data_book

        title_text = self._find_title(soup)
        price_el = self._find_current_price_element(soup)
        original_price_el = self._find_original_price_element(soup)
        in_stock = self._parse_availability(soup)

        raw_isbn = self._read_isbn(soup)
        isbn = (
            raw_isbn
            if raw_isbn and raw_isbn.lower() != "unknown"
            else self._generate_book_id(product_url, title_text)
        )

        author = self._read_author(soup)
        category = self._read_category(soup) or category_hint or self.store_name
        cover_image = self._read_cover_image(soup)
        language = self._read_language(soup)

        normalized_title = title_text.lower().strip() if title_text else ""
        listing_id = (
            f"{self.store_name.lower().replace(' ', '_')}_"
            f"{re.sub(r'[^a-zA-Z0-9]+', '_', product_url)}"
        )

        logger.debug(
            "Parsed product %s | title=%s isbn=%s author=%s",
            product_url,
            title_text,
            isbn,
            author,
        )

        return {
            "isbn": isbn,
            "title": title_text,
            "normalized_title": normalized_title,
            "format": "Paperback",
            "cover_image": cover_image,
            "language": language,
            "author_name": author,
            "category_name": category,
            "listing_id": listing_id,
            "price": self._parse_price(price_el.text if price_el else ""),
            "original_price": self._parse_price(original_price_el.text if original_price_el else ""),
            "in_stock": in_stock,
            "url": product_url,
            "store_name": self.store_name,
            "website": self.base_url,
            "currency": self.currency,
        }

    def _parse_next_data(self, soup: BeautifulSoup, product_url: str) -> Optional[Dict]:
        """Extract structured book metadata from __NEXT_DATA__ script if present."""
        nd_el = soup.find("script", id="__NEXT_DATA__")
        if not nd_el or not nd_el.string:
            return None
        try:
            data = json.loads(nd_el.string)
            prod = data.get("props", {}).get("pageProps", {}).get("iniData")
            if not prod or not isinstance(prod, dict) or not prod.get("name"):
                return None

            title = prod.get("name", "").strip()
            isbn = str(prod.get("isbn13") or prod.get("isbn") or "").replace("-", "").strip()
            if not isbn:
                isbn = self._generate_book_id(product_url, title)

            author_obj = prod.get("author")
            author_name = "Unknown"
            if isinstance(author_obj, dict):
                author_name = author_obj.get("name", "Unknown").strip()
            elif isinstance(author_obj, str):
                author_name = author_obj.strip()

            cat_obj = prod.get("category")
            category_name = self.store_name
            if isinstance(cat_obj, dict):
                category_name = cat_obj.get("name", self.store_name).strip()

            lang_obj = prod.get("language")
            language = None
            if isinstance(lang_obj, dict):
                language = lang_obj.get("language")
            elif isinstance(lang_obj, str):
                language = lang_obj

            image_path = prod.get("image", "")
            if image_path:
                cover_image = (
                    image_path
                    if image_path.startswith("http")
                    else f"https://cms.sarasavi.lk/storage/{image_path.lstrip('/')}"
                )
            else:
                cover_image = ""

            amount = float(prod.get("amount") or 0.0)
            promo = prod.get("promotion") or {}
            discounted = promo.get("discounted_price")
            price = float(discounted) if discounted is not None else amount
            original_price = amount if (discounted is not None and amount > price) else 0.0

            stock = prod.get("stock", 0)
            in_stock = stock > 0 if isinstance(stock, (int, float)) else True

            listing_id = (
                f"{self.store_name.lower().replace(' ', '_')}_"
                f"{re.sub(r'[^a-zA-Z0-9]+', '_', product_url)}"
            )

            return {
                "isbn": isbn,
                "title": title,
                "normalized_title": title.lower(),
                "format": "Paperback",
                "cover_image": cover_image,
                "language": language,
                "author_name": author_name or "Unknown",
                "category_name": category_name,
                "listing_id": listing_id,
                "price": price,
                "original_price": original_price,
                "in_stock": in_stock,
                "url": product_url,
                "store_name": self.store_name,
                "website": self.base_url,
                "currency": self.currency,
            }
        except Exception as exc:
            logger.debug("Failed to parse __NEXT_DATA__ on %s: %s", product_url, exc)
            return None

    # ------------------------------------------------------------------
    # Sidebar category seed
    # ------------------------------------------------------------------

    def _extract_sidebar_categories(self, soup: BeautifulSoup) -> List[Tuple[str, str]]:
        """Return ``[(category_name, url), ...]`` from category navigation or sidebar."""
        results: List[Tuple[str, str]] = []
        widget = soup.select_one(".widget_product_categories")
        if not widget:
            widget = soup.select_one("ul.product-categories")

        if widget:
            for anchor in widget.select("li.cat-item > a, a"):
                href = anchor.get("href", "").strip()
                name = re.sub(r"\s*\(\d+\)\s*$", "", anchor.get_text(strip=True))
                if href and name:
                    results.append((name, href))

        logger.info(
            "Extracted %d sidebar categories from %s", len(results), self.base_url
        )
        return results

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------

    def _fetch_soup(self, url: str) -> BeautifulSoup:
        response = self._get_with_retry(url)
        time.sleep(self.request_delay)
        return BeautifulSoup(response.text, "html.parser")

    def _get_with_retry(self, url: str, retries: int = 2) -> requests.Response:
        for attempt in range(retries + 1):
            try:
                response = requests.get(
                    url,
                    headers=self.DEFAULT_HEADERS,
                    timeout=25,
                )
                if response.status_code in (429, 503) and attempt < retries:
                    logger.warning(
                        "HTTP %d on %s — backing off %.0fs",
                        response.status_code,
                        url,
                        self.RETRY_DELAY,
                    )
                    time.sleep(self.RETRY_DELAY)
                    continue
                response.raise_for_status()
                return response
            except requests.HTTPError as exc:
                if exc.response is not None and exc.response.status_code in (429, 503):
                    if attempt < retries:
                        time.sleep(self.RETRY_DELAY)
                        continue
                raise
            except (requests.ConnectionError, requests.Timeout):
                if attempt < retries:
                    time.sleep(self.RETRY_DELAY)
                else:
                    raise

    # ------------------------------------------------------------------
    # Link classification
    # ------------------------------------------------------------------

    def _normalize_link(self, href: str, current_url: str) -> str:
        if not href or href.startswith(("#", "mailto:", "javascript:", "tel:")):
            return ""
        link, _fragment = urldefrag(urljoin(current_url, href))
        return link

    def _classify_link(self, url: str, anchor) -> str:
        """Classify a URL as 'product', 'category', 'pagination', or 'ignore'."""
        path = urlparse(url).path.lower()
        url_lower = url.lower()
        anchor_text = (anchor.text or "").strip().lower()
        anchor_classes = anchor.get("class", [])

        # --- Explicit exclusions ---
        if any(
            kw in url_lower
            for kw in [
                "/cdn-cgi/",
                "cdn-cgi",
                "/cart",
                "/add-to-cart",
                "add-to-cart",
                "/wishlist",
                "/checkout",
                "/my-account",
                "/compare",
                "/wp-admin",
                "/wp-login",
                "/privacy",
                "/terms",
                "/contact",
                "/about",
                "/careers",
                "/refund",
                "/returns",
                "/delivery",
                "/facebook.com",
                "/instagram.com",
                "/linkedin.com",
                "/twitter.com",
                "/youtube.com",
            ]
        ):
            return "ignore"

        if any(
            kw in anchor_text
            for kw in ["add to cart", "add to wishlist", "compare", "buy now", "checkout"]
        ):
            return "ignore"

        # --- Category / pagination signals (checked before product signals) ---
        if (
            "/product-category/" in path
            or "/categarey/" in path
            or "/category/" in path
            or "/genre/" in path
            or "/section/" in path
            or "/collection/" in path
            or "-books-sri-lanka/" in path
        ):
            return "category"

        # --- Pagination signals (checked before /shop/ depth) ---
        query = urlparse(url).query.lower()
        if (
            re.search(r"\bpage=\d+\b|\bpaged=\d+\b", query)
            or re.search(r"/page/\d+", path)
            or any(kw in anchor_text for kw in ["next", "previous", "›", "»", "‹", "«", "older", "newer"])
        ):
            return "pagination"

        # --- Product signals ---
        if any(seg in path for seg in ["/product/", "/app/book/", "/book/", "/books/"]):
            after = re.split(r"/product/|/app/book/|/book/|/books/", path, maxsplit=1)
            if len(after) > 1 and after[-1].strip("/"):
                return "product"

        # /shop/<category>/<product-slug> — three path segments after /shop/
        if "/shop/" in path:
            segments = [s for s in path.split("/") if s]
            shop_idx = next((i for i, s in enumerate(segments) if s == "shop"), None)
            if shop_idx is not None:
                depth = len(segments) - shop_idx - 1
                if depth == 1:
                    return "category"
                if depth >= 2 and segments[shop_idx + 1] != "page":
                    return "product"

        if any(
            cls
            for cls in anchor_classes
            if any(kw in cls.lower() for kw in ["product-link", "book-link", "woocommerce-loop-product__link"])
        ):
            return "product"

        return "ignore"

    def _should_follow_link(self, url: str, visited: set) -> bool:
        if url in visited:
            return False
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return False
        if any(
            parsed.path.lower().endswith(ext)
            for ext in [".jpg", ".jpeg", ".png", ".gif", ".css", ".js", ".pdf", ".xml", ".zip", ".svg"]
        ):
            return False
        if any(
            kw in parsed.path.lower()
            for kw in [
                "/cdn-cgi/",
                "/cart",
                "/add-to-cart",
                "/wishlist",
                "/checkout",
                "/my-account",
                "/wp-admin",
                "/wp-login",
            ]
        ):
            return False
        return True

    # ------------------------------------------------------------------
    # Content extraction
    # ------------------------------------------------------------------

    def _find_title(self, soup: BeautifulSoup) -> str:
        for selector in [
            "h1.product-title",
            "h1.title",
            "h1.entry-title",
            ".product-title",
            ".product_title",
            "h1",
        ]:
            el = soup.select_one(selector)
            if el and el.text.strip():
                return el.text.strip()
        return ""

    def _parse_availability(self, soup: BeautifulSoup) -> bool:
        status = soup.select_one(
            ".in-stock, .stock-status, .availability, .product-stock, p.stock"
        )
        if not status:
            return True
        text = status.text.strip().lower()
        return "out of stock" not in text and "sold out" not in text

    def _parse_price(self, text: str) -> float:
        if not text:
            return 0.0
        cleaned = re.sub(r"[^0-9.,]+", " ", text)
        matches = re.findall(r"\d{1,3}(?:[.,]\d{3})*(?:[.,]\d+)?", cleaned)
        if not matches:
            logger.debug("Could not parse price from: %s", text)
            return 0.0
        price_str = matches[-1].replace(",", "")
        try:
            return float(price_str)
        except ValueError:
            return 0.0

    def _read_isbn(self, soup: BeautifulSoup) -> str:
        # 1. Spec list items (e.g. Vijitha Yapa)
        for li in soup.select("ul.product-spec-list li, .product_meta li, .product_meta span"):
            text = li.get_text(" ", strip=True)
            if "isbn" in text.lower():
                val = re.sub(r"^.*?ISBN\s*[:\-]?\s*", "", text, flags=re.IGNORECASE).strip()
                val = val.split()[0] if val else ""
                digits = re.sub(r"[^0-9X]", "", val.upper())
                if len(digits) in (10, 13):
                    return digits

        # 2. WooCommerce product meta SKU / ISBN label
        for label_el in soup.find_all(text=lambda t: t and "ISBN" in t):
            if label_el.parent and label_el.parent.name in ["title", "script", "style", "meta"]:
                continue
            text = label_el.strip()
            match = re.search(r"(?:97[89][\-\s]?\d{1,5}[\-\s]?\d{1,7}[\-\s]?\d{1,7}[\-\s]?\d|[0-9X]{10})", text)
            if match:
                return match.group(0).replace(" ", "").replace("-", "")

        # 3. Search in description / attributes
        desc_el = soup.select_one(".description, .woocommerce-product-details__short-description, .product-spec-list")
        if desc_el:
            text = desc_el.get_text(" ")
            match = re.search(r"\b(97[89]\d{10}|\d{9}[\dX])\b", text.replace("-", ""))
            if match:
                return match.group(1)

        return ""

    def _generate_book_id(self, product_url: str, title_text: str) -> str:
        source = title_text or product_url
        slug = re.sub(r"[^a-zA-Z0-9]+", "_", source).strip("_").lower()
        if not slug:
            slug = re.sub(r"[^a-zA-Z0-9]+", "_", product_url).strip("_").lower()
        return f"{self.store_name.lower().replace(' ', '_')}_book_{slug}"

    # ------------------------------------------------------------------
    # Author extraction (priority order)
    # ------------------------------------------------------------------

    def _read_author(self, soup: BeautifulSoup) -> str:
        # 1. Spec list items (e.g. Vijitha Yapa <li><span>Author :</span>Name</li>)
        for li in soup.select("ul.product-spec-list li, .product-attributes li"):
            text = li.get_text(" ", strip=True)
            if "author" in text.lower():
                candidate = _clean_author_candidate(re.sub(r"^.*?Author\s*[:\-]?\s*", "", text, flags=re.IGNORECASE))
                if _is_plausible_author(candidate):
                    return candidate

        # 2. JSON-LD schema.org
        author = self._author_from_jsonld(soup)
        if author:
            return author

        # 3. <meta> tags
        author = self._author_from_meta(soup)
        if author:
            return author

        # 4. WooCommerce product-meta table (<th> / <td> pairs)
        author = self._author_from_product_table(soup)
        if author:
            return author

        # 5. rel=author anchor
        link = soup.find("a", rel=lambda r: r and "author" in r)
        if link and link.text.strip():
            candidate = _clean_author_candidate(link.text.strip())
            if _is_plausible_author(candidate):
                return candidate

        # 6. CSS-class selectors
        for selector in [".author-name", ".product-author", ".by-author", ".author"]:
            el = soup.select_one(selector)
            if el:
                inner_a = el.select_one("a")
                candidate = (inner_a or el).get_text(" ", strip=True)
                candidate = _clean_author_candidate(candidate)
                if _is_plausible_author(candidate):
                    return candidate

        # 7. Plain-text label scan
        for tag in soup.find_all(["p", "td", "span", "div", "li"], limit=300):
            text = tag.get_text(" ", strip=True)
            match = re.match(
                r"^(?:Author|By)\s*[:\-]?\s*(.+)$", text, flags=re.IGNORECASE
            )
            if match:
                candidate = _clean_author_candidate(match.group(1))
                if _is_plausible_author(candidate):
                    return candidate

        return "Unknown"

    def _author_from_jsonld(self, soup: BeautifulSoup) -> Optional[str]:
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
            except (json.JSONDecodeError, AttributeError):
                continue
            items = data if isinstance(data, list) else [data]
            for item in items:
                if "@graph" in item:
                    items += item["@graph"]
                author_field = item.get("author")
                if not author_field:
                    continue
                if isinstance(author_field, dict):
                    name = author_field.get("name", "")
                elif isinstance(author_field, list):
                    name = ", ".join(
                        a.get("name", "") if isinstance(a, dict) else str(a)
                        for a in author_field
                        if a
                    )
                else:
                    name = str(author_field)
                candidate = _clean_author_candidate(name)
                if _is_plausible_author(candidate):
                    return candidate
        return None

    def _author_from_meta(self, soup: BeautifulSoup) -> Optional[str]:
        for attrs in [
            {"name": "author"},
            {"property": "og:book:author"},
            {"property": "books:author"},
        ]:
            tag = soup.find("meta", attrs=attrs)
            if tag and tag.get("content", "").strip():
                candidate = _clean_author_candidate(tag["content"].strip())
                if _is_plausible_author(candidate):
                    return candidate
        return None

    def _author_from_product_table(self, soup: BeautifulSoup) -> Optional[str]:
        """Extract author from WooCommerce-style definition / table rows."""
        for th in soup.find_all("th"):
            if re.search(r"\bauthor\b", th.get_text(), re.IGNORECASE):
                td = th.find_next_sibling("td")
                if td:
                    candidate = _clean_author_candidate(td.get_text(" ", strip=True))
                    if _is_plausible_author(candidate):
                        return candidate

        meta_el = soup.select_one(".product_meta")
        if meta_el:
            for span in meta_el.find_all(["span", "p"]):
                text = span.get_text(" ", strip=True)
                m = re.match(
                    r"^(?:Author|By)\s*[:\-]?\s*(.+)$", text, flags=re.IGNORECASE
                )
                if m:
                    candidate = _clean_author_candidate(m.group(1))
                    if _is_plausible_author(candidate):
                        return candidate
        return None

    # ------------------------------------------------------------------
    # Category extraction
    # ------------------------------------------------------------------

    def _read_category(self, soup: BeautifulSoup) -> str:
        meta_el = soup.select_one(".product_meta")
        if meta_el:
            raw = meta_el.get_text(separator=" ")
            match = re.search(r"Categories?\s*:\s*(.+)", raw, re.I)
            if match:
                cats = [c.strip() for c in match.group(1).split(",") if c.strip()]
                if cats:
                    return cats[0]

        breadcrumb_items = [
            el.text.strip()
            for el in soup.select(
                ".woocommerce-breadcrumb a, .woocommerce-breadcrumb span,"
                ".breadcrumb a, .breadcrumb span"
            )
            if el.text.strip()
        ]
        if breadcrumb_items and breadcrumb_items[0].lower() == "home":
            breadcrumb_items = breadcrumb_items[1:]
        if len(breadcrumb_items) >= 2:
            return breadcrumb_items[-2]
        if breadcrumb_items:
            return breadcrumb_items[-1]

        og = soup.select_one("meta[property='article:section']")
        if og and og.get("content", "").strip():
            return og["content"].strip()

        return self.store_name

    def _read_cover_image(self, soup: BeautifulSoup) -> str:
        for selector in [
            "img.wp-post-image",
            "img.attachment-shop_single",
            ".woocommerce-product-gallery img",
            "img.cover",
            "img.product-image",
        ]:
            img = soup.select_one(selector)
            if img and img.get("src", ""):
                return img["src"]

        og = soup.select_one("meta[property='og:image']")
        if og and og.get("content", ""):
            return og["content"]
        return ""

    def _find_current_price_element(self, soup: BeautifulSoup):
        for selector in [
            ".price ins .woocommerce-Price-amount.amount",
            ".price ins .amount",
            ".price > .woocommerce-Price-amount.amount",
            ".price > .amount",
            ".price span.woocommerce-Price-amount.amount",
            ".price .amount",
            ".ppb-amount",
            ".price.product-page-price",
            ".product-price",
        ]:
            el = soup.select_one(selector)
            if el and el.text.strip():
                return el
        return None

    def _find_original_price_element(self, soup: BeautifulSoup):
        for selector in [
            ".price del .woocommerce-Price-amount.amount",
            ".price del .amount",
            ".price-old",
            ".original-price",
            ".product-old-price",
        ]:
            el = soup.select_one(selector)
            if el and el.text.strip():
                return el
        return None

    def _read_language(self, soup: BeautifulSoup) -> Optional[str]:
        # 1. Spec list items (e.g. Vijitha Yapa)
        for li in soup.select("ul.product-spec-list li, .product-attributes li"):
            text = li.get_text(" ", strip=True)
            if "language" in text.lower():
                val = re.sub(r"^.*?Language\s*[:\-]?\s*", "", text, flags=re.IGNORECASE).strip()
                if val:
                    return val

        # 2. WooCommerce attributes table
        for row in soup.select(".woocommerce-product-attributes tr"):
            th = row.find("th")
            if th and "language" in th.get_text(strip=True).lower():
                td = row.find("td")
                if td:
                    return td.get_text(strip=True)

        # 3. Product meta
        meta_el = soup.select_one(".product_meta")
        if meta_el:
            for span in meta_el.find_all(["span", "p"]):
                text = span.get_text(" ", strip=True)
                m = re.match(r"^(?:Language)\s*[:\-]?\s*(.+)$", text, flags=re.IGNORECASE)
                if m:
                    return m.group(1).strip()

        return None


# ---------------------------------------------------------------------------
# ScraperRunner
# ---------------------------------------------------------------------------


class ScraperRunner:
    def __init__(
        self,
        scrapers: Iterable[StoreScraper],
        max_items: Optional[int] = None,
        batch_size: int = 50,
        workers: int = 8,
    ):
        self.scrapers = list(scrapers)
        self.max_items = max_items
        self.batch_size = max(1, batch_size)
        self.workers = max(1, workers)

    def run(self) -> Iterable[dict]:
        for batch in self.run_batches():
            yield from batch

    def run_batches(self) -> Iterable[List[dict]]:
        logger.info(
            "Starting ScraperRunner with %d scrapers (max_items=%s, batch_size=%d, workers=%d)",
            len(self.scrapers),
            self.max_items,
            self.batch_size,
            self.workers,
        )
        scraped_count = 0
        batch: List[Tuple[StoreScraper, str, str]] = []
        for scraper in self.scrapers:
            logger.info("Running scraper for store: %s", scraper.store_name)
            # Pass max_links to scraper to avoid unnecessary crawl overhead
            remaining_limit = (self.max_items - scraped_count) if self.max_items is not None else None
            for url, category_hint in scraper.scrape_listing_links(max_links=remaining_limit):
                if self.max_items is not None and scraped_count >= self.max_items:
                    logger.info("Reached scrape limit of %d — stopping.", self.max_items)
                    if batch:
                        scraped_batch = self._scrape_batch(batch)
                        if scraped_batch:
                            yield scraped_batch
                    return

                batch.append((scraper, url, category_hint))
                if len(batch) >= self.batch_size:
                    scraped_batch = self._scrape_batch(batch)
                    scraped_count += len(scraped_batch)
                    if scraped_batch:
                        yield scraped_batch
                    batch = []

        if batch:
            scraped_batch = self._scrape_batch(batch)
            if scraped_batch:
                yield scraped_batch
                scraped_count += len(scraped_batch)
        logger.info("ScraperRunner finished. Total scraped: %d", scraped_count)

    def _scrape_batch(self, batch: List[Tuple[StoreScraper, str, str]]) -> List[dict]:
        scraped: List[dict] = []
        with ThreadPoolExecutor(max_workers=self.workers) as executor:
            futures = {
                executor.submit(scraper.scrape_book_data, url, category_hint): url
                for scraper, url, category_hint in batch
            }
            for future in as_completed(futures):
                url = futures[future]
                try:
                    scraped.append(future.result())
                except Exception as exc:
                    logger.warning("Failed to scrape %s: %s", url, exc)
        logger.info("Completed scrape batch: %d/%d listing(s)", len(scraped), len(batch))
        return scraped
