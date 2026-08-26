# 브랜드 레퍼런스 통합 수집기

건강기능식품 · 뷰티 미디어커머스 업계 전체의 광고/콘텐츠 레퍼런스를
**API 키 없이**, 모든 플랫폼에서 **동시에** 수집합니다.

| 플랫폼 | 방식 | 로그인 | 안정성 |
|---|---|---|---|
| Meta Ad Library | Playwright + JSON 응답 가로채기 | 불필요 | 보통 |
| Google Ads Transparency Center | Playwright + 네트워크 덤프(베스트 에포트) | 불필요 | 낮음~보통 |
| YouTube / TikTok | yt-dlp | 불필요 | 좋음 |
| Instagram / Facebook | Playwright + 세션 쿠키 | 필요 | 낮음 (기본 비활성) |

## 설치

```powershell
cd C:\Users\qwer2\brand-ref-collector
python -m pip install -r requirements.txt
python -m playwright install chromium
```

## 실행

```powershell
python main.py                              # 전체 브랜드 × 전체 플랫폼 동시 수집
python main.py --platforms meta,youtube     # 플랫폼 선택
python main.py --categories beauty          # 카테고리 선택 (health_food|beauty)
python main.py --brand 올리브영              # 브랜드 1곳만
python main.py --headed                     # 디버그: 브라우저 창 표시
```

## 출력 구조

```
references/
├── state.db                      # 중복 제거 상태 (SQLite)
├── report_YYYY-MM-DD.md          # 신규 감지 아이템 리포트
├── _debug/google/*.txt           # Google 응답 덤프 (스키마 튜닝용)
├── _media/{브랜드}/youtube/*.jpg  # 썸네일 파일
└── {카테고리}/{브랜드}/{플랫폼}/{날짜}/items.json
```

매일 실행해도 `state.db` 기준으로 **새로 나온 것만** 리포트에 남습니다.

## Instagram 활성화 (선택 — 계정 리스크 있음)

```powershell
python tools/save_ig_session.py     # 1회: 브라우저에서 직접 로그인 → 쿠키 저장
# config/brands.yaml 에서 settings.instagram.enabled: true 로 변경
```

전용 계정 사용 + 하루 1회 이내 실행을 권장합니다.

## 웹 뷰어 v2 — RefLens 대시보드

```powershell
python -X utf8 webapp.py            # http://127.0.0.1:7777 자동 열림
python -X utf8 webapp.py --no-open  # 브라우저 자동 열림 끔
```

- **대시보드**: KPI(누적/오늘 신규/브랜드수/미디어보유율), 14일 수집 추이 차트,
  플랫폼 도넛 분포, 브랜드 TOP10 랭킹, 최근 발견 카드
- **레퍼런스 탐색**: 무한스크롤 갤러리 + 사이드바 다중 필터(카테고리/플랫폼/브랜드
  체크박스) + 통합검색(`/` 단축키) + 정렬(발견/게시일/조회수/좋아요) + 미디어 보유만 토글
- **상세 모달**: 원본 크리에이티브, 전체 카피 복사, 지표표, 집행플랫폼, 랜딩링크,
  정합성 해시(content_hash)
- **편의기능**: 필터 상태 URL 공유(#해시), 다크/라이트 테마 저장, 새 데이터 감지 알림,
  **CSV 내보내기**(현재 필터 그대로, Excel 한글 BOM 지원)

### 성능 설계
| 항목 | 방식 |
|---|---|
| 반복 수집 | 증분 수집 — 기수집 영상은 flat 목록만 확인(37초→2초), Meta 스크롤 조기종료 |
| 웹 응답 | DB mtime 캐시 + lru 캐시(5.6초→27ms), SQLite WAL + 인덱스 |
| 이미지 | 프록시 디스크 캐시(MISS→HIT), YouTube 썸네일 로컬 서빙 |
| 정합성 | 저장 시 정규화(날짜/지표/URL) + content_hash + 재수집 시 payload 갱신 |

## 유지보수 팁

- **Meta**: UI 변경 시 `python tools/debug_meta.py "검색어"` 로 실제 응답을 덤프해 확인.
- **Google**: `python tools/debug_google.py 도메인` 으로 RPC 스키마 확인 후
  `google_ads.py` 의 `_harvest` 휴리스틱을 정밀화하세요.
- **yt-dlp**: 주기적으로 `python -m pip install -U yt-dlp` 업데이트 필수.

## 주의

- 각 플랫폼 약관상 무단 자동 수집은 회색지역입니다. 내부 리서치 용도·소규모로 사용하세요.
- `config/brands.yaml`의 핸들/도메인은 예시값입니다. 실행 전 실제 값으로 수정하세요
  (잘못된 핸들은 자동 스킵됩니다).
- 대량·상업적 수집이 필요해지면 Apify 같은 관리형 서비스로 전환이 현실적입니다.
