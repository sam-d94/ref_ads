"""YouTube / TikTok 수집기 — yt-dlp 기반 (API 키 불필요)

메타데이터 + 썸네일을 저장합니다(영상 본문은 미저장, 옵션으로 가능).
"""
import asyncio
import logging
import pathlib

import yt_dlp

from .base import Reference

log = logging.getLogger("ytdlp")

YT_OPTS = {
    "skip_download": True,
    "writethumbnail": True,       # skip_download와 함께 → 썸네일만 저장
    "writeinfojson": True,
    "quiet": True,
    "no_warnings": True,
    "ignoreerrors": True,
    "outtmpl": "%(id)s.%(ext)s",
}


def _sync_collect(url: str, out_dir: pathlib.Path, limit: int, known=None):
    """증분 수집: 1단계로 채널/프로필을 flat 추출해 빠르게 목록화(수 초),
    기수집되지 않은 ID만 2단계에서 상세정보+썸네일 다운로드."""
    known = known or set()
    list_opts = dict(YT_OPTS)
    list_opts.update({"extract_flat": "in_playlist", "playlistend": limit or 30,
                      "outtmpl": "-", "skip_download": True,
                      "writethumbnail": False, "writeinfojson": False})
    with yt_dlp.YoutubeDL(list_opts) as ydl:
        info = ydl.extract_info(url, download=False)
    raw = (info or {}).get("entries") or [info]
    listing = [e for e in raw if e and e.get("id")]

    detail_opts = dict(YT_OPTS)
    detail_opts["outtmpl"] = str(out_dir / "%(id)s.%(ext)s")
    entries = []
    with yt_dlp.YoutubeDL(detail_opts) as ydl:
        for e in listing[:limit or 30]:
            eid = e.get("id")
            if eid in known:
                continue
            target = e.get("url") or e.get("webpage_url") \
                or f"{url.rstrip('/')}/{eid}"
            try:
                full = ydl.extract_info(target, download=True)
            except Exception as ex:
                log.debug("상세 추출 실패 %s: %s", eid, ex)
                continue
            if full:
                entries.append(full)
            elif not known:  # flat 실패로 보이면 전체 경로 폴백
                return _sync_collect_full(url, out_dir, limit)
    return entries


def _sync_collect_full(url: str, out_dir: pathlib.Path, limit: int):
    """구 방식 전체 추출 (폴백용)"""
    opts = dict(YT_OPTS)
    opts["playlistend"] = limit or 30
    opts["outtmpl"] = str(out_dir / "%(id)s.%(ext)s")
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
    if info:
        raw = info.get("entries") or [info]
        return [e for e in raw if e]
    return []


def _fmt_date(v):
    s = str(v or "")
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 else s


async def collect_youtube(category: str, brand_name: str, handles: list,
                          settings: dict, out_root: str = "references",
                          known=None):
    limits = settings.get("limits", {})
    out_dir = pathlib.Path(out_root) / "_media" / brand_name / "youtube"
    out_dir.mkdir(parents=True, exist_ok=True)
    items = []
    for h in handles or []:
        handle = h if str(h).startswith("@") else f"@{h}"
        url = f"https://www.youtube.com/{handle}/videos"
        try:
            entries = await asyncio.to_thread(
                _sync_collect, url, out_dir, int(limits.get("youtube", 30)),
                known)
        except Exception as e:
            log.warning("[%s] youtube %s 실패: %s", brand_name, handle, e)
            continue
        for e in entries:
            vid = e.get("id")
            if not vid:
                continue
            items.append(Reference(
                platform="youtube",
                brand=brand_name,
                external_id=vid,
                url=e.get("webpage_url") or f"https://www.youtube.com/watch?v={vid}",
                title=e.get("title") or "",
                metrics={
                    "views": e.get("view_count"),
                    "likes": e.get("like_count"),
                    "comments": e.get("comment_count"),
                },
                media_urls=[e["thumbnail"]] if e.get("thumbnail") else [],
                published_at=_fmt_date(e.get("upload_date")),
                extra={"channel": e.get("channel"), "duration": e.get("duration"),
                       "tags": (e.get("tags") or [])[:10], "category": category,
                       "handle": handle},
            ))
        log.info("[%s] youtube %s: %d건", brand_name, handle, len(entries))
    return items


async def collect_tiktok(category: str, brand_name: str, handles: list,
                         settings: dict, out_root: str = "references",
                         known=None):
    limits = settings.get("limits", {})
    out_dir = pathlib.Path(out_root) / "_media" / brand_name / "tiktok"
    out_dir.mkdir(parents=True, exist_ok=True)
    items = []
    for h in handles or []:
        handle = str(h).lstrip("@")
        url = f"https://www.tiktok.com/@{handle}"
        try:
            entries = await asyncio.to_thread(
                _sync_collect, url, out_dir, int(limits.get("tiktok", 30)),
                known)
        except Exception as e:
            log.warning("[%s] tiktok @%s 실패: %s", brand_name, handle, e)
            continue
        for e in entries:
            vid = e.get("id")
            if not vid:
                continue
            items.append(Reference(
                platform="tiktok",
                brand=brand_name,
                external_id=str(vid),
                url=e.get("webpage_url") or "",
                title=e.get("title") or "",
                metrics={"views": e.get("view_count"), "likes": e.get("like_count"),
                         "comments": e.get("comment_count"),
                         "shares": e.get("share_count")},
                media_urls=[e["thumbnail"]] if e.get("thumbnail") else [],
                published_at=_fmt_date(e.get("timestamp")),
                extra={"music": ((e.get("track") or e.get("creator") or "")),
                       "category": category, "handle": handle},
            ))
        log.info("[%s] tiktok @%s: %d건", brand_name, handle, len(entries))
    return items
