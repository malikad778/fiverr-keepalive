#!/usr/bin/env python3
"""
scripts/local_auth.py
Headed login helper for local PC.
Launches a browser on your screen so you can log in and solve any CAPTCHAs.
Saves the encrypted cookies to session/store.db for direct upload to EC2.
This script is self-contained and avoids compiling heavy packages like NumPy locally.
"""
import os
import sys
import json
import asyncio
import base64
from pathlib import Path
from datetime import datetime, timezone

# Try importing required packages, auto-install if missing
try:
    from dotenv import load_dotenv
    from cryptography.fernet import Fernet
    import aiosqlite
    from playwright.async_api import async_playwright
except ImportError:
    print("Missing required libraries. Installing dependencies locally...")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "playwright", "aiosqlite", "cryptography", "python-dotenv"])
    print("Playwright install complete. Installing browser binaries...")
    subprocess.check_call([sys.executable, "-m", "playwright", "install", "chromium"])
    
    # Re-verify imports
    from dotenv import load_dotenv
    from cryptography.fernet import Fernet
    import aiosqlite
    from playwright.async_api import async_playwright

# Load local .env
_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(_ROOT / ".env")

def encrypt_data(data: str, secret: str) -> str:
    """Encrypt a plaintext string, return base64 ciphertext."""
    try:
        key = secret.encode() if isinstance(secret, str) else secret
        f = Fernet(key)
    except Exception:
        # Fallback key derivation if the key is not standard Fernet base64 format
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b"fiverr-keepalive-salt",
            iterations=100_000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(secret.encode()))
        f = Fernet(key)
    return f.encrypt(data.encode()).decode()

async def is_logged_in(page, username) -> bool:
    try:
        # Check for typical indicators of login
        checks = [
            page.locator(f"[href*='/users/{username}']").count(),
            page.locator(".user-profile-menu").count(),
            page.locator("[data-testid='user-avatar']").count(),
            page.locator(".profile-image").count(),
        ]
        results = await asyncio.gather(*checks, return_exceptions=True)
        return any(r > 0 for r in results if isinstance(r, int))
    except Exception:
        return False

async def main():
    username = os.getenv("FIVERR_USERNAME", "")
    secret = os.getenv("SECRET_KEY", "")
    
    if not secret:
        # Generate a new Fernet key if missing
        secret = Fernet.generate_key().decode()
        env_path = _ROOT / ".env"
        with open(env_path, "a") as f:
            f.write(f"\nSECRET_KEY={secret}\n")
        print(f"Generated new SECRET_KEY and saved to .env")

    if not username:
        username = input("Enter your Fiverr username (e.g. johndoe): ").strip()
        env_path = _ROOT / ".env"
        if env_path.exists():
            with open(env_path, "a") as f:
                f.write(f"\nFIVERR_USERNAME={username}\n")
        else:
            with open(env_path, "w") as f:
                f.write(f"FIVERR_USERNAME={username}\n")
        os.environ["FIVERR_USERNAME"] = username

    base_url = "https://www.fiverr.com"

    print("==================================================")
    print("  Fiverr Keepalive - Local Authentication Helper")
    print("==================================================")
    print("Since Fiverr requires trust and solving verification challenges,")
    print("we will capture cookies directly from your local Chrome browser.")
    print("\nHow to prepare:")
    print("1. Completely close all Google Chrome windows on your PC.")
    print("2. Open command prompt or run dialog (Win+R) and run:")
    print('   chrome.exe --remote-debugging-port=9222 --user-data-dir="C:\\fiverr-keepalive\\chrome_debug_profile"')
    print("3. In the Chrome window that opens, log in to Fiverr.")
    print("==================================================")
    
    input("Press Enter once you have started Chrome with the command above and are ready...")

    async with async_playwright() as p:
        try:
            # Connect to existing Chrome browser running with remote debugging
            browser = await p.chromium.connect_over_cdp("http://localhost:9222")
            context = browser.contexts[0]
            pages = context.pages
            page = pages[0] if pages else await context.new_page()
        except Exception as e:
            print(f"\n[!] Error: Could not connect to Chrome on port 9222.")
            print("Make sure you closed all Chrome windows and ran the exact command specified above.")
            print(f"Details: {e}")
            sys.exit(1)
            
        print("\nNavigating Chrome to Fiverr to check login status...")
        await page.goto(f"{base_url}/")
        
        print("Waiting for login status...")
        logged_in = False
        while not logged_in:
            await asyncio.sleep(3)
            if page.is_closed():
                print("Target tab was closed before login was completed.")
                sys.exit(1)
            
            logged_in = await is_logged_in(page, username)
            if not logged_in:
                print("Not logged in yet. Please log in to your account in the browser...")
            
        print("\n[+] Login detected successfully!")
        
        # Save cookies to local DB
        cookies = await context.cookies()
        raw = json.dumps(cookies)
        stored = encrypt_data(raw, secret)

        expires_unix = max((c.get("expires", 0) for c in cookies), default=0)
        expires_dt = (
            datetime.fromtimestamp(expires_unix, tz=timezone.utc).isoformat()
            if expires_unix > 0
            else None
        )

        db_file = _ROOT / "session" / "store.db"
        db_file.parent.mkdir(parents=True, exist_ok=True)
        
        async with aiosqlite.connect(db_file) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS cookies (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    domain    TEXT NOT NULL,
                    data      TEXT NOT NULL,
                    saved_at  TEXT NOT NULL,
                    expires   TEXT
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS session_state (
                    key   TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            await db.commit()
            
            await db.execute("DELETE FROM cookies WHERE domain = ?", ("fiverr.com",))
            await db.execute(
                "INSERT INTO cookies (domain, data, saved_at, expires) VALUES (?, ?, ?, ?)",
                ("fiverr.com", stored, datetime.now(timezone.utc).isoformat(), expires_dt),
            )
            await db.commit()

        print(f"[+] Cookies encrypted and saved to: {db_file}")
        print("You can close the browser window now.")
        await browser.close()
        print("==================================================")
        print("Next steps:")
        print("We will upload this session database to your EC2 instance.")
        print("==================================================")

if __name__ == "__main__":
    asyncio.run(main())
