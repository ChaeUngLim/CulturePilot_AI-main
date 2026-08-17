"""가상 문화공간 카탈로그 생성기.

손으로 2,000곳을 적을 수는 없다. 대신 **실제 자치구 중심좌표** 위에
카테고리별 분포를 따라 흩뿌린다. 좌표가 실제 행정구역 안에 떨어져야
지역 검색·반경 검색·이동시간 계산이 의미 있는 값을 내기 때문이다.

seed 고정이라 몇 번을 돌려도 같은 결과가 나온다 — 데모를 재현할 수 있어야 한다.

생성 규칙
  · 카테고리마다 실내/실외, 체류시간 분포가 다르다(공연장 120분 / 서점 50분)
  · 도심 자치구에 더 많이 배치한다(종로·중구가 은평·도봉보다 문화공간이 많다)
  · 이름은 지역·동네 어휘를 섞어 만든다
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

SEED = 20260810

# (지역, 중심위도, 중심경도, 반경도, 밀도가중치)
REGIONS = [
    # 서울 25개 자치구 — 도심일수록 가중치가 높다
    ("서울 종로구", 37.5735, 126.9790, 0.020, 3.0),
    ("서울 중구", 37.5636, 126.9975, 0.016, 2.6),
    ("서울 용산구", 37.5324, 126.9900, 0.018, 2.2),
    ("서울 성동구", 37.5634, 127.0369, 0.018, 2.0),
    ("서울 광진구", 37.5385, 127.0823, 0.017, 1.3),
    ("서울 동대문구", 37.5744, 127.0396, 0.017, 1.2),
    ("서울 중랑구", 37.6063, 127.0925, 0.018, 0.8),
    ("서울 성북구", 37.5894, 127.0167, 0.020, 1.3),
    ("서울 강북구", 37.6396, 127.0257, 0.018, 0.8),
    ("서울 도봉구", 37.6688, 127.0471, 0.018, 0.7),
    ("서울 노원구", 37.6542, 127.0568, 0.022, 1.0),
    ("서울 은평구", 37.6027, 126.9291, 0.020, 0.9),
    ("서울 서대문구", 37.5791, 126.9368, 0.017, 1.8),
    ("서울 마포구", 37.5637, 126.9084, 0.020, 2.4),
    ("서울 양천구", 37.5170, 126.8664, 0.017, 0.9),
    ("서울 강서구", 37.5509, 126.8495, 0.024, 1.1),
    ("서울 구로구", 37.4954, 126.8874, 0.019, 0.9),
    ("서울 금천구", 37.4569, 126.8955, 0.015, 0.7),
    ("서울 영등포구", 37.5264, 126.8963, 0.019, 1.6),
    ("서울 동작구", 37.5124, 126.9393, 0.017, 1.0),
    ("서울 관악구", 37.4784, 126.9516, 0.020, 1.0),
    ("서울 서초구", 37.4837, 127.0324, 0.022, 2.2),
    ("서울 강남구", 37.5172, 127.0473, 0.022, 2.8),
    ("서울 송파구", 37.5145, 127.1059, 0.022, 2.0),
    ("서울 강동구", 37.5301, 127.1238, 0.020, 1.1),
    # 광역시
    ("부산", 35.1600, 129.0700, 0.090, 3.2),
    ("대구", 35.8700, 128.6000, 0.070, 2.4),
    ("인천", 37.4560, 126.7050, 0.090, 2.4),
    ("대전", 36.3500, 127.3850, 0.060, 2.0),
    ("광주", 35.1600, 126.8500, 0.055, 1.6),
    ("울산", 35.5380, 129.3110, 0.055, 1.2),
    ("세종", 36.4800, 127.2890, 0.040, 0.8),
    ("제주", 33.4890, 126.5310, 0.120, 1.8),
]

# 카테고리별 주차 사정. 대형 시설은 유료 주차장이 있고,
# 골목의 작은 서점·공방은 대개 주차가 어렵다 — 이게 실제 불편의 대부분이다.
PARKING_BY_CATEGORY = {
    "미술관":       [("paid", 5), ("free", 2), ("nearby", 2), ("none", 1)],
    "갤러리":       [("nearby", 4), ("none", 4), ("paid", 2)],
    "박물관":       [("free", 5), ("paid", 3), ("nearby", 2)],
    "전시관":       [("paid", 4), ("nearby", 3), ("free", 2), ("none", 1)],
    "공연장":       [("paid", 6), ("free", 2), ("nearby", 2)],
    "독립영화관":   [("paid", 4), ("nearby", 4), ("none", 2)],
    "복합문화공간": [("paid", 5), ("free", 2), ("nearby", 3)],
    "도서관":       [("free", 6), ("paid", 2), ("nearby", 2)],
    "독립서점":     [("none", 6), ("nearby", 3), ("free", 1)],
    "공방":         [("none", 5), ("nearby", 4), ("free", 1)],
    "문화센터":     [("free", 6), ("paid", 2), ("nearby", 2)],
    "야외공연장":   [("free", 4), ("nearby", 3), ("paid", 3)],
    "문화유산":     [("free", 4), ("nearby", 3), ("none", 3)],
    "거리":         [("none", 5), ("nearby", 4), ("paid", 1)],
}
PARKING_NOTE = {
    "free":   ["무료 주차 가능", "부설 주차장 무료"],
    "paid":   ["유료 주차장 (10분 500원)", "건물 주차장 유료", "1시간 무료 후 유료"],
    "nearby": ["도보 3분 공영주차장", "인근 노상 주차 가능", "근처 민영주차장 이용"],
    "none":   ["전용 주차장 없음 · 대중교통 권장", "주차 불가 · 인근 주차난 심함"],
}

# (카테고리, kind, 실내, 체류분 범위, 상대 빈도)
CATEGORIES = [
    ("미술관",       "venue", True,  (60, 110), 1.4),
    ("갤러리",       "venue", True,  (35, 70),  2.0),
    ("박물관",       "venue", True,  (60, 130), 1.2),
    ("전시관",       "venue", True,  (45, 90),  1.1),
    ("공연장",       "venue", True,  (90, 140), 1.0),
    ("독립영화관",   "venue", True,  (95, 130), 0.7),
    ("복합문화공간", "venue", True,  (60, 120), 1.2),
    ("도서관",       "venue", True,  (45, 90),  0.9),
    ("독립서점",     "shop",  True,  (35, 70),  1.6),
    ("공방",         "shop",  True,  (60, 120), 1.5),
    ("문화센터",     "venue", True,  (50, 90),  0.8),
    ("야외공연장",   "park",  False, (40, 80),  0.5),
    ("문화유산",     "park",  False, (40, 90),  0.7),
    ("거리",         "park",  False, (40, 80),  0.5),
]

# 이름 조합용 어휘
PREFIX = [
    "고요", "달빛", "숲속", "골목", "언덕", "바람", "빛나", "온기", "하루", "쉼표",
    "여백", "한결", "그림", "물결", "노을", "새벽", "청담", "오후", "별빛", "가온",
    "다온", "누리", "미르", "아라", "이든", "하람", "소소", "담담", "정갈", "느린",
    "작은", "푸른", "맑은", "따뜻한", "조용한", "깊은", "너른", "고운", "환한", "잔잔한",
]
SUFFIX = {
    "미술관":       ["미술관", "아트뮤지엄", "현대미술관"],
    "갤러리":       ["갤러리", "아트스페이스", "전시실"],
    "박물관":       ["박물관", "역사관", "기념관"],
    "전시관":       ["전시관", "쇼룸", "아트홀"],
    "공연장":       ["아트홀", "콘서트홀", "소극장", "라이브홀"],
    "독립영화관":   ["시네마", "아트시네마", "독립극장"],
    "복합문화공간": ["문화공장", "컬처스페이스", "복합문화공간"],
    "도서관":       ["도서관", "북라운지", "열린도서관"],
    "독립서점":     ["책방", "북스", "서림", "서점"],
    "공방":         ["공방", "아틀리에", "스튜디오", "작업실"],
    "문화센터":     ["문화센터", "주민문화관", "생활문화센터"],
    "야외공연장":   ["야외무대", "잔디마당", "공원무대"],
    "문화유산":     ["고택", "옛터", "문화재길", "서원"],
    "거리":         ["문화거리", "예술거리", "골목길"],
}
THEME = ["도자", "유리", "가죽", "목공", "금속", "섬유", "사진", "회화", "판화", "향",
         "커피", "제본", "타이포", "민화", "한지", "자수", "인쇄", "디지털"]


def _parking(rng: random.Random, category: str) -> str:
    options = PARKING_BY_CATEGORY.get(category, [("unknown", 1)])
    return rng.choices([o[0] for o in options], weights=[o[1] for o in options])[0]


def generate(total: int = 2000) -> list[tuple]:
    rng = random.Random(SEED)

    region_w = [r[4] for r in REGIONS]
    cat_w = [c[4] for c in CATEGORIES]
    used: set[str] = set()
    out: list[tuple] = []
    seq = 0

    while len(out) < total:
        region, clat, clng, spread, _ = rng.choices(REGIONS, weights=region_w)[0]
        cat, kind, indoor, dwell_range, _ = rng.choices(CATEGORIES, weights=cat_w)[0]

        # 중심에 몰리도록 정규분포. 균등분포면 외곽에 과하게 퍼진다.
        lat = round(clat + rng.gauss(0, spread / 2.2), 6)
        lng = round(clng + rng.gauss(0, spread / 2.0), 6)

        # 이름이 겹치면 건너뛰는 대신 지역명을 덧붙여 해소한다.
        # 건너뛰기만 하면 조합 수가 많은 카테고리(공방)만 살아남아 분포가 무너진다.
        base = rng.choice(PREFIX)
        tail = rng.choice(SUFFIX[cat])
        name = (f"{base} {rng.choice(THEME)}{tail}" if cat == "공방"
                else f"{base} {tail}")
        if name in used:
            short = region.replace("서울 ", "")
            name = f"{short} {name}"
        if name in used:
            name = f"{name} {seq + 1}호점"
        if name in used:
            continue
        used.add(name)

        seq += 1
        parking = _parking(rng, cat)
        out.append((
            f"gen-{seq:04d}",                       # external_key
            name,
            kind,
            cat,
            f"{region} {rng.choice(PREFIX)}로 {rng.randint(1, 480)}",   # 주소
            region,
            lat,
            lng,
            indoor,
            rng.randint(*dwell_range),              # 체류분
            parking,
            rng.choice(PARKING_NOTE[parking]) if parking in PARKING_NOTE else None,
        ))
    return out


if __name__ == "__main__":
    from collections import Counter

    n = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
    rows = generate(n)
    by_region = Counter(r[5] for r in rows)
    by_cat = Counter(r[3] for r in rows)

    print(f"생성 {len(rows)}곳")
    print(f"\n지역 {len(by_region)}개 (상위 8)")
    for k, v in by_region.most_common(8):
        print(f"  {k:12s} {v:>4}곳")
    print(f"\n카테고리 {len(by_cat)}종")
    for k, v in by_cat.most_common():
        print(f"  {k:12s} {v:>4}곳")
    indoor = sum(1 for r in rows if r[8])
    print(f"\n실내 {indoor}곳 / 야외 {len(rows) - indoor}곳")
    print("주차:", dict(Counter(r[10] for r in rows)))
    print(f"이름 중복 없음: {len({r[1] for r in rows}) == len(rows)}")
