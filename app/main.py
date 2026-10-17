import json
import logging
import os
import sys
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import psycopg
from fastapi import FastAPI, HTTPException, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool, PoolTimeout
from pydantic import BaseModel, Field

from app import config

SERVICE = "payment-service"
VERSION = os.getenv("GIT_SHA", "dev")


# ---------------------------------------------------------------- logging (one JSON object per line)
class JsonFormatter(logging.Formatter):
    def format(self, record):
        data = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "service": SERVICE,
            "version": VERSION,
            "msg": record.getMessage(),
        }
        data.update(getattr(record, "fields", {}))
        if record.exc_info:
            data["exc"] = self.formatException(record.exc_info)
        return json.dumps(data)


_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(JsonFormatter())
log = logging.getLogger("payment")
log.setLevel(logging.INFO)
log.addHandler(_handler)
log.propagate = False


# ---------------------------------------------------------------- database pool
CONNINFO = f"postgresql://{config.DB_USER}:{config.DB_PASSWORD}@{config.DB_HOST}:5432/{config.DB_NAME}"
pool = ConnectionPool(
    CONNINFO, min_size=2, max_size=config.POOL_SIZE, timeout=config.POOL_TIMEOUT_S,
    kwargs={"row_factory": dict_row, "connect_timeout": 3}, open=False,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS payments (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_email TEXT NOT NULL,
    amount_cents   INTEGER NOT NULL CHECK (amount_cents > 0),
    status         TEXT NOT NULL DEFAULT 'settled',
    created        TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

# ---------------------------------------------------------------- metrics
HTTP_REQUESTS = Counter("http_requests", "HTTP requests", ["method", "path", "status"])  # exposed as http_requests_total
HTTP_LATENCY = Histogram("http_request_duration_seconds", "Request latency", ["path"])
APP_INFO = Gauge("app_info", "Deployed version", ["version"])
G_POOL_MAX = Gauge("db_pool_max", "Maximum connections in the pool")
G_POOL_SIZE = Gauge("db_pool_size", "Connections currently open")
G_POOL_AVAILABLE = Gauge("db_pool_available", "Idle connections")
G_POOL_IN_USE = Gauge("db_pool_in_use", "Connections handed out")
G_POOL_WAITING = Gauge("db_pool_waiting", "Requests waiting for a connection")


def _stat(key: str) -> int:
    try:
        return pool.get_stats().get(key, 0)
    except Exception:
        return 0


G_POOL_MAX.set(config.POOL_SIZE)
G_POOL_SIZE.set_function(lambda: _stat("pool_size"))
G_POOL_AVAILABLE.set_function(lambda: _stat("pool_available"))
G_POOL_IN_USE.set_function(lambda: _stat("pool_size") - _stat("pool_available"))
G_POOL_WAITING.set_function(lambda: _stat("requests_waiting"))

_RECEIPTS: dict[str, str] = {}      # only used when ENABLE_RECEIPT_CACHE is on


# ---------------------------------------------------------------- app
@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        with psycopg.connect(CONNINFO, connect_timeout=3) as c:       # fail fast with a readable reason
            c.execute("SELECT 1")
    except Exception as e:
        log.error(f"Cannot connect to database host={config.DB_HOST}: {e}")
        raise
    pool.open(wait=True, timeout=10)
    with pool.connection() as conn:
        conn.execute(SCHEMA)
    APP_INFO.labels(version=VERSION).set(1)
    log.info(f"Deployed commit {VERSION}, batch_size={config.BATCH_SIZE}, pool_size={config.POOL_SIZE}")
    yield
    pool.close()


app = FastAPI(title="payment-service", lifespan=lifespan)


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    start = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        route = request.scope.get("route")
        path = route.path if route else request.url.path
        if path != "/metrics":
            HTTP_REQUESTS.labels(request.method, path, str(status)).inc()
            HTTP_LATENCY.labels(path).observe(time.perf_counter() - start)


class PaymentIn(BaseModel):
    customer_email: str = Field(min_length=5, max_length=255)
    amount_cents: int = Field(gt=0, le=10_000_000)


def render_receipt(payment_id: int, email: str, amount: int) -> str:
    return f"Receipt #{payment_id} for {email}: {amount}\n" + ("-" * 100_000)


@app.post("/payments", status_code=201)
def create_payment(body: PaymentIn):
    start = time.perf_counter()
    try:
        with pool.connection() as conn:                  # waits up to POOL_TIMEOUT_S for a free connection
            for _ in range(config.BATCH_SIZE):           # per-item checks, connection is held the whole time
                conn.execute("SELECT pg_sleep(%s)", (config.ITEM_WORK_S,))
            row = conn.execute(
                "INSERT INTO payments (customer_email, amount_cents) VALUES (%s, %s) RETURNING id",
                (body.customer_email, body.amount_cents),
            ).fetchone()
    except PoolTimeout:
        log.error(
            f"Database connection pool exhausted: no connection free after {config.POOL_TIMEOUT_S}s",
            extra={"fields": {"path": "/payments", "pool_max": config.POOL_SIZE}},
        )
        raise HTTPException(500, "database connection timeout")

    elapsed = time.perf_counter() - start
    if elapsed > 1.0:
        log.warning(f"Slow request: POST /payments took {elapsed:.1f}s")
    if config.ENABLE_RECEIPT_CACHE:
        _RECEIPTS[str(uuid.uuid4())] = render_receipt(row["id"], body.customer_email, body.amount_cents)
    return {"id": row["id"], "status": "settled"}


@app.get("/payments/{payment_id}")
def get_payment(payment_id: int):
    try:
        with pool.connection() as conn:
            row = conn.execute("SELECT * FROM payments WHERE id = %s", (payment_id,)).fetchone()
    except PoolTimeout:
        log.error("Database connection pool exhausted on read", extra={"fields": {"path": "/payments/{id}"}})
        raise HTTPException(500, "database connection timeout")
    if not row:
        raise HTTPException(404, "payment not found")
    row["created"] = row["created"].isoformat()
    return row


@app.get("/health")
def health():
    return {"status": "ok", "version": VERSION}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
