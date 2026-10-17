import os

# --- database ---
DB_HOST = os.getenv("DB_HOST", "demo-db")
DB_NAME = os.getenv("DB_NAME", "payments")
DB_USER = os.getenv("DB_USER", "demo")
DB_PASSWORD = os.getenv("DB_PASSWORD", "demo")
POOL_SIZE = 5
POOL_TIMEOUT_S = 3

# --- payment processing ---
BATCH_SIZE = 100
ITEM_WORK_S = 0.002

# --- features ---
ENABLE_RECEIPT_CACHE = False
