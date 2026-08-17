"""기상청 단기예보 (공공데이터포털 VilageFcstInfoService_2.0).

두 가지가 까다롭다.
  1) 위경도를 기상청 격자(nx, ny)로 바꿔야 한다 — Lambert Conformal Conic 투영.
  2) 발표 시각이 하루 8회(02·05·08·11·14·17·20·23시)로 고정이고, 발표 후
     약 10분 뒤부터 조회 가능하다. 그 전에 요청하면 빈 응답이 온다.

카테고리 매핑
  SKY 1맑음 3구름많음 4흐림 · PTY 0없음 1비 2비/눈 3눈 4소나기
  POP 강수확률(%) · TMP 기온(℃) · PM10은 별도 API(에어코리아)라 여기선 다루지 않는다.
"""
from __future__ import annotations

import logging
from datetime import date as Date
from datetime import datetime, timedelta, timezone
from math import cos, log, pow, sin, tan
from typing import Any

from app.config import get_settings
from app.schemas import GeoPoint
from app.tools.base import cache_key, cached
from app.tools.http import as_list, dig, get_json

logger = logging.getLogger(__name__)

# 같은 예보를 두 곳에서 받을 수 있다. 인증 파라미터 이름이 다르다는 게 유일한 차이.
ENDPOINT_DATA_GO_KR = (
    "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst"
)
ENDPOINT_API_HUB = (
    "https://apihub.kma.go.kr/api/typ02/openApi/VilageFcstInfoService_2.0/getVilageFcst"
)

# 초단기예보 — +6시간, 30분마다 발표. 단기예보보다 훨씬 최신이라
# '현장 재계획'(일정 조기 종료·공백 채우기)에서 이쪽이 정확하다.
ULTRA_DATA_GO_KR = (
    "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getUltraSrtFcst"
)
ULTRA_API_HUB = (
    "https://apihub.kma.go.kr/api/typ02/openApi/VilageFcstInfoService_2.0/getUltraSrtFcst"
)

# 초단기실황 — 지금 이 순간. "지금 비 오나"에 답한다.
NCST_DATA_GO_KR = (
    "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getUltraSrtNcst"
)
NCST_API_HUB = (
    "https://apihub.kma.go.kr/api/typ02/openApi/VilageFcstInfoService_2.0/getUltraSrtNcst"
)

# 초단기 발표는 매시 30분, 조회 가능은 45분경부터
ULTRA_PUBLISH_MIN = 45
BASE_TIMES = (2, 5, 8, 11, 14, 17, 20, 23)
PUBLISH_DELAY_MIN = 15

# 컨테이너는 UTC로 돌지만 기상청 발표 시각은 KST다.
# 이 변환을 빼먹으면 9시간 어긋난 base_time을 요청해 빈 응답을 받는다.
KST = timezone(timedelta(hours=9))


def now_kst() -> datetime:
    return datetime.now(KST)


def today_kst() -> Date:
    return now_kst().date()

BAD_CONDITIONS = {"rain", "snow", "shower", "storm", "heat", "cold", "dust"}

_SKY = {"1": "clear", "3": "cloudy", "4": "overcast"}
_PTY = {"1": "rain", "2": "rain", "3": "snow", "4": "shower"}


async def hourly(geo: GeoPoint | None, day: Date | None) -> dict[str, Any]:
    """{"14": {"condition": "rain", "pop": 70, "temp": 24}, ...}  키는 시(HH).

    단기예보(+3일)를 뼈대로 삼고, 오늘이면 초단기예보(+6시간)로 가까운 시간대를 덮어쓴다.
    같은 15시라도 3시간 전 발표보다 30분 전 발표가 맞을 확률이 높다.
    """
    if geo is None or day is None:
        return {}
    if not get_settings().weather_key:
        return {}
    key = cache_key("weather.hourly", {"g": geo.model_dump(), "d": str(day)})
    return await cached(key, ttl=900, fn=lambda: _hourly_merged(geo, day))


async def _hourly_merged(geo: GeoPoint, day: Date) -> dict[str, Any]:
    import asyncio

    is_today = day == today_kst()
    base, ultra = await asyncio.gather(
        _hourly(geo, day),
        _ultra_short(geo, day) if is_today else _noop(),
    )
    for hour, slot in (ultra or {}).items():
        merged = {**base.get(hour, {}), **slot}
        merged["source"] = "ultra_short"      # 어느 예보에서 온 값인지 남긴다
        base[hour] = merged
    return base


async def _noop() -> dict[str, Any]:
    return {}


