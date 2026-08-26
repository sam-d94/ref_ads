"""Google Ads Transparency Center 진단 도구 — 실제 통신 확인

사용법: python tools/debug_google.py 도메인
"""
import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from playwright.async_api import async_playwright  # noqa: E402

from collectors.google_ads import ADSTC_URL, UA  # noqa: E402

DOMAIN = sys.argv[1] if len(sys.argv) > 1 else "oliveyoung.co.kr"
DEBUG_DIR = pathlib.Path(__file__).resolve().parents[1] / "references" / "_debug" / "google"


async def main():
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    reqs = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(locale="ko-KR", user_agent=UA,
                                        viewport={"width": 1440, "height": 900})
        page = await ctx.new_page()

        def on_request(req):
            if req.resource_type in ("xhr", "fetch"):
                reqs.append(f"{req.method} {req.url[:170]}")

        page.on("request", on_request)
        await page.goto(ADSTC_URL.format(region="KR", domain=DOMAIN),
                        wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(8000)
        title = await page.title()
        for i in range(4):
            await page.mouse.wheel(0, 2500)
            await page.wait_for_timeout(1500)
        body_text = ""
        try:
            body_text = await page.evaluate(
                "document.body ? document.body.innerText.slice(0, 1500) : ''")
        except Exception as e:
            print("evaluate 실패:", e)
        await browser.close()

    print("TITLE:", title)
    print("\n--- 본문 텍스트(앞 800자) ---")
    print((body_text or "")[:800])
    print(f"\nXHR/Fetch {len(reqs)}개:")
    for r in reqs[:30]:
        print(" -", r)


if __name__ == "__main__":
    asyncio.run(main())
