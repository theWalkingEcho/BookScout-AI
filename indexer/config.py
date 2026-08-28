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
SCRAPE_LIMIT = int(os.getenv("SCRAPE_LIMIT")) if os.getenv("SCRAPE_LIMIT") else None
SCRAPE_BATCH_SIZE = int(os.getenv("SCRAPE_BATCH_SIZE", "50"))
SCRAPE_WORKERS = int(os.getenv("SCRAPE_WORKERS", "8"))

STORE_CONFIGS = [
    {
        "name": "Book Bazaar LK",
        # NOTE: removed the trailing '#' fragment – it prevents the crawler
        # from leaving the landing page entirely.
        "base_url": "https://bookbazaarlk.com/shop/",
        "currency": "LKR",
    },
    {
        "name": "Jump Books LK",
        "base_url": "https://jumpbooks.lk",
        "currency": "LKR",
    },
    # TODO: add more Sri Lankan bookstores below
    {
        "name": "Vijitha Yapa",
        "base_url": "https://www.vijithayapa.com/shop/",
        "currency": "LKR",
    },
    {
        "name": "Sarasavi Bookshop",
        "base_url": "https://www.sarasavi.lk",
        "currency": "LKR",
    },
    {
        "name": "Makeen Books",
        "base_url": "https://makeenbooks.com",
        "currency": "LKR",
    },
]