async def _hourly(geo: GeoPoint, day: Date) -> dict[str, Any]:
    s = get_settings()
    nx, ny = latlng_to_grid(geo.lat, geo.lng)
    base_date, base_time = latest_base(now_kst())

    params: dict[str, Any] = {
        "pageNo": 1, "numOfRows": 1000, "dataType": "JSON",
        "base_date": base_date, "base_time": base_time, "nx": nx, "ny": ny,
    }
    if s.weather_source == "apihub":
        endpoint = ENDPOINT_API_HUB
        params["authKey"] = s.weather_key
    else:
        endpoint = ENDPOINT_DATA_GO_KR
        params["serviceKey"] = s.weather_key   # httpx가 URL 인코딩을 처리한다

    data = await get_json(endpoint, params=params, ttl=1800, retries=1,
                          name=f"kma.{s.weather_source}")

    items = as_list(dig(data, "response", "body", "items", "item", default=[]))
    if not items:
        from app.tools.http import record_error

        code = dig(data, "response", "header", "resultCode")
        msg = dig(data, "response", "header", "resultMsg")
        if code is not None and str(code) not in ("00", "0"):
            logger.warning("기상청 응답 코드 %s: %s", code, msg)
            record_error(f"kma.{s.weather_source}", f"resultCode={code} {msg}")
        elif data is not None:
            record_error(f"kma.{s.weather_source}",
                         f"응답은 왔으나 항목 없음 (base={base_date} {base_time}, nx={nx}, ny={ny})")
        return {}

    target = day.strftime("%Y%m%d")
    out: dict[str, dict[str, Any]] = {}
    for item in items:
        if str(item.get("fcstDate")) != target:
            continue
        hour = str(item.get("fcstTime", "0000"))[:2]
        slot = out.setdefault(hour, {})
        category, value = item.get("category"), str(item.get("fcstValue", ""))
        if category == "POP":
            slot["pop"] = _int(value)
        elif category == "TMP":
            slot["temp"] = _int(value)
        elif category == "SKY":
            slot["sky"] = _SKY.get(value, "clear")
        elif category == "PTY":
            slot["pty"] = _PTY.get(value)
        elif category == "REH":
            slot["humidity"] = _int(value)
        elif category == "WSD":
            slot["wind"] = value

    for slot in out.values():
        slot["condition"] = _condition(slot)
        slot.pop("sky", None)
        slot.pop("pty", None)
    return out


async def _ultra_short(geo: GeoPoint, day: Date) -> dict[str, Any]:
    """초단기예보(+6시간). 발표가 30분마다라 현장 상황에 가장 가깝다."""
    s = get_settings()
    nx, ny = latlng_to_grid(geo.lat, geo.lng)
    base_date, base_time = latest_ultra_base(now_kst())

    params: dict[str, Any] = {
        "pageNo": 1, "numOfRows": 300, "dataType": "JSON",
        "base_date": base_date, "base_time": base_time, "nx": nx, "ny": ny,
    }
    endpoint = ULTRA_API_HUB if s.weather_source == "apihub" else ULTRA_DATA_GO_KR
    params["authKey" if s.weather_source == "apihub" else "serviceKey"] = s.weather_key

    data = await get_json(endpoint, params=params, ttl=600, retries=1,
                          name=f"kma.{s.weather_source}.ultra")
    items = as_list(dig(data, "response", "body", "items", "item", default=[]))
    if not items:
        return {}

    target = day.strftime("%Y%m%d")
    out: dict[str, dict[str, Any]] = {}
    for item in items:
        if str(item.get("fcstDate")) != target:
            continue
        hour = str(item.get("fcstTime", "0000"))[:2]
        slot = out.setdefault(hour, {})
        category, value = item.get("category"), str(item.get("fcstValue", ""))
        if category == "T1H":            # 초단기는 기온 코드가 TMP가 아니라 T1H
            slot["temp"] = _int(value)
        elif category == "SKY":
            slot["sky"] = _SKY.get(value, "clear")
        elif category == "PTY":
            slot["pty"] = _PTY.get(value)
        elif category == "POP":
            slot["pop"] = _int(value)
        elif category == "RN1":
            slot["rain_mm"] = value
        elif category == "REH":
            slot["humidity"] = _int(value)

    for slot in out.values():
        slot["condition"] = _condition(slot)
        slot.pop("sky", None)
        slot.pop("pty", None)
    return out


