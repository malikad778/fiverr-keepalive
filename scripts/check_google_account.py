import asyncio
import aiosqlite
import json
import os
import re
from pathlib import Path
from dotenv import load_dotenv
from cryptography.fernet import Fernet
from playwright.async_api import async_playwright

load_dotenv('C:/fiverr-keepalive/.env')
secret = os.getenv('SECRET_KEY')
f = Fernet(secret.encode())

async def check_google_account():
    async with aiosqlite.connect('C:/fiverr-keepalive/session/store.db') as db:
        async with db.execute('SELECT data FROM cookies') as cur:
            row = await cur.fetchone()
            if not row:
                print('No cookies stored in store.db.')
                return
            raw = f.decrypt(row[0].encode()).decode()
            cookies = json.loads(raw)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        await context.add_cookies(cookies)
        page = await context.new_page()

        print('Navigating to https://myaccount.google.com/ ...')
        try:
            await page.goto('https://myaccount.google.com/', wait_until='domcontentloaded', timeout=30000)
            await asyncio.sleep(4)
        except Exception as e:
            print(f'Navigation error: {e}')

        title = await page.title()
        url = page.url
        print(f'Page URL: {url}')
        print(f'Page Title: {title}')

        # Search for email in the page
        body_text = await page.inner_text('body')
        emails = set(re.findall(r'[a-zA-Z0-9_.+-]+@gmail\.com', body_text))
        if emails:
            print(f'Verified Logged-in Gmail: {list(emails)}')
        else:
            # Check avatar / header
            links = await page.query_selector_all('a[aria-label*="Google Account"]')
            found_label = False
            for link in links:
                aria = await link.get_attribute('aria-label')
                if aria:
                    print(f'Google Account info: {aria}')
                    found_label = True
            if not found_label:
                print('No active Google session displayed on myaccount.google.com')

        await browser.close()

if __name__ == '__main__':
    asyncio.run(check_google_account())
