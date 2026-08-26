"""Meta Ad Library 진단 도구 v2 — archive_id가 어디서 오는지 전수 스캔"""
import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from playwright.async_api import async_playwright  # noqa: E402
from urllib.parse import quote  # noqa: E402

from collectors.meta_adlibrary import LIBRARY_URL, UA  # noqa: E402

KEYWORD = sys.argv[1] if len(sys.argv) > 1 else "올리브영"
DEBUG_DIR = pathlib.Path(__file__).resolve().parents[1] / "references" / "_debug" / "meta"


async def main():
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    hits, reqs = [], []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(locale="ko-KR", user_agent=UA,
                                        viewport={"width": 1440, "height": 900})
        page = await ctx.new_page()

        def on_request(req):
            if req.resource_type in ("xhr", "fetch"):
                reqs.append(f"{req.method} {req.url[:160]}")

        async def on_response(resp):
            try:
                if resp.request.resource_type not in ("xhr", "fetch", "document"):
                    return
                body = await resp.text()
                n = body.count("archive_id")
                if n:
                    rec = f"{resp.status} {resp.request.resource_type} {resp.url[:140]} | len={len(body)} archive_id={n}"
                    hits.append(rec)
                    if len(hits) <= 3:
                        (DEBUG_DIR / f"hit_{len(hits)}.txt").write_text(
                            body[:500000], encoding="utf-8", errors="ignore")
            except Exception:
                pass

        page.on("request", on_request)
        page.on("response", on_response)

        await page.goto(LIBRARY_URL.format(country="KR", q=quote(KEYWORD)),
                        wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(8000)
        for i in range(6):
            await page.mouse.wheel(0, 2500)
            await page.wait_for_timeout(1500)

        html_len = await page.evaluate("document.documentElement.outerHTML.length")
        has_in_html = await page.evaluate(
            "document.documentElement.outerHTML.includes('archive_id')")
        cards = await page.evaluate(
            "document.querySelectorAll('[class*=card], a[href*=library]').length")
        await browser.close()

    print(f"HTML 길이={html_len}  HTML내 archive_id={has_in_html}  대략카드={cards}")
    print(f"\narchive_id 포함 응답 {len(hits)}개:")
    for h in hits[:10]:
        print(" -", h)
    print(f"\nXHR/Fetch 요청 {len(reqs)}개 (앞 25개):")
    for r in reqs[:25]:
        print(" -", r)


if __name__ == "__main__":
    asyncio.run(main())
