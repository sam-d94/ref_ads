"""Meta Ad Library 수집기 — 로그인 없이 공개 페이지의 JSON 응답을 가로채는 방식"""
import asyncio
import logging
import re
import time
from urllib.parse import quote

from playwright.async_api import async_playwright

from .base import Reference, goto_with_retry

log = logging.getLogger("meta")

LIBRARY_URL = (
    "https://www.facebook.com/ads/library/"
    "?active_status=active&ad_type=all&country={country}"
    "&media_type=all&q={q}&search_type=keyword_unordered"
)
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def _harvest_media(node, out: list):
    """snapshot 내 미디어 URL을 휴리스틱으로 수집 (스키마 변경에 강함)"""
    stack = [node]
    while stack and len(out) < 20:
        n = stack.pop()
        if isinstance(n, dict):
            for k in ("video_hd_url", "video_sd_url", "video_preview_image_url"):
                v = n.get(k)
                if isinstance(v, str) and v.startswith("http") and v not in out:
                    out.append(v)
            stack.extend(n.values())
        elif isinstance(n, list):
            stack.extend(n)
        elif isinstance(n, str) and n.split("?")[0].lower().endswith(
                (".jpg", ".jpeg", ".png", ".webp")) \
                and n.startswith("http") and n not in out:
            out.append(n)


def _ad_fields(node: dict) -> dict:
    snap = node.get("snapshot") or {}
    body = ((snap.get("body") or {}).get("text") or "").strip()
    media = []
    _harvest_media(snap, media)
    platforms = node.get("publisher_platform") or []
    return {
        "body": body,
        "title": (snap.get("title") or "").strip(),
        "cta": snap.get("cta_text") or snap.get("cta_type") or "",
        "platforms": ",".join(platforms) if isinstance(platforms, list) else str(platforms),
        "start": node.get("start_date") or snap.get("start_date") or "",
        "similar": node.get("collation_count"),
        "page": node.get("page_name") or snap.get("page_name") or "",
        "media": media,
        "link": snap.get("link_url") or "",
        "active_days": node.get("total_active_time"),
    }


def _extract_ads(payload, bucket: dict):
    stack = [payload]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            aid = node.get("ad_archive_id") or node.get("archive_id")
            if aid and aid not in bucket:
                bucket[str(aid)] = _ad_fields(node)
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)


def _fmt_ts(v):
    s = str(v or "")
    if s.isdigit() and len(s) == 10:
        return time.strftime("%Y-%m-%d", time.gmtime(int(s)))
    return s


async def collect(category: str, brand_name: str, keyword: str, settings: dict,
                  headless: bool = True, scrolls: int = 8):
    refs = {}
    country = settings.get("country", "KR")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        ctx = await browser.new_context(
            locale="ko-KR", user_agent=UA,
            viewport={"width": 1440, "height": 900},
        )
        page = await ctx.new_page()

        seen_hits = [0]

        async def on_response(resp):  # DOM 파싱 대신 JSON 응답 가로채기
            u = resp.url
            if ("/api/graphql/" not in u) and ("ads/library" not in u):
                return
            try:
                try:
                    data = await resp.json()
                except Exception:
                    import json as _json
                    text = await resp.text()
                    data = _json.loads(re.sub(r"^[^{]*", "", text, count=1))
                n0 = len(refs)
                _extract_ads(data, refs)
                if len(refs) > n0:
                    seen_hits[0] += 1
                    log.info("[%s] graphql에서 %d건 파싱 (+%d)",
                             brand_name, len(refs), len(refs) - n0)
            except Exception as e:
                log.debug("[%s] 응답 파싱 스킵(%s): %s", brand_name, u[:80], e)

        page.on("response", on_response)

        page.on("response", on_response)

        url = LIBRARY_URL.format(country=country, q=quote(keyword))
        await goto_with_retry(page, url, wait_until="domcontentloaded",
                              timeout=60000)
        try:
            await page.wait_for_load_state("networkidle", timeout=20000)
        except Exception:
            pass

        html = (await page.content()).lower()
        if "security check" in html or "보안 확인" in html:
            log.warning("[%s] Meta 보안 체크 감지 → 결과 부족 가능. headed 모드로 재시도 권장",
                        brand_name)

        prev_count, stall = -1, 0
        for _ in range(scrolls):  # 무한 스크롤 유발 (성장 정지 시 조기 종료)
            await page.mouse.wheel(0, 2500)
            await page.wait_for_timeout(1200)
            if len(refs) == prev_count:
                stall += 1
                if stall >= 3:
                    break
            else:
                stall = 0
                prev_count = len(refs)

        await browser.close()

    items = []
    for aid, f in refs.items():
        if not (f["body"] or f["media"] or f["title"]):
            continue  # 광고 크리에이티브가 비어있는 레코드 제외
        items.append(Reference(
            platform="meta_adlibrary",
            brand=brand_name,
            external_id=aid,
            url=f"https://www.facebook.com/ads/library/?id={aid}",
            title=f["title"],
            body=f["body"],
            metrics={"similar_ads": f["similar"], "platforms": f["platforms"],
                     "active_days": f["active_days"]},
            media_urls=f["media"],
            published_at=_fmt_ts(f["start"]),
            extra={"page_name": f["page"], "cta": f["cta"], "link_url": f["link"],
                   "category": category, "keyword": keyword},
        ))
    log.info("[%s] meta_adlibrary '%s': %d건", brand_name, keyword, len(items))
    return items
