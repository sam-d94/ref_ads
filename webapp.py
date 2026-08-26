"""수집 레퍼런스 뷰어 v3 — API 서버 + SPA (피처링AI 스타일)

실행:  python -X utf8 webapp.py            (브라우저 자동 열림)
       python -X utf8 webapp.py --no-open --port 7777
"""
import argparse
import csv
import hashlib
import io
import json
import mimetypes
import sqlite3
import sys
import threading
import urllib.request
import webbrowser
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote, urlparse

import yaml
from flask import (Flask, Response, abort, request, send_from_directory)

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

DB_PATH = BASE / "references" / "state.db"
MEDIA_ROOT = BASE / "references" / "_media"
STATIC_DIR = BASE / "static"
CONFIG_PATH = BASE / "config" / "brands.yaml"

mimetypes.add_type("image/webp", ".webp")
mimetypes.add_type("image/avif", ".avif")

PLATFORMS = {
    "meta_adlibrary": ("Meta 광고", "p-meta"),
    "google_ads": ("Google 광고", "p-google"),
    "youtube": ("YouTube", "p-youtube"),
    "tiktok": ("TikTok", "p-tiktok"),
    "instagram": ("Instagram", "p-insta"),
    "facebook": ("Facebook", "p-fb"),
}
PROXY_HOSTS = ("fbcdn.net", "googleusercontent.com", "ytimg.com",
               "tiktokcdn.com", "tiktokcdn-us.com", "tiktokv.com",
               "akamaized.net", "byteoversea.com", "googlesyndication.com")

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/static")


# ---------------------------------------------------------------- 설정/캐시
def _cfg_mtime():
    try:
        return CONFIG_PATH.stat().st_mtime
    except OSError:
        return 0.0


@lru_cache(maxsize=2)
def _full_cfg(mtime):
    """전체 설정 파싱 캐시"""
    try:
        return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


@lru_cache(maxsize=2)
def _cats(mtime):
    return _full_cfg(mtime).get("categories") or {}


@lru_cache(maxsize=2)
def _companies_cfg(mtime):
    return _full_cfg(mtime).get("companies") or {}


@lru_cache(maxsize=2)
def _bmap_cached(mtime):
    """{브랜드명: (카테고리키, 카테고리라벨, 법인키)} — 설정파일 mtime 기준 캐시"""
    out = {}
    for cat_key, cat in _cats(mtime).items():
        label = cat.get("label", cat_key)
        for b in cat.get("brands") or []:
            out[b["name"]] = (cat_key, label, (b.get("company") or ""))
    return out


def brand_map():
    return _bmap_cached(_cfg_mtime())


_row_cache = {"mtime": None, "rows": []}


def load_rows():
    """[(rowid, dict), ...] — DB mtime 기준 캐시"""
    if not DB_PATH.exists():
        return []
    try:
        mtime = DB_PATH.stat().st_mtime
    except OSError:
        mtime = None
    if _row_cache["mtime"] == mtime and _row_cache["rows"]:
        return _row_cache["rows"]
    con = sqlite3.connect(DB_PATH)
    raw = con.execute(
        "SELECT rowid, brand, platform, first_run, payload FROM seen "
        "ORDER BY first_run DESC, rowid DESC").fetchall()
    con.close()
    items = []
    for rid, brand, platform, first_run, payload in raw:
        try:
            d = json.loads(payload)
        except Exception:
            continue
        d["brand"] = brand
        d["platform"] = platform
        d["discovered"] = str(first_run)[:10]
        d["_rowid"] = rid
        items.append(d)
    _row_cache.update(mtime=mtime, rows=items)
    return items


def local_thumbnail(brand: str, platform: str, external_id: str):
    folder = MEDIA_ROOT / brand / platform
    if not folder.exists():
        return None
    for ext in (".webp", ".jpg", ".png"):
        f = folder / f"{external_id}{ext}"
        if f.exists():
            rel = str(f.relative_to(MEDIA_ROOT)).replace("\\", "/")
            return f"/media/{quote(rel)}"
    return None


