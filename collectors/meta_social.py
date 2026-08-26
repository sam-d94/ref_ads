"""Instagram / Facebook 프로필 수집기 (실험적 — 로그인 세션 필요)

!! 주의: 자동화 접속은 계정 제한 위험이 있습니다.
- 전용 계정 사용, 하루 1회 이내 실행 권장
- 사전에 tools/save_ig_session.py 로 세션 파일 생성 필수
"""
import logging
import time

from playwright.async_api import async_playwright

from .base import Reference, goto_with_retry

log = logging.getLogger("meta_social")

IG_URL = "https://www.instagram.com/{handle}/"
FB_URL = "https://www.facebook.com/{handle}/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def _extract_ig(data: dict, found: dict):
    stack = [data]
    while stack:
        n = stack.pop()
        if isinstance(n, dict):
            sc = n.get("shortcode") or n.get("code")
            if sc and sc not in found:
                likes = None
                lb = n.get("edge_liked_by") or n.get("edge_media_preview_like") or {}
                if isinstance(lb, dict):
                    likes = lb.get("count")
                if likes is None:
                    likes = n.get("like_count")
                caption = ""
                ce = n.get("edge_media_to_caption")
                if isinstance(ce, dict) and ce.get("edges"):
                    first = (ce["edges"] or [{}])[0]
                    caption = ((first.get("node") or {}).get("text")) or ""
                elif isinstance(n.get("caption"), str):
                    caption = n["caption"]
                found[str(sc)] = {
                    "likes": likes,
                    "comments": n.get("edge_media_to_comment",
                                      {}).get("count") if isinstance(
                        n.get("edge_media_to_comment"), dict) else n.get("comment_count"),
                    "caption": caption,
                    "ts": n.get("taken_at_timestamp"),
                    "display": n.get("display_url"),
                }
            stack.extend(n.values())
        elif isinstance(n, list):
            stack.extend(n)


async def collect_instagram(category: str, brand_name: str, handles: list,
                            settings: dict, headless: bool = True):
    ig_cfg = settings.get("instagram", {})
    state_file = ig_cfg.get("state_file", "config/ig_state.json")
    scrolls = int(settings.get("scrolls", {}).get("instagram", 4))

    import pathlib
    state_path = pathlib.Path(state_file)
    if not state_path.exists():
        log.warning("[%s] IG 세션 파일 없음(%s) → 스킵. tools/save_ig_session.py 먼저 실행",
                    brand_name, state_file)
        return []

    found = {}
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        ctx = await browser.new_context(storage_state=str(state_path),
                                        locale="ko-KR", user_agent=UA,
                                        viewport={"width": 1280, "height": 900})
        page = await ctx.new_page()

        async def on_response(resp):
            if not any(k in resp.url for k in ("/api/v1/", "/graphql/", "/query/")):
                return
            try:
                data = await resp.json()
            except Exception:
                return
            _extract_ig(data if isinstance(data, dict) else {}, found)

        page.on("response", on_response)

        for handle in handles or []:
            try:
                await goto_with_retry(page, IG_URL.format(handle=handle),
                                      wait_until="domcontentloaded", timeout=60000)
            except Exception as e:
                log.warning("[%s] instagram @%s 로드 실패: %s", brand_name, handle, e)
                continue
            if "/accounts/login" in page.url:
                log.warning("[%s] IG 세션 만료 → @%s 스킵 (save_ig_session 재실행)",
                            brand_name, handle)
                continue
            for _ in range(scrolls):
                await page.mouse.wheel(0, 2000)
                await page.wait_for_timeout(1500)
        await browser.close()

    items = []
    for sc, d in found.items():
        ts = d.get("ts")
        items.append(Reference(
            platform="instagram",
            brand=brand_name,
            external_id=sc,
            url=f"https://www.instagram.com/p/{sc}/",
            body=(d.get("caption") or "")[:500],
            metrics={"likes": d.get("likes"), "comments": d.get("comments")},
            media_urls=[d["display"]] if d.get("display") else [],
            published_at=time.strftime("%Y-%m-%d", time.gmtime(ts)) if ts else "",
            extra={"category": category},
        ))
    log.info("[%s] instagram: %d건", brand_name, len(items))
    return items


def _extract_fb(data: dict, found: dict):
    stack = [data]
    while stack:
        n = stack.pop()
        if isinstance(n, dict):
            pid = n.get("post_id")
            msg = n.get("message") or n.get("text")
            if pid and isinstance(msg, str) and msg.strip():
                found.setdefault(str(pid), {"message": msg.strip(),
                                            "url": n.get("wwwURL") or ""})
            stack.extend(n.values())
        elif isinstance(n, list):
            stack.extend(n)


async def collect_facebook(category: str, brand_name: str, handles: list,
                           settings: dict, headless: bool = True):
    fb_cfg = settings.get("facebook", {})
    state_file = fb_cfg.get("state_file", "config/fb_state.json")

    import pathlib
    state_path = pathlib.Path(state_file)
    if not state_path.exists():
        log.warning("[%s] FB 세션 파일 없음(%s) → 스킵", brand_name, state_file)
        return []

    found = {}
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        ctx = await browser.new_context(storage_state=str(state_path),
                                        locale="ko-KR", user_agent=UA)
        page = await ctx.new_page()

        async def on_response(resp):
            if "graphql" not in resp.url.lower():
                return
            try:
                data = await resp.json()
            except Exception:
                return
            _extract_fb(data if isinstance(data, dict) else {}, found)

        page.on("response", on_response)

        for handle in handles or []:
            try:
                await goto_with_retry(page, FB_URL.format(handle=handle),
                                      wait_until="domcontentloaded", timeout=60000)
            except Exception as e:
                log.warning("[%s] facebook %s 로드 실패: %s", brand_name, handle, e)
                continue
            for _ in range(int(settings.get("scrolls", {}).get("facebook", 3))):
                await page.mouse.wheel(0, 2500)
                await page.wait_for_timeout(1500)
        await browser.close()

    items = []
    for pid, d in found.items():
        items.append(Reference(
            platform="facebook",
            brand=brand_name,
            external_id=pid,
            url=d.get("url") or f"https://www.facebook.com/{pid}",
            body=d.get("message", ""),
            extra={"category": category},
        ))
    log.info("[%s] facebook: %d건", brand_name, len(items))
    return items
