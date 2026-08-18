"""출발·도착 절을 문장에서 떼어낸다.

떼어내는 이유는 두 가지다.
  · '09시에 출발'의 09시는 방문할 장소가 아니라 **일정의 시작 시각**이다.
  · '종로역에서 도착'의 종로역은 추천할 장소가 아니라 **하루의 끝점**이다.
구분하지 않으면 출발지가 첫 번째 방문지로 일정에 들어간다.

이 모듈이 큰 이유는 한국어 어순이 둘 다 자연스럽기 때문이다 — '판교역에서 출발'
(후치)과 '출발 판교역'(라벨). 앞만 보면 뒤에 온 장소를 놓치고, 심지어 도착 절의
시각을 출발로 읽는다. 주석에 달린 «실제로 겪은 버그» 들이 전부 그 경계에서 나왔다.
"""
from __future__ import annotations

import re

from app.graph.router.detect import _NOT_LANDMARK
from app.graph.router.timeparse import _TIME, _time_in

# "양재역 낮 09시에서 출발", "밤 20시에 종로역에서 도착" 처럼 장소와 시각이
# 한 덩어리로 붙어 온다. 이 덩어리를 통째로 떼어내지 않으면 '09시'가 일정 항목으로
# 잡혀서, 출발 시각이 방문할 장소 하나로 둔갑한다.
_START_WORD = re.compile(
    r"(?:출발|시작|나가|나서|나올|집에서 나)(?:해서|하여|하고|해|한|하는|합니다)?")
# '귀가'가 빠져 있어서 '밤 10시까지 귀가'의 10시가 **방문할 장소의 지정 시각**으로
# 잡혔고, 그 하나만 22:00에 고정된 채 나머지 일정이 통째로 밀려났다.
# 하루의 끝을 뜻하는 말은 넉넉히 열어 둔다 — 빠뜨리면 일정이 무너진다.
# 어간이 활용되므로 종성까지 열어 둔다 — '돌아가'만 적으면 '돌아갈'을 놓친다.
# 다만 '-고'가 붙은 연결어미는 제외한다. '끝나고 다시 수원역 밤 8시 도착'의
# '끝나고'는 하루의 끝이 아니라 '그 다음에'라는 뜻이라, 여기서 절을 끊으면
# 진짜 도착 절을 통째로 놓친다.
_END_WORD = re.compile(
    r"(?:도착|종료|마무리|귀가|복귀|퇴근"
    r"|끝나(?!고)|끝내(?!고)|돌아[가갈감와올](?!고)|들어[가갈감와올](?!고))"
    r"(?:해서|하여|하고|해|는|하는|할|합니다|겠|거야|꺼야)?")
# 절 안에서 장소 후보를 찾을 때 걸러낼 말들.
# 이동수단이 빠져 있어서 '지하철로 출발'의 출발지가 '지하철'로 잡혔다.
_NOT_PLACE = {
    "오전", "오후", "아침", "점심", "저녁", "밤", "낮", "새벽", "정오",
    "오늘", "내일", "모레", "이따", "지금", "부터", "에서", "해서", "그리고",
    "지하철", "버스", "전철", "자가용", "자차", "차량", "도보", "대중교통",
    "택시", "자전거", "따릉이", "마을버스", "광역버스",
}

# 라벨처럼 앞에 오는 어순. "출발 아침 9시 판교역 / 도착 회기역 밤 9시"
# 한국어는 '판교역에서 출발'도 '출발 판교역'도 자연스럽다. 앞만 보면
# 뒤에 온 장소를 놓치고, 심지어 도착 절의 시각을 출발로 읽는다.
# 라벨 뒤에 조사가 붙는 게 더 자연스럽다 — '출발은 부산역', '도착은 서울역'.
# 조사를 허용하지 않으면 '출발은'이 라벨로 안 잡혀서, 앞쪽을 뒤지다가
# '만들어줘' 같은 엉뚱한 말을 출발지로 집는다.
_LABEL_START = re.compile(r"(?:^|[\s,])(?:출발지?|시작)(?:은|는|이|가|point)?\s*[:：]?\s")
_LABEL_END = re.compile(r"(?:^|[\s,])(?:도착지?|종점|목적지)(?:은|는|이|가)?\s*[:：]?\s")


def _clause_after(query: str, match: re.Match, width: int = 28) -> tuple[int, str]:
    """키워드 뒤의 창. 라벨 어순('출발 판교역')에서 장소·시각을 뽑는다."""
    start = match.end()
    chunk = query[start:start + width]
    # 다음 라벨이 나오면 거기서 끊는다 — 도착 절을 출발 절이 삼키면 안 된다
    for nxt in (_LABEL_START, _LABEL_END, _START_WORD, _END_WORD):
        m = nxt.search(chunk)
        if m and m.start() > 0:
            chunk = chunk[:m.start()]
    # 구두점에서도 끊는다. '도착: 홍대입구역 20시, 강남 전시 3곳'의 뒷부분은
    # 도착지가 아니라 다음 조건이다.
    for sep in (",", "·", ".", "에서", "으로"):
        idx = chunk.find(sep)
        if idx > 1:
            chunk = chunk[:idx]
    return start, chunk


