#!/usr/bin/env python3
"""
scripts/capture_challenge.py
Diagnostic: dump the live "It needs a human touch" page so the solver's
selectors can be verified against the real DOM.

The screenshot alone can't tell us what actually matters - whether the
PRESS & HOLD control lives in the main frame or a nested PerimeterX iframe,
and what its real id/class/bounding box are. This prints all of that.

Usage:
    python scripts/capture_challenge.py                # capture only
    python scripts/capture_challenge.py --solve        # capture, then try solving
    python scripts/capture_challenge.py --headed       # watch it happen
"""
import sys
import json
import asyncio
import argparse
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.async_api import async_playwright

from src.utils.config import get, load_config
from src.session.manager import load_cookies
from src.behavior.challenge import (
    is_challenge_present,
    _find_press_hold_target,
    solve_human_touch,
)

_ROOT = Path(__file__).resolve().parent.parent

PROBE_SELECTORS = [
    "#px-captcha",
    "#px-captcha-wrapper",
    "div[id^='px-captcha']",
    "[class*='px-captcha']",
    "div[aria-label*='Press & Hold' i]",
    "button[aria-label*='Press & Hold' i]",
    "iframe",
]


async def probe_frames(page) -> list:
    """Enumerate every frame and which challenge selectors match inside it."""
    report = []
    for frame in page.frames:
        entry = {
            "url": frame.url[:160],
            "name": frame.name,
            "is_main": frame is page.main_frame,
            "matches": {},
        }
        for sel in PROBE_SELECTORS:
            try:
                count = await frame.locator(sel).count()
                if count == 0:
                    continue
                loc = frame.locator(sel).first
                entry["matches"][sel] = {
                    "count": count,
                    "visible": await loc.is_visible(),
                    "box": await loc.bounding_box(),
                }
            except Exception as e:
                entry["matches"][sel] = {"error": str(e)[:120]}
        report.append(entry)
    return report


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--solve", action="store_true", help="attempt the press-and-hold after capturing")
    ap.add_argument("--headed", action="store_true", help="run with a visible browser")
    ap.add_argument("--url", default=None, help="URL to probe (default: target.base_url)")
    ap.add_argument("--profile", default=None,
                    help="browser profile dir (default: browser.user_data_dir). "
                         "Point at a scratch dir to probe without touching the "
                         "daemon's profile, which Chromium keeps locked while running.")
    ap.add_argument("--no-cookies", action="store_true",
                    help="skip cookie injection - probe a cold session")
    args = ap.parse_args()

    cfg = load_config()
    url = args.url or cfg["target"]["base_url"]
    user_data_dir = args.profile or str(_ROOT / get("browser.user_data_dir", "session/profile"))
    Path(user_data_dir).mkdir(parents=True, exist_ok=True)
    out_dir = _ROOT / "diagnostics"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

    print("=" * 62)
    print("  Challenge capture")
    print("=" * 62)
    print(f"  url      : {url}")
    print(f"  profile  : {user_data_dir}")
    print(f"  output   : {out_dir}")
    print("=" * 62)

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir,
            headless=not args.headed,
            viewport={"width": 1366, "height": 768},
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        page = context.pages[0] if context.pages else await context.new_page()

        if args.no_cookies:
            print("[*] Cold session - no cookies injected")
        else:
            try:
                await load_cookies(context)
                print("[+] Stored cookies injected")
            except Exception as e:
                print(f"[!] Cookie load skipped: {e}")

        print(f"[*] Navigating to {url} ...")
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            print(f"[!] Navigation note: {e}")
        await asyncio.sleep(5)

        title = await page.title()
        blocked = await is_challenge_present(page)

        print(f"\n  title    : {title!r}")
        print(f"  final url: {page.url}")
        print(f"  BLOCKED  : {blocked}")

        shot = out_dir / f"challenge-{stamp}.png"
        await page.screenshot(path=str(shot), full_page=True)
        print(f"  screenshot -> {shot}")

        frames = await probe_frames(page)
        print(f"\n--- frames ({len(frames)}) ---")
        for f in frames:
            tag = "MAIN" if f["is_main"] else "child"
            print(f"  [{tag}] {f['url']}")
            for sel, info in f["matches"].items():
                print(f"        {sel} -> {info}")

        target = await _find_press_hold_target(page)
        if target:
            frame, box = target
            print(f"\n[+] Solver would press at "
                  f"({box['x'] + box['width']/2:.0f}, {box['y'] + box['height']/2:.0f})")
            print(f"    box   = {box}")
            print(f"    frame = {frame.url[:100]}")
        else:
            print("\n[!] Solver found NO press-and-hold target "
                  "(it will abort rather than click blindly)")

        # Dump the PX container markup if present - this is what selector
        # fixes should be written against.
        html_dump = None
        for frame in page.frames:
            try:
                loc = frame.locator("#px-captcha, div[id^='px-captcha'], [class*='px-captcha']").first
                if await loc.count() > 0:
                    html_dump = await loc.evaluate("el => el.outerHTML")
                    break
            except Exception:
                continue

        report = {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "url": page.url,
            "title": title,
            "blocked": blocked,
            "frames": frames,
            "target_found": target is not None,
            "target_box": target[1] if target else None,
            "px_container_html": html_dump,
            "body_text_head": (
                await page.evaluate("() => (document.body && document.body.innerText || '').slice(0, 800)")
            ),
        }
        report_path = out_dir / f"challenge-{stamp}.json"
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\n  report -> {report_path}")

        if args.solve:
            if not blocked:
                print("\n[*] No challenge present - nothing to solve.")
            else:
                print("\n[*] Attempting press-and-hold ...")
                # context=None on purpose: solve_human_touch() saves cookies on
                # success, and save_cookies() does DELETE-then-INSERT on the
                # shared store.db. A scratch-profile probe must never be able to
                # clobber the daemon's real stored session.
                ok = await solve_human_touch(page, None)
                print(f"[{'+' if ok else '!'}] solve_human_touch -> {ok}")
                after = out_dir / f"challenge-{stamp}-after.png"
                await page.screenshot(path=str(after), full_page=True)
                print(f"    after screenshot -> {after}")

        await context.close()
        print("\nDone.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrupted.")
