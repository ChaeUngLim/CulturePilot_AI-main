"""외부 API 연결 점검.

    docker compose exec api python scripts/check_apis.py

각 API를 실제로 한 번씩 호출하고, 실패하면 '무엇을 고쳐야 하는지'까지 알려준다.
JSON을 읽고 해석하는 대신 결과를 그대로 보고 판단할 수 있게 만드는 게 목적이다.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# 컨테이너에서 `python scripts/check_apis.py` 로 실행하면 sys.path[0] 이 scripts/ 가 되어
# app 패키지를 찾지 못한다. 프로젝트 루트를 직접 넣어 어디서 실행하든 동작하게 한다.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("LLM_BACKEND", "fake")

from app.config import get_settings
from app.schemas import GeoPoint, TripConditions
from app.tools import culture_api, maps, weather, websearch
from app.tools.http import close_client, last_error

OK, NG, SKIP = "✅", "❌", "⏭️ "
SEOUL = GeoPoint(lat=37.5665, lng=126.9780, name="서울시청")

HINTS = {
    "kma": "apihub.kma.go.kr → '4. 동네예보 조회'의 4.1/4.2/4.3 각각 [API 활용신청]",
    "culture": "데이터셋이 다를 수 있습니다. 발급 화면의 '참고문서(API 명세서)'에서 "
               "요청 URL을 확인해 .env의 CULTURE_API_ENDPOINT 에 넣으세요.",
    "ncp": "console.ncloud.com/maps/application → Application 수정 → "
           "Geocoding / Directions 5 체크 확인",
    "local": "developers.naver.com/apps/#/register 에서 '검색' API 등록 후 "
             ".env의 NAVER_SEARCH_CLIENT_ID / SECRET 입력",
    "ors": "openrouteservice.org/dev/#/signup 에서 무료 가입 → Request a token → "
           "Standard 토큰을 .env의 ORS_API_KEY 에 입력 (무료 2,500건/일)",
    "odsay": "lab.odsay.com 가입 → API 이용신청 → apiKey 를 .env의 ODSAY_API_KEY 에 입력 "
             "(무료 1,000건/일)",
}

results: list[tuple[str, bool, str]] = []


def line(name: str, ok: bool, detail: str = "", hint: str = "",
         optional: bool = False) -> None:
    """optional=True 는 없어도 서비스가 돌아가는 항목 — 실패로 세지 않는다."""
    if not ok and optional:
        print(f"{SKIP}{name}")
        if detail:
            print(f"     {detail}")
        return
    results.append((name, ok, detail))
    print(f"{OK if ok else NG} {name}")
    if detail:
        print(f"     {detail}")
    if not ok and hint:
        print(f"     → {hint}")


async def main() -> int:
    s = get_settings()
    today = weather.today_kst()

    print("=" * 64)
    print(f"  서버 시각(KST)  : {weather.now_kst():%Y-%m-%d %H:%M}")
    print(f"  날씨 소스        : {s.weather_source}")
    print(f"  LLM              : 요청={s.llm_backend}", end="")
    try:
        from app.llm.provider import effective_backend
        print(f" / 실제={effective_backend()}")
    except Exception:
        print()
    print("=" * 64)

    # 1. LLM
    try:
        from app.llm.provider import get_chat_model
        res = await get_chat_model("fast", temperature=0).ainvoke(
            [{"role": "user", "content": "'ok' 라고만 답하세요."}])
        text = (res.content if isinstance(res.content, str) else str(res.content)).strip()
        line("LLM 호출", bool(text), f"응답: {text[:60]!r}")
    except Exception as exc:
        line("LLM 호출", False, f"{type(exc).__name__}: {exc}"[:200],
             "NVIDIA_API_KEY 확인. 자체 NIM을 안 쓰면 NIM_*_BASE_URL 은 비워야 합니다.")

    # 2. 기상청 — 단기 / 초단기 / 실황
    vb, vt = weather.latest_base(weather.now_kst())
    ub, ut = weather.latest_ultra_base(weather.now_kst())
    nx, ny = weather.latlng_to_grid(SEOUL.lat, SEOUL.lng)
    print(f"\n[기상청] 격자=({nx},{ny})  단기base={vb} {vt}  초단기base={ub} {ut}")

    fc = await weather.hourly(SEOUL, today)
    ultra = [h for h, v in fc.items() if v.get("source") == "ultra_short"]
    sample = ""
    if fc:
        h = sorted(fc)[0]
        sample = f"{h}시 → {fc[h].get('condition')} / 강수확률 {fc[h].get('pop')}% / {fc[h].get('temp')}℃"
    line("기상청 단기예보", bool(fc),
         f"{len(fc)}개 시간대  {sample}" if fc else
         (last_error(f"kma.{s.weather_source}") or "빈 응답"), HINTS["kma"])
    if fc:
        line("기상청 초단기예보", bool(ultra),
             f"{len(ultra)}개 시간대 덮어씀 {ultra[:6]}" if ultra else
             (last_error(f"kma.{s.weather_source}.ultra") or "미적용"), HINTS["kma"])

    now = await weather.current(SEOUL)
    line("기상청 초단기실황", bool(now),
         str(now) if now else (last_error(f"kma.{s.weather_source}.ncst") or "빈 응답"),
         HINTS["kma"])

    # 3. 문화 API
    print()
    events = await culture_api.search_events(
        TripConditions(date=today, origin=SEOUL, region="서울"), limit=5)
    if s.culture_key and s.culture_api_endpoint:
        # 공공 API가 막히면 웹검색으로 넘어간다 — 어느 쪽이 답했는지 표시한다
        via_web = any(e.source == "web" for e in events)
        label = "문화 행사 탐색 (웹검색 폴백)" if via_web else "문화 행사 탐색 (공공 API)"
        line(label, bool(events),
             ", ".join(e.name for e in events[:3]) if events else
             (last_error("culture.period") or "빈 응답")[:220],
             HINTS["culture"])
        if via_web:
            print(f"     공공 API 응답: {(last_error('culture.period') or '')[:80]}")
            print("     공연전시정보조회서비스를 활용신청하면 공식 데이터로 바뀝니다")
    else:
        # 공공 API 미사용 — 웹검색이 대신한다. 이때는 웹검색이 필수 경로가 된다.
        line("문화 행사 탐색 (웹검색 대체)", bool(events),
             ", ".join(e.name for e in events[:3]) if events else
             "웹검색으로 행사를 찾지 못했습니다 — TAVILY_API_KEY 확인",
             "공공 API를 쓰려면 CULTURE_API_KEY 와 CULTURE_API_ENDPOINT 를 채우세요")

    # 4. NCP Maps
    print()
    if not (s.naver_client_id and s.naver_client_secret):
        line("NCP Geocoding", False, "키 미설정", HINTS["ncp"])
    else:
        geo = await maps.geocode("서울특별시 중구 세종대로 110")
        line("NCP Geocoding", geo is not None,
             f"{geo.lat:.5f}, {geo.lng:.5f}" if geo else
             (last_error("naver.geocode") or "빈 응답")[:220], HINTS["ncp"])

        route = await maps.route_duration(SEOUL, GeoPoint(lat=37.5796, lng=126.9770), "car")
        line("NCP Directions 5", not route["estimated"],
             f"{route['minutes']}분 / {route['distance_m']}m" if not route["estimated"]
             else (last_error("naver.directions") or "추정값으로 폴백됨")[:220], HINTS["ncp"])

    # 4-1. 도보 — OpenRouteService (NAVER에 도보 경로 API가 없다)
    print()
    if not s.ors_api_key:
        line("도보 경로 (OpenRouteService)", False,
             "키 미설정 — 도보 시간이 추정치(~)로 표시됩니다", HINTS["ors"], optional=True)
    else:
        walk = await maps.route_duration(SEOUL, GeoPoint(lat=37.5665, lng=126.9850), "walk")
        ok = walk.get("source") == "ors"
        line("도보 경로 (OpenRouteService)", ok,
             f"{walk['minutes']}분 / {walk['distance_m']}m" if ok
             else (last_error("ors.directions") or "추정값으로 폴백됨")[:220], HINTS["ors"])

    # 4-2. 대중교통 — ODsay (지하철만/버스만/통합을 파라미터로 구분한다)
    if not s.odsay_api_key:
        line("대중교통 경로 (ODsay)", False,
             "키 미설정 — 지하철·버스 시간이 추정치(~)로 표시됩니다",
             HINTS["odsay"], optional=True)
    else:
        for mode, label in (("transit", "지하철+버스"), ("subway", "지하철"), ("bus", "버스")):
            leg = await maps.route_duration(
                SEOUL, GeoPoint(lat=37.5045, lng=127.0495), mode)
            ok = leg.get("source") == "odsay"
            extra = f" · 환승 {leg['transfers']}" if leg.get("transfers") else ""
            fare = f" · {leg['fare']:,}원" if leg.get("fare") else ""
            line(f"대중교통 ({label})", ok,
                 f"{leg['minutes']}분 / {leg['distance_m']}m{extra}{fare}" if ok
                 else (last_error("odsay.path") or "추정값으로 폴백됨")[:220],
                 HINTS["odsay"])

    # 5. 지역검색
    if not s.naver_search_client_id:
        line("NAVER 지역검색", False, "키 미설정 (상시 문화공간·주변 추천이 비게 됨)",
             HINTS["local"])
    else:
        rows = await maps.search_nearby(SEOUL, "cafe", radius_m=1000, limit=3)
        line("NAVER 지역검색", bool(rows),
             ", ".join(r["name"] for r in rows[:3]) if rows else
             (last_error("naver.local") or "빈 응답")[:220], HINTS["local"])

    # 6. 웹검색
    hits = await websearch.search("대림미술관 운영시간", k=2)
    line("웹검색(Tavily)", bool(hits),
         hits[0].get("title", "")[:60] if hits else "빈 응답",
         "TAVILY_API_KEY 확인")

    await close_client()

    failed = [n for n, ok, _ in results if not ok]
    print("\n" + "=" * 64)
    if failed:
        print(f"  실패 {len(failed)}건: {', '.join(failed)}")
        print("  위 → 표시된 안내를 따라 처리한 뒤 다시 실행하세요.")
    else:
        print("  모든 API 정상 — 실제 데이터로 일정 생성이 가능합니다.")
    print("=" * 64)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
