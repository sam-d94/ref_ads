# RefLens — 브랜드 광고 레퍼런스 인텔리전스

![Python](https://img.shields.io/badge/Python-3.10+-blue)
![Platform](https://img.shields.io/badge/Platform-Windows-green)

> Meta 광고, Google 광고, YouTube, TikTok 크리에이티브를 자동 수집하고
> **법인/모회사** 관점에서 브랜드 광고 전략을 분석하는 대시보드

**GitHub**: https://github.com/sam-d94/ref_ads

## 특징

- **API 키 불필요** — Playwright, yt-dlp 기반 완전 자동 수집
- **법인/모회사 계층** — CJ제일제당, LVMH, LG生活健康 등 법인별 브랜드 그룹핑
- **6개 플랫폼** — Meta Ad Library, Google Ads Transparency, YouTube, TikTok, Instagram, Facebook
- **피처링AI 스타일 UI** — 접이식 법인 섹션, 라디오형 칩 필터, 무한스크롤 갤러리
- **실시간 대시보드** — KPI, 수집 추이 차트, 플랫폼 도넛, 브랜드 랭킹

## 설치

```bash
pip install -r requirements.txt
playwright install chromium
```

## 실행

```bash
# 전체 수집
python main.py

# 플랫폼/카테고리/브랜드 선택
python main.py --platforms meta,youtube
python main.py --categories beauty
python main.py --brand 올리브영

# 웹 대시보드 (http://127.0.0.1:7777)
python -X utf8 webapp.py
```

## 아키텍처

```
brand-ref-collector/
├── main.py                    # 비동기 수집 오케스트레이터
├── webapp.py                  # Flask API 서버 + SPA
├── static/index.html          # 피처링AI 스타일 SPA UI
├── config/brands.yaml         # 22개 브랜드 + 법인 매핑
├── collectors/
│   ├── base.py                # Reference 데이터 모델
│   ├── meta_adlibrary.py      # GraphQL 가로채기 수집
│   ├── google_ads.py          # SearchCreatives RPC 가로채기
│   ├── social_ytdlp.py        # yt-dlp 증분 수집
│   └── meta_social.py         # Instagram/Facebook 세션
├── storage/store.py           # SQLite 저장소 + 정규화
└── tools/                     # 디버깅 도구
```

## API

| 엔드포인트 | 설명 |
|---|---|
| `GET /api/stats` | 대시보드 통계 (누적/오늘/플랫폼별/카테고리별) |
| `GET /api/items` | 레퍼런스 목록 (필터/정렬/페이지네이션) |
| `GET /api/item/<id>` | 상세 조회 |
| `GET /api/brands` | 브랜드별 아이템 수 |
| `GET /api/companies` | **법인/모회사** 목록 + 브랜드 매핑 |
| `GET /api/export.csv` | CSV 내보내기 (Excel BOM) |

## 법인(모회사) 기능

`config/brands.yaml`에 각 브랜드의 `company` 필드를 지정하면:
- 대시보드에서 법인별로 브랜드를 그룹핑하여 표시
- 법인별 필터로 특정 모회사 산하 브랜드만 탐색
- CSV 내보내기 시 법인 컬럼 포함

```yaml
companies:
  CJ올리브영: { label: CJ올리브영, en: OliveYoung }
  LVMH: { label: LVMH, en: LVMH }

categories:
  beauty:
    brands:
      - name: 올리브영
        company: CJ올리브영    # 법인 지정
      - name: 닥터자르트
        company: LVMH          # LVMH 산하
```

## 성능

| 항목 | 개선 전 | 개선 후 |
|---|---|---|
| 유튜브 반복 수집 | 37초 | **2초** (증분) |
| 웹 `/api/items` | 5.6초 | **27ms** (lru 캐시) |
| 이미지 프록시 | 매 요청 CDN | 디스크 캐시 HIT |
| Meta 스크롤 | 고정 8회 | 성장 정지 시 조기종료 |

## 주의

- 각 플랫폼 약관상 무단 자동 수집은 회색지역입니다. 내부 리서치 용도·소규모로 사용하세요.
- `config/brands.yaml`의 핸들/도메인은 예시값입니다. 실행 전 실제 값으로 수정하세요.
