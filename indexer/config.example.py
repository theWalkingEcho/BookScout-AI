NEO4J_CONFIG = {
    "uri": "YOUR_NEO4J_URI",
    "user": "YOUR_NEO4J_USER",
    "password": "YOUR_NEO4J_PASSWORD",
    "database": "YOUR_NEO4J_DATABASE",
}

# Maximum number of product pages to scrape per run.
# Set to None to scrape everything found (may be slow).
SCRAPE_LIMIT = 50

STORE_CONFIGS = [
    {
        "name": "Example Store",
        "base_url": "YOUR_STORE_URL",
        "currency": "YOUR_CURRENCY",
    },
    {
            "name": "Example Store",
            "base_url": "YOUR_STORE_URL",
            "currency": "YOUR_CURRENCY",
    },
]