def _clause_before(query: str, match: re.Match, width: int = 32) -> tuple[int, str]:
    """키워드 앞의 짧은 창(window). 여기서 시각과 장소를 뽑는다.

    폭이 좁으면 '서울 강남구 영동대로 513 에서 10시에 출발'의 '서울'이 잘려
    주소가 반쪽이 된다. 문장 구분자에서 끊으므로 넉넉히 잡아도 안전하다.
    """
    start = max(0, match.start() - width)
    # 문장 구분자가 있으면 거기서부터 — 앞 문장을 끌어오지 않는다
    chunk = query[start:match.start()]
    for sep in (",", ".", "그리고", "하고", "주고", "보고", "돌고", "들러"):
        idx = chunk.rfind(sep)
        if idx >= 0:
            chunk = chunk[idx + len(sep):]
            start += idx + len(sep)
    return start, chunk


# 조사는 뒤에서만 떼어낸다. str.strip(문자집합)을 쓰면 '서울역'의 앞 '서'까지
# 깎여서 '울역'이 된다 — 실제로 겪은 버그다.
# 홑 '로'는 떼지 않는다. 도로명이 '영동대로'·'세종대로'로 끝나기 때문에
# 떼어내면 '영동대'가 되어 주소를 못 찾는다. '으로'만 조사로 본다.
_PARTICLE = re.compile(r"(?:에서|부터|까지|으로|에|의|를|을|은|는|이|가|와|과)$")

# 주소처럼 보이는가. 도로명·지번은 여러 낱말로 이뤄져서 마지막 낱말만 뽑으면
# '513' 같은 번지만 남는다. 이런 건 절 전체를 그대로 주소 API에 넘겨야 한다.
# 번지 앞에는 공백이 있어야 한다. \s* 로 두면 '종로3가역'의 '종로3'이
# 도로명+번지로 잡혀서 역 이름이 주소로 둔갑한다.
# 홑 '시'는 넣지 않는다. '다시'·'역시'·'잠시'가 시(市)로 잡혀서
# '다시 수원역 밤 8시' 전체가 주소로 둔갑한다 — 실제로 겪은 버그다.
_ADDRESSY = re.compile(
    r"(?:[가-힣]{2,}(?:특별시|광역시)\s|[가-힣]{2,}(?:구|군)\s|"
    r"[가-힣]+(?:대로|로|길)\s+\d|[가-힣]+동\s+\d)")


# 걸러낼 말인지 볼 때만 쓰는 넓은 조사 집합. 장소 이름을 만들 때는 쓰지 않는다
# ('영동대로'의 로까지 떼면 주소가 깨진다).
_PARTICLE_LOOSE = re.compile(r"(?:에서|부터|까지|으로|로|에|의|를|을|은|는|이|가|와|과)$")


def _is_noise(word: str) -> bool:
    """이동수단·시간 표현처럼 장소가 될 수 없는 말인지."""
    return _PARTICLE_LOOSE.sub("", word) in _NOT_PLACE or word in _NOT_PLACE


def _place_in(chunk: str, *, first: bool = False) -> tuple[str, int] | None:
    """절에서 장소 이름과 그 위치를 고른다. 시각·조사는 버린다.

    주소는 여러 낱말이라 마지막 낱말만 뽑으면 '513' 같은 번지만 남는다.
    주소처럼 보이면 절 전체를 넘긴다 — 주소 API가 알아서 해석한다.

    first=True 는 라벨 어순('출발 판교역 …')에서 쓴다. 이 어순은 장소가
    키워드 바로 뒤에 오므로, 마지막 낱말을 고르면 뒤에 붙은 다른 말을 집는다.
    """
    cleaned = _TIME.sub(lambda m: " " * len(m.group(0)), chunk)   # 위치 보존

    if _ADDRESSY.search(cleaned):
        words = [w for w in cleaned.split() if w and not _is_noise(w)]
        if len(words) >= 2 and not first:
            phrase = _PARTICLE.sub("", " ".join(words)).strip()
            if len(phrase) >= 4:
                return (phrase, cleaned.index(words[0]))

    best: tuple[str, int] | None = None
    for m in re.finditer(r"[가-힣A-Za-z0-9]+", cleaned):
        raw = m.group(0)
        word = _PARTICLE.sub("", raw)
        if len(word) >= 2 and not _is_noise(raw) and not word.isdigit():
            best = (word, m.start())
            if first:
                break
    return best


def _span_end(chunk: str, place, when) -> int:
    """이 절에서 실제로 읽어낸 부분의 끝. 잘라낼 범위를 정확히 정한다.

    어림수로 자르면 '출발 … 판교역와 도착 회기역'에서 '도착 회'까지 먹어
    다음 절의 장소가 통째로 사라진다 — 실제로 겪은 버그다.
    """
    ends = []
    if place:
        ends.append(place[1] + len(place[0]))
    if when:
        m = _TIME.search(chunk, when[1])
        ends.append(m.end() if m else when[1] + 3)
    return max(ends) if ends else 0


