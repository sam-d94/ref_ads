"""저장소: 스냅샷(JSON) + 중복 제거 상태(SQLite) + 마크다운 리포트 + 통계"""
import hashlib
import json
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from collectors.base import Reference


# ---------- 정규화 (데이터 정합성) ----------
def norm_date(v):
    """유닉스초 / yyyymmdd / ISO → 'YYYY-MM-DD'"""
    s = str(v or "").strip()
    if not s:
        return ""
    if s.isdigit():
        if len(s) == 10:
            return time.strftime("%Y-%m-%d", time.gmtime(int(s)))
        if len(s) == 8:
            return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
        return ""
    m = re.match(r"(\d{4}-\d{2}-\d{2})", s)
    return m.group(1) if m else s[:10]


def norm_metrics(metrics):
    """숫자는 숫자로, 나머지는 안전한 문자열로"""
    out = {}
    for k, v in (metrics or {}).items():
        if v in (None, ""):
            continue
        try:
            out[k] = int(v)
        except (TypeError, ValueError):
            try:
                out[k] = float(v)
            except (TypeError, ValueError):
                out[k] = str(v)[:120]
    return out


def normalize(item: Reference) -> Reference:
    item.title = " ".join((item.title or "").split())
    item.body = " ".join((item.body or "").split())
    item.url = (item.url or "").strip()
    item.published_at = norm_date(item.published_at)
    item.metrics = norm_metrics(item.metrics)
    item.media_urls = [u for u in (item.media_urls or []) if str(u).startswith("http")]
    return item


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "state.db"
        self._init_db()

    def _connect(self):
        con = sqlite3.connect(self.db_path)
        try:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA synchronous=NORMAL")
        except Exception:
            pass
        return con

    def _init_db(self):
        with self._connect() as con:
            con.execute("""
                CREATE TABLE IF NOT EXISTS seen (
                    dedupe_key TEXT PRIMARY KEY,
                    brand      TEXT NOT NULL,
                    platform   TEXT NOT NULL,
                    first_run  TEXT NOT NULL,
                    last_run   TEXT NOT NULL,
                    payload    TEXT NOT NULL
                )
            """)
            con.execute("CREATE INDEX IF NOT EXISTS idx_seen_brand ON seen(brand)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_seen_platform ON seen(platform)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_seen_last ON seen(last_run)")
            # 데이터 정합성 컬럼(마이그레이션): 미디어 보유 플래그
            cols = {r[1] for r in con.execute("PRAGMA table_info(seen)").fetchall()}
            if "has_media" not in cols:
                con.execute("ALTER TABLE seen ADD COLUMN has_media INTEGER NOT NULL DEFAULT 0")
            # 과거 행 백필: 미디어 배열이 비어있지 않으면 1로
            con.execute(
                "UPDATE seen SET has_media=1 "
                "WHERE has_media=0 AND instr(payload, '\"media_urls\": []')=0")

    def upsert_many(self, items):
        """정규화 + 전체 기록 + 이번 실행에서 처음 본 아이템만 반환"""
        run = datetime.now().isoformat(timespec="seconds")
        new_items = []
        with self._connect() as con:
            for item in items:
                normalize(item)
                key = item.dedupe_key()
                cur = con.execute("SELECT 1 FROM seen WHERE dedupe_key=?", (key,))
                if cur.fetchone() is None:
                    new_items.append(item)
                    first = run
                else:
                    first = con.execute(
                        "SELECT first_run FROM seen WHERE dedupe_key=?", (key,)
                    ).fetchone()[0]
                d = item.to_dict()
                # 콘텐츠 해시 — 제목/본문/첫 미디어 기반 정합성 검증용
                d["content_hash"] = hashlib.sha1(
                    "|".join([item.platform, item.brand, item.title,
                              item.body[:200],
                              (item.media_urls or [""])[0]]).encode()
                ).hexdigest()[:16]
                con.execute(
                    """INSERT INTO seen(dedupe_key, brand, platform, first_run, last_run, payload, has_media)
                       VALUES(?,?,?,?,?,?,?)
                       ON CONFLICT(dedupe_key)
                       DO UPDATE SET last_run=excluded.last_run,
                                     has_media=excluded.has_media,
                                     payload=excluded.payload""",
                    (key, item.brand, item.platform, first, run,
                     json.dumps(d, ensure_ascii=False),
                     1 if item.media_urls else 0),
                )
        return new_items

    def known_ids(self, brand=None, platform=None):
        """해당 조건의 기수집 external_id 집합 — 증분 수집용"""
        q = "SELECT dedupe_key FROM seen WHERE 1=1"
        args = []
        if brand:
            q += " AND brand=?"
            args.append(brand)
        if platform:
            q += " AND platform=?"
            args.append(platform)
        with self._connect() as con:
            keys = [r[0] for r in con.execute(q, args)]
        return {k.split(":", 2)[2] for k in keys if k.count(":") >= 2}

    def mtime(self):
        try:
            return self.db_path.stat().st_mtime
        except OSError:
            return None

    def stats(self):
        """대시보드용 집계 — SQL 레벨에서 한 번에"""
        with self._connect() as con:
            total = con.execute("SELECT COUNT(*) FROM seen").fetchone()[0]
            today = con.execute(
                "SELECT COUNT(*) FROM seen WHERE substr(first_run,1,10)=?",
                (datetime.now().strftime("%Y-%m-%d"),)).fetchone()[0]
            by_platform = dict(con.execute(
                "SELECT platform, COUNT(*) FROM seen GROUP BY platform").fetchall())
            by_brand = con.execute(
                "SELECT brand, COUNT(*) FROM seen GROUP BY brand ORDER BY 2 DESC"
            ).fetchall()
            timeline = con.execute(
                "SELECT substr(first_run,1,10) d, COUNT(*) FROM seen "
                "WHERE first_run >= datetime('now','-13 days','localtime') "
                "GROUP BY d ORDER BY d").fetchall()
            quality = con.execute(
                "SELECT SUM(has_media), COUNT(*) FROM seen").fetchone()
        with_media = quality[0] or 0
        return {
            "total": total, "today": today,
            "by_platform": by_platform, "by_brand": by_brand,
            "timeline": timeline,
            "media_ratio": round(with_media / total * 100) if total else 0,
        }

    def save_snapshot(self, category, brand, platform, items):
        date = datetime.now().strftime("%Y-%m-%d")
        d = self.root / category / brand / platform / date
        d.mkdir(parents=True, exist_ok=True)
        path = d / "items.json"
        data = []
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
        data.extend(i.to_dict() for i in items)
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return path

    def write_report(self, sections):
        """sections: {(category, brand, platform): [신규 Reference, ...]}"""
        today = datetime.now().strftime("%Y-%m-%d")
        total = sum(len(v) for v in sections.values())
        lines = [f"# 레퍼런스 수집 리포트 — {today}", "", f"**전체 신규 {total}건**", ""]
        for (cat, brand, platform), items in sorted(sections.items()):
            if not items:
                continue
            lines.append(f"## {brand} — {platform} ({cat}) · 신규 {len(items)}건")
            for it in items[:50]:
                label = (it.title or it.body or "(텍스트 없음)").strip().splitlines()[0][:80]
                lines.append(f"- [{label}]({it.url})" if it.url else f"- {label}")
                if it.metrics:
                    m = " · ".join(f"{k}={v}" for k, v in it.metrics.items() if v not in (None, ""))
                    if m:
                        lines.append(f"  - {m}")
            if len(items) > 50:
                lines.append(f"- … 외 {len(items) - 50}건")
            lines.append("")
        path = self.root / f"report_{today}.md"
        path.write_text("\n".join(lines), encoding="utf-8")
        return path
