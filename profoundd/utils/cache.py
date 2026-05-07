"""
Two-tier search cache: in-memory L1 (fast) + SQLite L2 (persistent).
Survives container restarts. Thread-safe.
"""
import hashlib
import logging
import os
import sqlite3
import threading
import time

logger = logging.getLogger(__name__)

# --- L1: In-memory cache (fast, volatile) ---
_lock = threading.Lock()
_mem = {}  # key -> (value, expires_at)
MEM_MAX = 500
MEM_TTL = 300  # 5 minutes

# --- L2: SQLite persistent cache ---
_DB_PATH = os.environ.get("CACHE_DB_PATH", "/app/data/cache.db")
_db_lock = threading.Lock()
_db_conn = None

SEARCH_TTL = 300       # 5 min for search results
AI_SUMMARY_TTL = 86400  # 24 hours for AI summaries


def _get_db():
    """Lazy-init SQLite connection (one per process)."""
    global _db_conn
    if _db_conn is not None:
        return _db_conn
    with _db_lock:
        if _db_conn is not None:
            return _db_conn
        try:
            os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
            conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cache (
                    key TEXT PRIMARY KEY,
                    value BLOB NOT NULL,
                    expires_at REAL NOT NULL,
                    created_at REAL NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cache_expires ON cache(expires_at)")
            conn.commit()
            _db_conn = conn
            logger.info("Cache DB initialized at %s", _DB_PATH)
        except Exception as e:
            logger.warning("Cache DB init failed (falling back to memory-only): %s", e)
        return _db_conn


def _cleanup_db():
    """Remove expired entries (called occasionally)."""
    conn = _get_db()
    if conn:
        try:
            conn.execute("DELETE FROM cache WHERE expires_at < ?", (time.time(),))
            conn.commit()
        except Exception:
            pass


# --- Public API ---

def cache_get(key):
    """Check L1 (memory), then L2 (SQLite). Returns value or None."""
    now_mono = time.monotonic()
    now_epoch = time.time()

    # L1 check
    with _lock:
        entry = _mem.get(key)
        if entry is not None:
            value, expires_at = entry
            if now_mono > expires_at:
                del _mem[key]
            else:
                return value

    # L2 check
    conn = _get_db()
    if conn:
        try:
            with _db_lock:
                row = conn.execute(
                    "SELECT value FROM cache WHERE key = ? AND expires_at > ?",
                    (key, now_epoch)
                ).fetchone()
            if row:
                # Promote to L1
                with _lock:
                    _mem[key] = (row[0], now_mono + MEM_TTL)
                return row[0]
        except Exception as e:
            logger.debug("Cache DB read error: %s", e)

    return None


def cache_set(key, value, ttl=SEARCH_TTL):
    """Write to both L1 (memory) and L2 (SQLite)."""
    now_mono = time.monotonic()
    now_epoch = time.time()

    # L1
    with _lock:
        if len(_mem) >= MEM_MAX:
            # Evict expired
            expired = [k for k, (_, exp) in _mem.items() if now_mono > exp]
            for k in expired:
                del _mem[k]
            if len(_mem) >= MEM_MAX:
                to_drop = sorted(_mem, key=lambda k: _mem[k][1])[:MEM_MAX // 4]
                for k in to_drop:
                    del _mem[k]
        _mem[key] = (value, now_mono + min(ttl, MEM_TTL))

    # L2
    conn = _get_db()
    if conn:
        try:
            # Store as string (rendered HTML or JSON)
            val_bytes = value if isinstance(value, (bytes, memoryview)) else str(value).encode("utf-8")
            with _db_lock:
                conn.execute(
                    "INSERT OR REPLACE INTO cache (key, value, expires_at, created_at) VALUES (?, ?, ?, ?)",
                    (key, val_bytes, now_epoch + ttl, now_epoch)
                )
                conn.commit()
        except Exception as e:
            logger.debug("Cache DB write error: %s", e)

    # Periodic cleanup (1 in 50 writes)
    if int(now_epoch) % 50 == 0:
        _cleanup_db()


def make_search_key(query, category, page, sort_by, date_from=None, date_to=None, source_filter=None, view="cards", per_page=20):
    """Build a cache key from search parameters."""
    raw = f"search:{query.lower().strip()}:{category}:{page}:{sort_by}:{date_from}:{date_to}:{source_filter}:{view}:{per_page}"
    return f"search:{hashlib.md5(raw.encode()).hexdigest()}"


def make_ai_key(query):
    """Build a cache key for AI summary results."""
    raw = f"ai:{query.lower().strip()}"
    return f"ai:{hashlib.md5(raw.encode()).hexdigest()}"
