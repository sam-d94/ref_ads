"""공통 데이터 모델 + 유틸"""
from dataclasses import dataclass, field


async def goto_with_retry(page, url, attempts: int = 3, **kwargs):
    """네트워크 흔들림 대비 재시도 goto (지수 백오프)"""
    last = None
    for i in range(attempts):
        try:
            return await page.goto(url, **kwargs)
        except Exception as e:  # net::ERR_* 등 일시 실패
            last = e
            await page.wait_for_timeout(1200 * (i + 1))
    raise last


@dataclass
class Reference:
    platform: str          # meta_adlibrary | google_ads | youtube | tiktok | instagram | facebook
    brand: str             # 브랜드명 (brands.yaml 기준)
    external_id: str       # 플랫폼 내 고유 ID
    url: str = ""
    title: str = ""
    body: str = ""
    metrics: dict = field(default_factory=dict)     # 조회수/좋아요 등
    media_urls: list = field(default_factory=list)  # 크리에이티브 미디어 URL
    published_at: str = ""
    extra: dict = field(default_factory=dict)

    def dedupe_key(self) -> str:
        return f"{self.platform}:{self.brand}:{self.external_id}"

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["dedupe_key"] = self.dedupe_key()
        return d
