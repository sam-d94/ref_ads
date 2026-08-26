"""Google Ads Transparency Center 수집기 (베스트 에포트)

SPA라서 네트워크(batchexecute) 응답을 가로채고, 스키마가 자주 바뀌므로
1) 원본 응답을 _debug 폴더에 덤프해두어(스키마 튜닝용)
2) 긴 텍스트/미디어 URL을 휴리스틱으로 추출합니다.
"""
import asyncio
import hashlib
import json
import logging
import re
from pathlib import Path

from playwright.async_api import async_playwright

from .base import Reference, goto_with_retry

log = logging.getLogger("google")

ADSTC_URL = ("https://adstransparency.google.com/"
             "?region={region}&domain={domain}&hl=ko")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

HANGUL = (0xAC00, 0xD7A3)


def _looks_like_copy(t: str) -> bool:
    t = t.strip()
    if not (25 <= len(t) <= 400):
        return False
    if HANGUL[0] <= ord(t[0]) <= HANGUL[1]:
        return True
    return " " in t and not t.startswith(("http", "/", "{", "["))


IMG_SRC_RE = re.compile(r'<img[^>]+src="(https://[^"]+)"', re.I)


def _harvest(node, texts: set, media: set):
    if isinstance(node, str):
        t = node.strip()
        low = t.split("?")[0].lower()
        # 크리에이티브 미디어
        if t.startswith("http") and (low.endswith((".jpg", ".jpeg", ".png", ".webp", ".mp4"))
                                     or "simgad" in t or "/imgad" in t):
            media.add(t)
            return
        # HTML 스니펫 안의 광고 이미지 → URL만 추출
        if "<img" in t:
            for m in IMG_SRC_RE.findall(t):
                media.add(m)
            return
        if _looks_like_copy(t) and not t.lstrip().startswith("<"):
            texts.add(t)
    elif isinstance(node, list):
        for x in node:
            _harvest(x, texts, media)
    elif isinstance(node, dict):
        for v in node.values():
            _harvest(v, texts, media)


def _try_parse(raw: str):
    """RPC(batchexecute류) 응답 파싱: )]}' 접두사 제거 후 JSON 시도,
    실패 시 따옴표로 묶인 긴 문자열 정규식 폴백"""
    body = re.sub(r"^\)\]\}'\s*", "", raw)
    try:
        return [json.loads(body)]
    except Exception:
        quoted = re.findall(r'"((?:[^"\\]|\\.){25,400})"', raw[:500000])
        return [q.encode().decode("unicode_escape", errors="ignore") for q in quoted]


def _loads_nested(obj, depth=0):
    """JSON 인코딩된 문자열 필드를 재귀적으로 파싱 (최대 3단계)"""
    if depth > 3:
        return obj
    if isinstance(obj, str):
        s = obj.strip()
        if s[:1] in ("[", "{"):
            try:
                return _loads_nested(json.loads(s), depth + 1)
            except Exception:
                return obj
        return obj
    if isinstance(obj, list):
        return [_loads_nested(x, depth + 1) for x in obj]
    if isinstance(obj, dict):
        return {k: _loads_nested(v, depth + 1) for k, v in obj.items()}
    return obj


async def collect(category: str, brand_name: str, domains: list, settings: dict,
                  headless: bool = True, scrolls: int = 6, max_refs_per_domain: int = 50):
    out_root = Path(settings.get("out_root", "references"))
    region = settings.get("region", "KR")
    debug_dir = out_root / "_debug" / "google"
    debug_dir.mkdir(parents=True, exist_ok=True)

    items = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        ctx = await browser.new_context(locale="ko-KR", user_agent=UA,
                                        viewport={"width": 1440, "height": 900})
        for domain in domains:
            payloads = []

            async def on_response(resp, domain=domain):
                u = resp.url
                if "adstransparency.google.com" not in u:
                    return
                # 크리에이티브 검색 RPC만 포착
                if "SearchCreatives" not in u and "SearchAdvertisers" not in u:
                    return
                try:
                    text = await resp.text()
                except Exception:
                    return
                if len(text) > 500:
                    payloads.append(text)

            page = await ctx.new_page()
            page.on("response", on_response)
            url = ADSTC_URL.format(region=region, domain=domain)
            try:
                await goto_with_retry(page, ADSTC_URL.format(region=region, domain=domain),
                                      wait_until="domcontentloaded", timeout=60000)
            except Exception as e:
                log.warning("[%s] google 로드 실패(%s): %s", brand_name, domain, e)
                await page.close()
                continue
            try:
                await page.wait_for_load_state("networkidle", timeout=20000)
            except Exception:
                pass
            for _ in range(scrolls):
                await page.mouse.wheel(0, 2500)
                await page.wait_for_timeout(1200)
            await page.close()

            # 디버그 덤프 (응답 스키마 확인·튜닝용)
            ts = asyncio.get_event_loop().time()
            for i, raw in enumerate(payloads):
                (debug_dir / f"{domain}_{int(ts)}_{i}.txt").write_text(
                    raw[:300000], encoding="utf-8", errors="ignore")

            texts, media = set(), set()
            for raw in payloads:
                for piece in _try_parse(raw):
                    _harvest(_loads_nested(piece), texts, media)

            for t in list(texts)[:max_refs_per_domain]:
                items.append(Reference(
                    platform="google_ads",
                    brand=brand_name,
                    external_id=hashlib.sha1(t.encode()).hexdigest()[:16],
                    url=ADSTC_URL.format(region=region, domain=domain),
                    body=t,
                    extra={"domain": domain, "category": category},
                ))
            for m in list(media)[:max_refs_per_domain]:
                items.append(Reference(
                    platform="google_ads",
                    brand=brand_name,
                    external_id="m" + hashlib.sha1(m.encode()).hexdigest()[:15],
                    url=ADSTC_URL.format(region=region, domain=domain),
                    title="(크리에이티브 이미지)",
                    media_urls=[m],
                    extra={"domain": domain, "category": category},
                ))
            log.info("[%s] google_ads %s: 텍스트 %d / 미디어 %d개 감지",
                     brand_name, domain, len(texts), len(media))
        await browser.close()
    return items
