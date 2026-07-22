"""Shared Playwright rendering helper for JS-rendered external sources.

Sport-parameterized: each fetcher accepts a URL and returns rendered text.
Sport slug is chosen by the caller — this module doesn't hardcode any sport.

Graceful degrade: if Playwright/Chromium is missing (dev machine without
setup, CI runner where install failed), returns (None, 'unavailable')
instead of raising. Fetchers should treat unavailable == skip source.

USAGE:
    from _playwright_helper import render_page
    text, err = render_page('https://dimers.com/bet-hub/mlb/schedule')
    if err: return [], 200   # graceful skip
    # parse text
"""
import os
from typing import Optional


def render_page(url: str, wait_ms: int = 6000,
                wait_until: str = 'domcontentloaded',
                timeout_ms: int = 30000,
                user_agent: Optional[str] = None) -> tuple:
    """Render a page and return (body_text, error).

    (text, None)          → success
    (None, 'unavailable') → Playwright/Chromium missing (skip source)
    (None, error_msg)     → render failed (log + skip)
    """
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        return None, 'unavailable'

    ua = user_agent or ('Mozilla/5.0 (X11; Linux x86_64) '
                        'AppleWebKit/537.36 (KHTML, like Gecko) '
                        'Chrome/122.0 Safari/537.36')
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
            except Exception as e:
                # Chromium binary missing (playwright install chromium not run)
                if 'Executable doesn' in str(e) or 'not found' in str(e).lower():
                    return None, 'unavailable'
                return None, f'launch:{type(e).__name__}:{e}'

            try:
                page = browser.new_page(user_agent=ua)
                page.goto(url, wait_until=wait_until, timeout=timeout_ms)
                page.wait_for_timeout(wait_ms)
                text = page.inner_text('body')
                return text, None
            finally:
                browser.close()
    except Exception as e:
        return None, f'{type(e).__name__}:{e}'


def is_available() -> bool:
    """Cheap check — does Playwright import + Chromium binary exist?"""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            try:
                b = p.chromium.launch(headless=True)
                b.close()
                return True
            except Exception:
                return False
    except ImportError:
        return False
