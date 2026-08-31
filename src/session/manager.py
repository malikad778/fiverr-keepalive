"""
src/session/manager.py
Cookie and session state persistence using encrypted SQLite storage.
Handles save, load, and expiry detection.
"""
import json
import asyncio
import aiosqlite
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Optional

from playwright.async_api import BrowserContext

from ..utils.config import get
from ..utils.crypto import encrypt, decrypt
from ..utils.logger import get_logger

log = get_logger("session.manager")

_ROOT = Path(__file__).resolve().parents[2]


def _db_path() -> Path:
    p = _ROOT / get("session.db_path", "session/store.db")
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


async def _init_db(conn: aiosqlite.Connection) -> None:
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS cookies (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            domain    TEXT NOT NULL,
            data      TEXT NOT NULL,          -- encrypted JSON
            saved_at  TEXT NOT NULL,
            expires   TEXT
        )
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS session_state (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    await conn.commit()


async def save_cookies(context: BrowserContext, domain: str = "fiverr.com") -> None:
    """Encrypt and persist all cookies from the browser context."""
    cookies = await context.cookies()
    if not cookies:
        log.warning("session.manager.no_cookies_to_save")
        return

    should_encrypt = get("session.cookie_encrypt", True)
    raw = json.dumps(cookies)
    stored = encrypt(raw) if should_encrypt else raw

    # Detect max expiry from cookies
    expires_unix = max(
        (c.get("expires", 0) for c in cookies),
        default=0,
    )
    expires_dt = (
        datetime.fromtimestamp(expires_unix, tz=timezone.utc).isoformat()
        if expires_unix > 0
        else None
    )

    async with aiosqlite.connect(_db_path()) as db:
        await _init_db(db)
        # Delete old entry for domain
        await db.execute("DELETE FROM cookies WHERE domain = ?", (domain,))
        await db.execute(
            "INSERT INTO cookies (domain, data, saved_at, expires) VALUES (?, ?, ?, ?)",
            (domain, stored, datetime.utcnow().isoformat(), expires_dt),
        )
        await db.commit()

    log.info("session.manager.cookies_saved", count=len(cookies), domain=domain)


async def load_cookies(context: BrowserContext, domain: str = "fiverr.com") -> bool:
    """
    Load cookies from storage and inject into browser context.
    Returns True if cookies were found and loaded.
    """
    should_decrypt = get("session.cookie_encrypt", True)

    async with aiosqlite.connect(_db_path()) as db:
        await _init_db(db)
        async with db.execute(
            "SELECT data, saved_at, expires FROM cookies WHERE domain = ? ORDER BY id DESC LIMIT 1",
            (domain,),
        ) as cursor:
            row = await cursor.fetchone()

    if not row:
        log.info("session.manager.no_stored_cookies", domain=domain)
        return False

    data_str, saved_at, expires = row

    # Check if cookies are too old (> 7 days)
    saved = datetime.fromisoformat(saved_at)
    if (datetime.utcnow() - saved) > timedelta(days=7):
        log.warning("session.manager.cookies_too_old", saved_at=saved_at)
        return False

    try:
        raw = decrypt(data_str) if should_decrypt else data_str
        cookies = json.loads(raw)
        await context.add_cookies(cookies)
        log.info("session.manager.cookies_loaded", count=len(cookies))
        return True
    except Exception as e:
        log.error("session.manager.load_error", error=str(e))
        return False


async def set_state(key: str, value: str) -> None:
    """Store an arbitrary session state value."""
    async with aiosqlite.connect(_db_path()) as db:
        await _init_db(db)
        await db.execute(
            "INSERT OR REPLACE INTO session_state (key, value, updated_at) VALUES (?, ?, ?)",
            (key, value, datetime.utcnow().isoformat()),
        )
        await db.commit()


async def get_state(key: str) -> Optional[str]:
    """Retrieve a stored session state value."""
    async with aiosqlite.connect(_db_path()) as db:
        await _init_db(db)
        async with db.execute(
            "SELECT value FROM session_state WHERE key = ?", (key,)
        ) as cursor:
            row = await cursor.fetchone()
    return row[0] if row else None


async def clear_session(domain: str = "fiverr.com") -> None:
    """Wipe stored cookies for a domain (force fresh login)."""
    async with aiosqlite.connect(_db_path()) as db:
        await _init_db(db)
        await db.execute("DELETE FROM cookies WHERE domain = ?", (domain,))
        await db.commit()
    log.info("session.manager.cleared", domain=domain)
