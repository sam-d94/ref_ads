"""브랜드 레퍼런스 통합 수집기 — 모든 플랫폼 동시 실행 오케스트레이터

사용 예:
    python main.py                          # 전체 브랜드 × 전체 플랫폼
    python main.py --platforms meta,google  # 특정 플랫폼만
    python main.py --brand 올리브영          # 특정 브랜드만
    python main.py --categories beauty      # 특정 카테고리만
    python main.py --headed                 # 브라우저 창 보이며 실행(디버그)
"""
import argparse
import asyncio
import logging
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent))

import yaml  # noqa: E402

from collectors import google_ads, meta_adlibrary, meta_social, social_ytdlp  # noqa: E402
from storage.store import Store  # noqa: E402

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)-7s %(name)-8s | %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("main")

ALL_PLATFORMS = ["meta", "google", "youtube", "tiktok", "instagram", "facebook"]


def load_config(path: str) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def iter_brands(cfg: dict, categories=None, brand=None):
    for cat_key, cat in (cfg.get("categories") or {}).items():
        if categories and cat_key not in categories:
            continue
        for b in cat.get("brands") or []:
            if brand and b["name"] != brand:
                continue
            yield cat_key, cat.get("label", cat_key), b


async def run(cfg: dict, args):
    s = cfg.get("settings", {})
    store = Store(s.get("out_root", "references"))
    sem_pw = asyncio.Semaphore(int(s.get("concurrency", {}).get("playwright", 2)))
    sem_yt = asyncio.Semaphore(int(s.get("concurrency", {}).get("ytdlp", 3)))
    headless = not args.headed
    platforms = set(args.platforms)

    async def guarded(sem, fn, *a, **k):
        async with sem:
            return await fn(*a, **k)

    tasks = []
    sections = {}   # (cat, brand, platform) -> 신규 Reference 리스트
    counts = {}     # (cat, brand, platform) -> 수집 건수

    def add_task(cat, label, bname, platform, factory):
        async def runner():
            try:
                items = await factory()
                new = store.upsert_many(items)
                if items:
                    path = store.save_snapshot(cat, bname, platform, items)
                    log.info("+ %-28s 저장: %s", label, path)
                else:
                    log.info(". %-28s 결과 없음", label)
                sections[(cat, bname, platform)] = new
                counts[(cat, bname, platform)] = len(items)
            except Exception as e:
                log.exception("! %s 실패: %s", label, e)
                sections[(cat, bname, platform)] = []
                counts[(cat, bname, platform)] = 0
        tasks.append(runner())

    n_jobs = 0
    for cat, _label, b in iter_brands(cfg, args.categories, args.brand):
        name = b["name"]

        if "meta" in platforms:
            scrolls = int(s.get("scrolls", {}).get("meta_adlibrary", 8))
            for kw in (b.get("meta_keywords") or []) or [name]:
                add_task(cat, f"meta:{name}:{kw}", name, "meta_adlibrary",
                         lambda kw=kw, name=name, cat=cat: guarded(
                             sem_pw, meta_adlibrary.collect, cat, name, kw, s,
                             headless=headless, scrolls=scrolls))
                n_jobs += 1

        if "google" in platforms and b.get("google_domains"):
            add_task(cat, f"google:{name}", name, "google_ads",
                     lambda name=name, cat=cat: guarded(
                         sem_pw, google_ads.collect, cat, name, b["google_domains"], s,
                         headless=headless,
                         scrolls=int(s.get("scrolls", {}).get("google_ads", 6))))
            n_jobs += 1

        if "youtube" in platforms and b.get("youtube"):
            add_task(cat, f"youtube:{name}", name, "youtube",
                     lambda name=name, cat=cat: guarded(
                         sem_yt, social_ytdlp.collect_youtube, cat, name,
                         b["youtube"], s,
                         out_root=s.get("out_root", "references"),
                         known=store.known_ids(name, "youtube")))
            n_jobs += 1

        if "tiktok" in platforms and b.get("tiktok"):
            add_task(cat, f"tiktok:{name}", name, "tiktok",
                     lambda name=name, cat=cat: guarded(
                         sem_yt, social_ytdlp.collect_tiktok, cat, name,
                         b["tiktok"], s,
                         out_root=s.get("out_root", "references"),
                         known=store.known_ids(name, "tiktok")))
            n_jobs += 1

        if "instagram" in platforms and s.get("instagram", {}).get("enabled") \
                and b.get("instagram"):
            add_task(cat, f"instagram:{name}", name, "instagram",
                     lambda name=name, cat=cat: guarded(
                         sem_pw, meta_social.collect_instagram, cat, name,
                         b["instagram"], s, headless=headless))
            n_jobs += 1

        if "facebook" in platforms and s.get("facebook", {}).get("enabled") \
                and b.get("facebook"):
            add_task(cat, f"facebook:{name}", name, "facebook",
                     lambda name=name, cat=cat: guarded(
                         sem_pw, meta_social.collect_facebook, cat, name,
                         b["facebook"], s, headless=headless))
            n_jobs += 1

    log.info("총 %d개 수집 작업을 병렬로 시작합니다...", len(tasks))
    await asyncio.gather(*tasks)

    report = store.write_report(sections)
    collected = sum(counts.values())
    new_total = sum(len(v) for v in sections.values())
    print("\n" + "=" * 60)
    print(f"[수집 완료] 작업 {len(tasks)}건 - 아이템 {collected}건 - 신규 {new_total}건")
    print(f"리포트: {report}")
    print("=" * 60)


def parse_args():
    ap = argparse.ArgumentParser(description="브랜드 레퍼런스 통합 수집기")
    ap.add_argument("--config", default="config/brands.yaml")
    ap.add_argument("--platforms", default=",".join(ALL_PLATFORMS),
                    help="쉼표 구분: meta,google,youtube,tiktok,instagram,facebook")
    ap.add_argument("--categories", default=None,
                    help="쉼표 구분: health_food,beauty")
    ap.add_argument("--brand", default=None, help="특정 브랜드명만")
    ap.add_argument("--headed", action="store_true",
                    help="브라우저 창을 보이며 실행 (디버그)")
    args = ap.parse_args()
    args.platforms = [p.strip() for p in args.platforms.split(",") if p.strip()]
    args.categories = ([c.strip() for c in args.categories.split(",")]
                       if args.categories else None)
    return args


if __name__ == "__main__":
    a = parse_args()
    asyncio.run(run(load_config(a.config), a))