def prep_item(d: dict) -> dict:
    label, cls = PLATFORMS.get(d["platform"], (d["platform"], ""))
    img, media_n = "", len(d.get("media_urls") or [])
    for m in (d.get("media_urls") or []):
        if isinstance(m, str) and m.startswith("http"):
            host = urlparse(m).netloc.lower()
            if any(host == h or host.endswith("." + h) for h in PROXY_HOSTS):
                img = "/proxy?url=" + quote(m, safe="")
                break
    if not img:
        img = local_thumbnail(d["brand"], d["platform"],
                              str(d.get("external_id", ""))) or ""
    extra = d.get("extra") or {}
    metrics = d.get("metrics") or {}
    bmap = brand_map()
    cat_key, cat_label, company = bmap.get(d["brand"], ("기타", "기타", ""))
    body = (d.get("body") or "").strip()
    title = (d.get("title") or "").strip()
    return {
        "id": d.get("_rowid"),
        "platform": d["platform"],
        "platform_label": label,
        "platform_cls": cls,
        "brand": d["brand"],
        "company": company,
        "category": cat_label,
        "title": title,
        "body": body,
        "img": img,
        "media_urls": [u for u in (d.get("media_urls") or [])
                       if isinstance(u, str) and u.startswith("http")],
        "media_count": media_n,
        "has_media": bool(media_n or img),
        "url": d.get("url") or "#",
        "landing": extra.get("link_url") or "",
        "page_name": extra.get("page_name") or "",
        "platforms_flag": extra.get("platforms") or "",
        "handle": extra.get("handle") or "",
        "metrics": metrics,
        "views": metrics.get("views") or 0,
        "likes": metrics.get("likes") or 0,
        "published_at": d.get("published_at") or "",
        "discovered": d.get("discovered", ""),
        "quality": ("high" if (img or media_n) and (body or title)
                    else "medium" if (body or title) else "low"),
    }


# 필터 키 정규화: 쉼표 다중값 허용
def _multi(name):
    raw = request.args.get(name, "") or ""
    return [v.strip() for v in raw.split(",") if v.strip()]


def apply_filters(rows, q="", categories=(), brands=(), platforms=(),
                  companies=(), has_media=None, min_views=0,
                  date_from="", date_to=""):
    bmap = brand_map()
    needle = (q or "").lower().strip()
    out = []
    for d in rows:
        if brands and d["brand"] not in brands:
            continue
        if platforms and d["platform"] not in platforms:
            continue
        cat_key, cat_label, company = bmap.get(d["brand"], ("기타", "기타", ""))
        if categories and cat_key not in categories \
                and cat_label not in categories:
            continue
        if companies and company not in companies:
            continue
        disc = d.get("discovered", "")
        if date_from and disc < date_from:
            continue
        if date_to and disc > date_to:
            continue
        if needle:
            hay = " ".join([d["brand"], company,
                            d.get("title") or "",
                            d.get("body") or "",
                            json.dumps(d.get("extra") or {}, ensure_ascii=False)])
            if needle not in hay.lower():
                continue
        it = prep_item(d)
        if has_media is True and not it["has_media"]:
            continue
        if min_views and (it["metrics"].get("views") or 0) < min_views:
            continue
        out.append(it)
    return out


SORTS = {
    "discovered": lambda x: (x["discovered"], x["id"]),
    "published": lambda x: x["published_at"] or "",
    "views": lambda x: x["views"],
    "likes": lambda x: x["likes"],
    "brand": lambda x: x["brand"],
}