async def current(geo: GeoPoint | None) -> dict[str, Any]:
    """초단기실황 — 지금 이 순간의 관측값. gap_fill(현장 재계획)에서 쓴다."""
    s = get_settings()
    if geo is None or not s.weather_key:
        return {}
    nx, ny = latlng_to_grid(geo.lat, geo.lng)
    now = now_kst()
    # 실황은 매시 정시 발표, 40분경부터 조회 가능
    stamp = now - timedelta(hours=0 if now.minute >= 40 else 1)

    params: dict[str, Any] = {
        "pageNo": 1, "numOfRows": 60, "dataType": "JSON",
        "base_date": stamp.strftime("%Y%m%d"), "base_time": stamp.strftime("%H00"),
        "nx": nx, "ny": ny,
    }
    endpoint = NCST_API_HUB if s.weather_source == "apihub" else NCST_DATA_GO_KR
    params["authKey" if s.weather_source == "apihub" else "serviceKey"] = s.weather_key

    data = await get_json(endpoint, params=params, ttl=300, retries=1,
                          name=f"kma.{s.weather_source}.ncst")
    items = as_list(dig(data, "response", "body", "items", "item", default=[]))
    slot: dict[str, Any] = {}
    for item in items:
        category, value = item.get("category"), str(item.get("obsrValue", ""))
        if category == "T1H":
            slot["temp"] = _int(value)
        elif category == "PTY":
            slot["pty"] = _PTY.get(value)
        elif category == "RN1":
            slot["rain_mm"] = value
        elif category == "REH":
            slot["humidity"] = _int(value)
    if not slot:
        return {}
    slot["condition"] = _condition(slot)
    slot.pop("pty", None)
    slot["observed_at"] = stamp.strftime("%Y-%m-%d %H:00 KST")
    return slot


def latest_ultra_base(now: datetime) -> tuple[str, str]:
    """초단기예보 base. 매시 30분 발표, 45분경부터 조회 가능."""
    cursor = now if now.minute >= ULTRA_PUBLISH_MIN else now - timedelta(hours=1)
    return cursor.strftime("%Y%m%d"), cursor.strftime("%H30")


def _condition(slot: dict[str, Any]) -> str:
    """강수 형태가 있으면 그것이 우선, 없으면 기온으로 폭염·한파를 판정한다."""
    if slot.get("pty"):
        return slot["pty"]
    temp = slot.get("temp")
    if temp is not None:
        if temp >= 33:
            return "heat"
        if temp <= -10:
            return "cold"
    return slot.get("sky") or "clear"


def risky_hours(forecast: dict[str, Any]) -> list[str]:
    """야외 활동이 어려운 시간대. 일정 배치와 weather_risk 검증의 입력."""
    return [
        hour for hour, v in forecast.items()
        if v.get("condition") in BAD_CONDITIONS or (v.get("pop") or 0) >= 60
    ]


def latest_base(now: datetime) -> tuple[str, str]:
    """가장 최근에 발표된 base_date/base_time. 발표 직후 15분은 이전 회차를 쓴다."""
    cursor = now - timedelta(minutes=PUBLISH_DELAY_MIN)
    for hour in reversed(BASE_TIMES):
        if cursor.hour >= hour:
            return cursor.strftime("%Y%m%d"), f"{hour:02d}00"
    prev = cursor - timedelta(days=1)
    return prev.strftime("%Y%m%d"), "2300"


# ------------------------------------------------- 위경도 → 기상청 격자(LCC)
_RE, _GRID = 6371.00877, 5.0
_SLAT1, _SLAT2 = 30.0, 60.0
_OLON, _OLAT = 126.0, 38.0
_XO, _YO = 43, 136


def latlng_to_grid(lat: float, lng: float) -> tuple[int, int]:
    """기상청 단기예보 격자 변환 (Lambert Conformal Conic)."""
    from math import pi

    degrad = pi / 180.0
    re = _RE / _GRID
    slat1, slat2 = _SLAT1 * degrad, _SLAT2 * degrad
    olon, olat = _OLON * degrad, _OLAT * degrad

    sn = tan(pi * 0.25 + slat2 * 0.5) / tan(pi * 0.25 + slat1 * 0.5)
    sn = log(cos(slat1) / cos(slat2)) / log(sn)
    sf = tan(pi * 0.25 + slat1 * 0.5)
    sf = pow(sf, sn) * cos(slat1) / sn
    ro = tan(pi * 0.25 + olat * 0.5)
    ro = re * sf / pow(ro, sn)

    ra = tan(pi * 0.25 + lat * degrad * 0.5)
    ra = re * sf / pow(ra, sn)
    theta = lng * degrad - olon
    if theta > pi:
        theta -= 2.0 * pi
    if theta < -pi:
        theta += 2.0 * pi
    theta *= sn

    return int(ra * sin(theta) + _XO + 0.5), int(ro - ra * cos(theta) + _YO + 0.5)


def _int(value: Any) -> int | None:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None
