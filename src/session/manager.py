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
    try:
        saved = datetime.fromisoformat(saved_at)
        if saved.tzinfo is None:
            now = datetime.utcnow()
        else:
            now = datetime.now(timezone.utc)
        if (now - saved) > timedelta(days=7):
            log.warning("session.manager.cookies_too_old", saved_at=saved_at)
            return False
    except Exception as e:
        log.warning("session.manager.date_check_warning", error=str(e))

    try:
        raw = decrypt(data_str) if should_decrypt else data_str
        cookies = json.loads(raw)

        # Never re-inject a stale PerimeterX identity - see PX_COOKIE_PREFIXES.
        if get("session.strip_px_cookies", True):
            before = len(cookies)
            cookies = [c for c in cookies if not is_px_cookie(c.get("name", ""))]
            if before != len(cookies):
                log.info("session.manager.px_cookies_skipped_on_load",
                         dropped=before - len(cookies))

        await context.add_cookies(cookies)
        log.info("session.manager.cookies_loaded", count=len(cookies))
        return True
    except Exception as e:
        log.error("session.manager.load_error", error=str(e))
        return False


# PerimeterX identity + clearance cookies. _pxvid pins the browser to a
# specific PX visitor id; _px3 is the clearance token. Once that visitor has
# been escalated, PX refuses to serve a solvable challenge to it *from any IP*
# - verified 2026-09-01: a cold profile on a rotated IP got a normal
# press-and-hold, while the daemon loading these same cookies got
# "Failed to display challenge" 47 seconds later on that identical IP.
#
# Dropping them costs a challenge (the clearance token goes too) but yields a
# fresh visitor that PX will actually let solve one. This is the mechanism
# behind the manual "clear site data and it works" fix.
PX_COOKIE_PREFIXES = ("_px", "pxcts")


def is_px_cookie(name: str) -> bool:
    n = (name or "").lower()
    return any(n.startswith(p) for p in PX_COOKIE_PREFIXES)


async def strip_px_cookies(context: BrowserContext) -> int:
    """
    Remove PerimeterX identity cookies from a live context.

    Needed in addition to filtering on load, because the daemon runs a
    persistent profile whose on-disk cookie jar already holds them.
    """
    try:
        cookies = await context.cookies()
        keep = [c for c in cookies if not is_px_cookie(c.get("name", ""))]
        dropped = len(cookies) - len(keep)
        if dropped:
            await context.clear_cookies()
            if keep:
                await context.add_cookies(keep)
            log.info("session.manager.px_cookies_stripped", dropped=dropped)
        return dropped
    except Exception as e:
        log.warning("session.manager.px_strip_error", error=str(e))
        return 0


def _domain_matches(cookie_domain: str, patterns: list[str]) -> bool:
    """True if a cookie's domain equals or is a subdomain of any pattern."""
    cd = (cookie_domain or "").lstrip(".").lower()
    for p in patterns:
        p = p.lstrip(".").lower()
        if cd == p or cd.endswith("." + p):
            return True
    return False


async def get_stored_cookies(domain: str = "fiverr.com") -> Optional[list]:
    """
    Return the decrypted cookie list from storage *without* injecting it
    into a context. Returns None if nothing is stored or decryption fails.
    """
    should_decrypt = get("session.cookie_encrypt", True)

    async with aiosqlite.connect(_db_path()) as db:
        await _init_db(db)
        async with db.execute(
            "SELECT data FROM cookies WHERE domain = ? ORDER BY id DESC LIMIT 1",
            (domain,),
        ) as cursor:
            row = await cursor.fetchone()

    if not row:
        return None

    try:
        raw = decrypt(row[0]) if should_decrypt else row[0]
        return json.loads(raw)
    except Exception as e:
        log.error("session.manager.stored_cookie_read_error", error=str(e))
        return None


async def restore_cookies(
    context: BrowserContext,
    only_domains: Optional[list[str]] = None,
    exclude_domains: Optional[list[str]] = None,
    domain: str = "fiverr.com",
) -> int:
    """
    Selectively re-inject stored cookies into a context.

    Used by challenge recovery: after wiping Fiverr site data we want the
    Google SSO cookies back (so "Continue with Google" is one click) but
    NOT the stale Fiverr / PerimeterX cookies that triggered the block.

    Returns the number of cookies injected.
    """
    cookies = await get_stored_cookies(domain)
    if not cookies:
        log.info("session.manager.no_cookies_to_restore", domain=domain)
        return 0

    selected = []
    for c in cookies:
        cd = c.get("domain", "")
        if only_domains and not _domain_matches(cd, only_domains):
            continue
        if exclude_domains and _domain_matches(cd, exclude_domains):
            continue
        selected.append(c)

    if not selected:
        log.info("session.manager.no_cookies_matched_filter",
                 only=only_domains, exclude=exclude_domains)
        return 0

    try:
        await context.add_cookies(selected)
        log.info("session.manager.cookies_restored",
                 count=len(selected), only=only_domains, exclude=exclude_domains)
        return len(selected)
    except Exception as e:
        log.error("session.manager.restore_error", error=str(e))
        return 0


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