def query_items():
    rows = load_rows()
    items = apply_filters(
        rows,
        q=request.args.get("q", ""),
        categories=_multi("category"), brands=_multi("brand"),
        platforms=_multi("platform"), companies=_multi("company"),
        has_media=(request.args.get("has_media", "").lower() in ("1", "true")),
        min_views=int(request.args.get("min_views") or 0),
        date_from=request.args.get("from", ""),
        date_to=request.args.get("to", ""),
    )
    sort = request.args.get("sort", "discovered")
    key = SORTS.get(sort, SORTS["discovered"])
    rev = sort != "brand"
    items.sort(key=key, reverse=rev)
    return items


# ---------------------------------------------------------------- API
@app.get("/api/version")
def api_version():
    return {"mtime": DB_PATH.stat().st_mtime if DB_PATH.exists() else 0,
            "total": len(load_rows())}


@app.get("/api/stats")
def api_stats():
    from storage.store import Store
    s = Store(DB_PATH.parent).stats() if DB_PATH.exists() else {}
    bmap = brand_map()
    by_cat = {}
    for brand, cnt in s.get("by_brand", []):
        cat = bmap.get(brand, ("기타", "기타", ""))[1]
        by_cat[cat] = by_cat.get(cat, 0) + cnt
    top_brands = [{"name": b, "count": c,
                   "category": bmap.get(b, ("기타", "기타", ""))[1],
                   "company": bmap.get(b, ("기타", "기타", ""))[2]}
                  for b, c in s.get("by_brand", [])[:10]]
    recent = [prep_item(d) for d in load_rows()[:12]]
    return {
        "total": s.get("total", 0), "today": s.get("today", 0),
        "brand_count": len(s.get("by_brand", [])),
        "media_ratio": s.get("media_ratio", 0),
        "by_platform": [{"key": k, "label": PLATFORMS.get(k, (k, ""))[0],
                         "cls": PLATFORMS.get(k, ("", ""))[1], "count": v}
                        for k, v in sorted(s.get("by_platform", {}).items(),
                                           key=lambda x: -x[1])],
        "by_category": by_cat,
        "top_brands": top_brands,
        "timeline": s.get("timeline", []),
        "recent": recent,
    }


@app.get("/api/brands")
def api_brands():
    rows = load_rows()
    bmap = brand_map()
    agg = {}
    for d in rows:
        a = agg.setdefault(d["brand"], {"count": 0, "platforms": {}})
        a["count"] += 1
        a["platforms"][d["platform"]] = a["platforms"].get(d["platform"], 0) + 1
    out = [{"name": name, "count": a["count"], "platforms": a["platforms"],
            "category": bmap.get(name, ("기타", "기타", ""))[0],
            "category_label": bmap.get(name, ("기타", "기타", ""))[1],
            "company": bmap.get(name, ("기타", "기타", ""))[2]}
           for name, a in sorted(agg.items(), key=lambda x: -x[1]["count"])]
    return {"brands": out}


@app.get("/api/companies")
def api_companies():
    """법인/모회사 목록 — 브랜드 수/아이템 수 포함"""
    rows = load_rows()
    bmap = brand_map()
    company_cfg = _companies_cfg(_cfg_mtime())
    # 아이템 카운트 집계
    item_counts = {}
    brand_counts = {}
    for d in rows:
        comp = bmap.get(d["brand"], ("", "", ""))[2] or ""
        if comp:
            item_counts[comp] = item_counts.get(comp, 0) + 1
            if comp not in brand_counts:
                brand_counts[comp] = set()
            brand_counts[comp].add(d["brand"])
    # 설정에 있는 모든 법인 + DB에서 발견된 법인
    all_companies = set(company_cfg.keys()) | set(item_counts.keys())
    out = []
    for comp in all_companies:
        cfg = company_cfg.get(comp, {})
        out.append({
            "name": comp,
            "label": cfg.get("label", comp),
            "en": cfg.get("en", comp),
            "brand_count": len(brand_counts.get(comp, set())),
            "item_count": item_counts.get(comp, 0),
            "brands": sorted(brand_counts.get(comp, set())),
        })
    out.sort(key=lambda x: -x["item_count"])
    return {"companies": out}