# 후치 어순('판교역에서 출발')의 표식. 키워드 바로 앞에 붙는 처소격 조사다.
_TRAILING_MARK = re.compile(r"(?:에서|부터|에)\s*$")


def _looks_trailing(before: str) -> bool:
    """키워드 앞의 이 부분이 '출발지 절'인가, 아니면 그냥 앞 문장인가.

    아무 낱말이나 있으면 후치로 보면 안 된다. '일정 만들어줘 출발은 부산역'의
    '만들어줘'를 출발지로 집게 된다 — 실제로 그렇게 깨졌다.

    후치 어순은 둘 중 하나로 드러난다.
      · 시각이 붙어 있다     '판교역에서 7시 출발'
      · 처소격 조사로 끝난다  '판교역에서 출발'
    """
    return bool(_TRAILING_MARK.search(before.rstrip())) or _time_in(before) is not None


def _split_endpoints(query: str) -> tuple[dict, str]:
    """출발·도착 절을 떼어내고 나머지를 돌려준다.

    떼어내는 이유는 두 가지다.
      · '09시에 출발'의 09시는 방문할 장소가 아니라 일정의 시작 시각이다.
      · '종로역에서 도착'의 종로역은 추천할 장소가 아니라 하루의 끝점이다.
    이걸 구분하지 않으면 출발지가 첫 번째 방문지로 일정에 들어간다.
    """
    found: dict = {}
    cuts: list[tuple[int, int]] = []
    # 이미 쓴 구간은 다음 절이 다시 보지 않는다. 그러지 않으면 출발 시각이
    # 도착 시각으로도 잡혀서 '9시에 나가서 … 8시에 퇴근'의 퇴근이 9시가 된다.
    taken = [False] * len(query)

    for word, label, keys in (
            (_START_WORD, _LABEL_START, ("origin_name", "start_time")),
            (_END_WORD, _LABEL_END, ("destination_name", "end_time"))):
        # 라벨 어순('출발 판교역')을 먼저 본다. 이게 있으면 뒤쪽을 읽는다.
        m = label.search(query)
        label_order = bool(m)
        if m:
            # 라벨처럼 보여도 **앞에 아직 안 쓴 내용이 있으면 후치 어순**이다.
            # '판교역에서 7시 출발 청계산역 …'의 '출발 '을 라벨로 읽으면
            # 뒤의 청계산역을 출발지로, 21시를 출발 시각으로 집는다.
            # 실제로 그렇게 잡혀서 19시 출발/21시 종료가 되었고 일정이 비었다.
            #
            # 앞 절이 이미 가져간 부분은 가리고 본다. '출발 판교역 07시 도착 …'에서
            # '도착' 앞의 '판교역 07시'는 출발 절이 쓴 것이라, 그걸 내용으로 세면
            # 진짜 라벨 어순까지 후치로 오인해 도착지를 놓친다.
            b_start, before = _clause_before(query, m)
            before = "".join(" " if taken[b_start + i] else ch
                             for i, ch in enumerate(before))
            if _looks_trailing(before):
                label_order = False
        if label_order and m:
            start, chunk = _clause_after(query, m)
        else:
            # 하루의 끝은 문장 끝에 온다. 첫 매치를 쓰면 문장 중간의
            # '끝나고'·'도착해서' 같은 말에 걸려 진짜 도착 절을 놓친다.
            hits = list(word.finditer(query))
            if not hits:
                continue
            m = hits[-1] if word is _END_WORD else hits[0]
            start, chunk = _clause_before(query, m)
        # 앞 절이 이미 가져간 부분은 공백으로 가린다
        chunk = "".join(" " if taken[start + i] else ch
                        for i, ch in enumerate(chunk))
        # 출발 절의 표시 없는 1~7시는 오전으로 읽는다. 도착 절은 오후가 자연스럽다.
        place = _place_in(chunk, first=label_order)
        when = _time_in(chunk, pm_bias=word is _END_WORD)
        offsets = []
        if place and place[0] not in _NOT_LANDMARK:
            found[keys[0]] = place[0]
            offsets.append(place[1])
        if when:
            found[keys[1]] = when[0]
            offsets.append(when[1])
        # 잘라내는 범위는 실제로 쓴 부분까지만. 창 전체를 지우면
        # '3곳 정도' 같은 다른 조건까지 같이 사라진다.
        if offsets:
            # 라벨 어순이면 키워드부터 값 끝까지, 아니면 값 시작부터 키워드까지
            a, b = ((m.start(), start + _span_end(chunk, place, when))
                    if start >= m.end()
                    else (start + min(offsets), m.end()))
            cuts.append((a, min(b, len(query))))
            for i in range(a, min(b, len(taken))):
                taken[i] = True

    rest = query
    for a, b in sorted(cuts, reverse=True):
        rest = rest[:a] + " " + rest[b:]
    return found, rest
