"""문화 API 요청 주소 자동 탐색.

    docker compose exec api python scripts/find_culture_api.py

같은 '문화 데이터'라도 발급처(KCISA / 공공데이터포털)와 데이터셋에 따라 요청 주소가
전부 다르다. 문서를 뒤지는 대신 후보를 실제로 호출해 보고 데이터가 오는 주소를 찾는다.
찾으면 .env 에 넣을 한 줄을 그대로 출력한다.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("LLM_BACKEND", "fake")

from app.config import get_settings
from app.tools.http import close_client, get_json

# KCISA(문화공공데이터광장) — UUID 키. 공연·전시 계열 API 코드 후보.
KCISA_CODES = [
    "API_CCA_144", "API_CCA_145", "API_CCA_146", "API_CCA_147", "API_CCA_148",
    "API_CCA_149", "API_CCA_150", "API_CCA_151", "API_TOU_049", "API_CNV_060",
]
KCISA_BASE = "https://api.kcisa.kr/openapi/{code}/request"

# 공공데이터포털 계열
DATA_GO_KR = [
    "https://apis.data.go.kr/B553457/nopenapi/rest/publicperformancedisplays/period",
    "https://apis.data.go.kr/B553457/nopenapi/rest/publicperformancedisplays/area",
    "https://api.kcisa.kr/openapi/service/rest/meta16/getkopis01",
]


def looks_like_data(payload) -> tuple[bool, str]:
    """항목 리스트가 실제로 들어 있는지 확인하고, 샘플 제목을 뽑는다."""
    if not isinstance(payload, dict):
        return False, ""
    found: list[dict] = []

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k in ("item", "items", "perforList", "row") and v:
                    rows = v if isinstance(v, list) else [v]
                    if rows and isinstance(rows[0], dict):
                        found.extend(rows)
                walk(v)
        elif isinstance(node, list):
            for x in node:
                walk(x)

    walk(payload)
    if not found:
        return False, ""
    titles = []
    for row in found[:3]:
        for key in ("TITLE", "title", "SUBJECT", "subject"):
            if row.get(key):
                titles.append(str(row[key])[:30])
                break
    return True, " / ".join(titles)


def error_of(payload) -> str:
    if not isinstance(payload, dict):
        return "응답 없음"
    text = str(payload)
    for marker in ("NO_OPENAPI_SERVICE_ERROR", "SERVICE_KEY_IS_NOT_REGISTERED",
                   "LIMITED_NUMBER_OF_SERVICE_REQUESTS", "INVALID_REQUEST_PARAMETER",
                   "UNKNOWN_ERROR", "APPLICATION_ERROR"):
        if marker in text:
            return marker
    return text[:120].replace("\n", " ")


async def main() -> int:
    s = get_settings()
    if not s.culture_key:
        print("CULTURE_API_KEY 가 비어 있습니다.")
        return 1

    print("=" * 70)
    print(f"  키: {s.culture_key[:8]}…{s.culture_key[-4:]}  ({len(s.culture_key)}자)")
    print("  후보 주소를 하나씩 호출합니다. 데이터가 오는 곳을 찾으면 표시합니다.")
    print("=" * 70)

    winners: list[str] = []

    async def probe(url: str, params: dict) -> None:
        payload = await get_json(url, params=params, retries=0, name="probe")
        ok, sample = looks_like_data(payload)
        short = url.replace("https://", "")
        if ok:
            winners.append(url)
            print(f"  ✅ {short}")
            print(f"       예시: {sample}")
        else:
            print(f"  ❌ {short}")
            print(f"       {error_of(payload)}")

    print("\n[KCISA]")
    for code in KCISA_CODES:
        await probe(KCISA_BASE.format(code=code),
                    {"serviceKey": s.culture_key, "numOfRows": 3, "pageNo": 1})

    print("\n[공공데이터포털 계열]")
    from app.tools.weather import today_kst

    day = today_kst().strftime("%Y%m%d")
    for url in DATA_GO_KR:
        await probe(url, {"serviceKey": s.culture_key, "from": day, "to": day,
                          "cPage": 1, "rows": 3, "numOfRows": 3, "pageNo": 1})

    await close_client()

    print("\n" + "=" * 70)
    if winners:
        print("  아래 한 줄을 .env 에 넣고 컨테이너를 재시작하세요:\n")
        print(f"  CULTURE_API_ENDPOINT={winners[0]}")
        if len(winners) > 1:
            print(f"\n  (다른 후보: {', '.join(winners[1:])})")
    else:
        print("  데이터가 오는 주소를 찾지 못했습니다.")
        print("  발급처 마이페이지의 '활용신청 상세'에서 요청 URL을 확인해 주세요.")
        print("  KCISA: kcisa.kr → 마이페이지 → 오픈API 신청현황 → 상세")
    print("=" * 70)
    return 0 if winners else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
