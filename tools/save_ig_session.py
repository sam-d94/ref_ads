"""Instagram 세션(쿠키) 저장 도구 — IG 수집 활성화 전 딱 1회 실행

사용법:
    python tools/save_ig_session.py
브라우저가 열리면 직접 로그인하고, 터미널로 돌아와 Enter를 누르세요.
"""
import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from playwright.async_api import async_playwright  # noqa: E402

STATE_FILE = pathlib.Path(__file__).resolve().parents[1] / "config" / "ig_state.json"


async def main():
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        ctx = await browser.new_context(locale="ko-KR")
        page = await ctx.new_page()
        await page.goto("https://www.instagram.com/accounts/login/")
        input(">> 브라우저에서 로그인 후, 여기서 Enter를 누르세요... ")
        await ctx.storage_state(path=str(STATE_FILE))
        await browser.close()
    print(f"세션 저장 완료: {STATE_FILE}")
    print("이제 config/brands.yaml 의 settings.instagram.enabled 를 true로 바꾸세요.")


if __name__ == "__main__":
    asyncio.run(main())