@app.get("/api/items")
def api_items():
    per = min(int(request.args.get("per_page") or 48), 120)
    items = query_items()
    page = max(1, int(request.args.get("page") or 1))
    total = len(items)
    pages = max(1, -(-total // per))
    page = min(page, pages)
    chunk = items[(page - 1) * per: page * per]
    return {"items": chunk, "total": total, "page": page,
            "pages": pages, "per_page": per}


@app.get("/api/item/<int:rid>")
def api_item(rid):
    for d in load_rows():
        if d.get("_rowid") == rid:
            return prep_item(d) | {
                "extra": d.get("extra") or {},
                "media_urls": d.get("media_urls") or [],
                "content_hash": d.get("content_hash") or "",
                "dedupe_key": f"{d['platform']}:{d['brand']}:{d.get('external_id')}",
                "tags": (d.get("extra") or {}).get("tags") or [],
            }
    abort(404)


CSV_COLS = ["platform", "brand", "company", "title", "body", "published_at",
            "discovered", "url", "landing", "page_name", "platforms_flag",
            "views", "likes", "media_urls", "quality"]


@app.get("/api/export.csv")
def api_export():
    items = query_items()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_COLS)
    for it in items:
        w.writerow([
            it["platform"], it["brand"], it.get("company", ""),
            it["title"], it["body"],
            it["published_at"], it["discovered"], it["url"], it["landing"],
            it["page_name"], it["platforms_flag"], it["views"], it["likes"],
            " | ".join(it.get("media_urls", [])), it["quality"],
        ])
    data = "\ufeff" + buf.getvalue()  # Excel 한글 BOM
    fname = f"references_{datetime.now():%Y%m%d_%H%M}.csv"
    return Response(data, mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition":
                             f"attachment; filename={fname}"})


def _raw_media(item):  # 하위호환용 (CSV는 media_urls 직접 사용)
    if item["img"].startswith("/proxy?url="):
        return [item["img"].split("/proxy?url=", 1)[1]]
    return []


# ---------------------------------------------------------------- 정적/미디어
@app.get("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


PROXY_CACHE = BASE / "references" / "_cache" / "proxy"


@app.get("/proxy")
def proxy():
    url = request.args.get("url", "")
    host = urlparse(url).netloc.lower()
    if not url.startswith("http") or not any(
            host == h or host.endswith("." + h) for h in PROXY_HOSTS):
        abort(400)

    key = hashlib.sha1(url.encode()).hexdigest()
    body_f = PROXY_CACHE / key
    type_f = PROXY_CACHE / f"{key}.type"
    PROXY_CACHE.mkdir(parents=True, exist_ok=True)

    if body_f.exists() and type_f.exists():
        resp = Response(body_f.read_bytes(),
                        mimetype=type_f.read_text().strip() or "image/jpeg")
        resp.headers["Cache-Control"] = "public, max-age=604800"
        resp.headers["X-Cache"] = "HIT"
        return resp

    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    })
    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            data = r.read(5_000_000)
            ctype = r.headers.get("Content-Type", "image/jpeg").split(";")[0]
    except Exception:
        abort(502)
    try:
        body_f.write_bytes(data)
        type_f.write_text(ctype)
    except Exception:
        pass
    resp = Response(data, mimetype=ctype)
    resp.headers["Cache-Control"] = "public, max-age=604800"
    resp.headers["X-Cache"] = "MISS"
    return resp


@app.get("/media/<path:p>")
def media(p):
    return send_from_directory(MEDIA_ROOT, p)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=7777)
    ap.add_argument("--no-open", action="store_true")
    a = ap.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    url = f"http://127.0.0.1:{a.port}"
    if not DB_PATH.exists():
        print("[!] references/state.db 없음 — 먼저 main.py를 실행해 데이터를 수집하세요.")
    print(f"뷰어 실행 중: {url}")
    if not a.no_open:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=a.port, debug=False)


if __name__ == "__main__":
    main()
