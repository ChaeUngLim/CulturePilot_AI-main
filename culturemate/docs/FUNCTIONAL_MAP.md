# CultureMate 기능·소스 매핑서

> **기능(UR) → 코드 → 데이터 → 화면**을 한 장에서 추적하기 위한 문서.
> 설계 의도는 `ARCHITECTURE.md`, 실행 흐름은 `STRUCTURE.md`, 요구사항 정의는
> `REQUIREMENTS.md`. 이 문서는 그 셋을 **기능 축으로 다시 자른 것**이다.
> 기획안 원문과 그 대조는 [PLANNING.md](PLANNING.md), UR 전량 목록은
> [REQUIREMENTS.md §3.5](REQUIREMENTS.md).

- 검증 기준: `langgraph 1.2.10` · `langchain-core 1.5.3` · `fastapi 0.141.1` ·
  `pydantic 2.13.4` · `PostgreSQL 18.4 + pgvector 0.8.5`
- 이 문서의 모든 파일·함수·테이블은 **작성 시점에 소스에서 확인한 것**이다.
  존재하지 않는 것은 ❌ 로 표시했다 — 문서가 코드보다 앞서가면 그때부터 문서는 근거가 아니다.
- 범위 제외: **UR-11 · UR-12 · UR-15 · UR-16** (기능 제거됨. 이력은 §7 참고)
- 미구현·부분 항목은 §1 표에 상태와 함께 남긴다. 목록에서 지우면
  «없다»가 아니라 «원래 없었다»로 읽혀, 기획안과의 차이를 추적할 수 없게 된다.

---

## 0. 한 장 요약

```
[모바일 Expo]  Composer 입력
      │  POST /chat (SSE)
      ▼
[FastAPI]  app/api/main.py            엔드포인트 23개
      │  graph.astream(["updates","messages"])
      ▼
[LangGraph]  app/graph/build.py       노드 11개 · 서브그래프 4개
      │
      ├─ classify        router/                UR-02
      ├─ ⟨병렬⟩ archive / discovery / current_plan   UR-03 · UR-04
      ├─ merge_context   nodes.py
      ├─ itinerary       subgraphs/itinerary/   UR-05 · UR-06 · UR-07 · UR-08
      ├─ validation      subgraphs/validation.py
      ├─ hitl            nodes.py               UR-13
      ├─ finalize → persist                     UR-09 · UR-10
      └─ compose         nodes.py               UR-14
      ▼
[PostgreSQL 18 + pgvector]  테이블 13개 + 체크포인트 3개
```

**규모**: 백엔드 Python **10,496줄**(54파일) / 모바일 TS **6,542줄**(`src` 4,042 + `app` 2,500) / 테스트 **194개**.

---

## 1. 기능 목록과 구현 상태

| UR | 기능명 | 상태 | 진입점 |
|:--:|---|:--:|---|
| **UR-01** | 개인 취향 등록(카드) | ✅ | `POST /preferences/cards` → `profile.apply_preference_cards` |
| **UR-02** | 문화생활 조건 입력 | ✅ | `router.classify` |
| **UR-03** | 문화 콘텐츠 통합 탐색 | ✅ | `discovery` 서브그래프 |
| **UR-04** | 신뢰 가능한 정보 확인 | ✅ | `tools/verify.py` |
| **UR-05** | 맞춤 일정 자동 생성 | ✅ | `itinerary.schedule` |
| **UR-06** | 지도 기반 동선 확인 | ✅ | `tools/maps.py` · `tools/routing.py` |
| **UR-07** | 날씨 기반 일정 추천 | ✅ | `itinerary.ctx_weather` |
| **UR-08** | 주변 장소 추천 | ✅ | `detect_gaps` → `nearby_search` |
| **UR-09** | 일정 직접 수정 | ✅ | `plan_modify` 라우트 + `nodes.persist` → `repo.save_plan_edits` → `plan_edits` |
| **UR-10** | 개인 아카이브 관리 | ✅ | `POST /visits` |
| **UR-13** | 대안 확인 및 선택 | ✅ | `nodes.human_review` |
| **UR-14** | AI 판단 근거 확인 | ✅ | `Evidence` 누적 |
| **UR-15** | 취향 리포트 | ✅ | `GET /report/{user_id}` (대화 경로만 제거 — §7) |
| **UR-17** | 개인정보 통제 | ✅ | `ON DELETE CASCADE` |

**기획안 대조에서 새로 들어온 항목** (근거: [PLANNING.md §7](PLANNING.md))

| UR | 기능명 | 상태 | 진입점 |
|:--:|---|:--:|---|
| **UR-28** | **캘린더로 일정 확인** ★핵심 | ✅ | `repo.list_plans` → `GET /plans/{user_id}` → `(tabs)/calendar.tsx` |
| **UR-40** | 과거 불편의 선제 경고 | ✅ | `validation.check_friction` → `past_friction` 이슈 → 확인 카드 |
| **UR-29** | 남은 기간 배지 · «오늘 열려요» | ⬜ | 탐색 목록 화면 없음 |
| **UR-30** | 저장 목록에서 일정 만들기 | ⬜ | `user_collections` → 일정 생성 경로 없음 |
| **UR-31** | 3지 반응 | ✅ | `app/taste-cards.tsx` — 기대돼요/가봤어요/관심 없어요 |
| **UR-32** | 구조화 기록 입력 | ⚠️ **부분** | `POST /visits` · `app/visit.tsx` — 자동 유도·태그·사진 얇음 |
| **UR-33** | 근거 3색 규약 · 확인 시각 | ⚠️ **부분** | `Evidence` 는 있고 화면 계약이 없다 |
| **UR-34** | 적용된 규칙 칩 · 끄기 | ⬜ | 규칙값이 내부에만 있다 |
| **UR-35** | 파생 제약 자동 산출 | ⚠️ **부분** | `avg_dwell_min` 만 개인화됨 |
| **UR-36** | 현장 진행 표시 · 조기 종료 감지 | ⚠️ **부분** | `gap_fill` 라우트는 있고 트리거가 없다 |
| **UR-37** | 이동 요약 · 정렬 선택 · 편의시설 | ⬜ | 구간 데이터는 이미 있다 |
| **UR-38** | 뱃지 · 맞춤 미션 | ⬜ | — |
| **UR-39** | 예약·티켓 연결 | ⬜ | Phase 3 |

> UR-01 은 오래 **문서가 구현을 앞서 있던 항목**이었다. UR-09·UR-28 과 함께
> 2026-08-17 에 닫혔고, UR-01 은 2026-08-17 후속 작업에서 닫혔다.
> 위 표의 ⬜·부분 항목도 같은 원칙으로 다룬다 — **코드에서 확인되지 않으면 ✅를 쓰지 않는다.**

---

## 2. 기능별 상세

### UR-01 — 개인 취향 등록(카드)

| | |
|---|---|
| **의도** | 아카이브가 빈 신규 사용자의 초기 취향을 카드 평가로 부트스트랩 |
| **데이터** | `preference_cards` 테이블 (`db/001_schema.sql`) |
| **코드** | `repo.save_preference_cards` · `profile.apply_preference_cards` |
| **API** | `POST /preferences/cards` · `GET /preferences/cards/{user_id}` |
| **화면** | `mobile/app/taste-cards.tsx` (취향 탭에서 진입하는 모달) |

**어디에 접히나 — 새 필드를 만들지 않았다.** 카드는 기존 두 필드로 접힌다.

| 카드 | 접히는 곳 |
|---|---|
| 카테고리 + 긍정 | `preferred_categories[카테고리]` **+** |
| 카테고리 + 부정 | `preferred_categories[카테고리]` **−** (음수) |
| 장소 + 긍정 | 그 장소의 `places.category` 로 **+** |
| 장소 + 부정 | 위와 같이 **−**, 그리고 `frequent_removals[place_id]` |

그래서 `personal_score()` 는 **한 줄도 고치지 않고** 카드를 반영한다. 점수 계산이
«어디서 온 취향인가»를 알 필요가 없다는 뜻이다.

대신 **음수가 처음으로 `preferred_categories` 에 들어온다.** 이 값을 내림차순으로
정렬해 상위 몇 개를 «선호»로 읽던 자리 네 곳(`router._apply_taste`·`nodes._taste_summary`
· 모바일 `report.tsx`·`index.tsx`)에 `> 0` 필터를 함께 넣었다 — 안 걸면 **싫다고 표시한
카테고리가 검색어가 된다.**

**재집계가 카드를 되읽는다** (`profile.rebuild_profile` → `_CARDS_SQL`). 재집계는 전량
재계산이라, 이걸 빼면 방문 기록 **한 건**이 들어온 순간 등록해 둔 취향이 전부 지워진다 —
UR-09 에서 똑같이 밟은 함정이다.

**카드가 아카이브를 이기지 않는다.** 카테고리당 카드 기여분은 `_CARD_CAP`(0.30)으로
자른다. 없으면 등록만 한 취향이 실제로 다녀온 곳보다 세지고, «기록이 쌓일수록 추천이
좋아진다»는 전제가 뒤집힌다.

---

### UR-02 — 문화생활 조건 입력

| | |
|---|---|
| **입력** | 자연어 발화 + 화면 칩(출발/도착/시각/이동수단/지역) |
| **출력** | `TripConditions` (좌표 확정 포함) · `RequestType` · `PlanFlags` |
| **코드** | `app/graph/router/` (6모듈, 최대 295줄) — `__init__.classify` · `rules.py` |
| **화면** | `Composer.tsx` · `RoutePoints.tsx` · `TransportPicker.tsx` · `RegionPicker.tsx` |

**처리 순서** (`classify` 내부, 순서가 곧 우선순위)

```
① 규칙 추출     _rule_conditions()   정규식 — 지역·날짜·시각·수단·개수·체류
② LLM 보강      structured("router") 규칙이 부족할 때만 (아래 규칙 참고)
③ 규칙 보정     _merge_rules()       LLM이 놓친 빈칸을 규칙이 채움
④ 화면값 덮기   _apply_override()    발화가 화면 잔재보다 세다
⑤ 취향 반영     _apply_taste()
⑥ 좌표 확정     _resolve_places()    출발·도착·지역 → GeoPoint
```

**규칙 — 규칙이 충분하면 LLM을 건너뛴다** (`_rules_suffice`).
요청 유형·장소·시각이 **모두** 규칙으로 잡혔을 때만 건너뛴다. 하나라도 비면 LLM에
맡긴다. 아끼는 건 3.6초지만 잘못 건너뛰면 일정 전체가 틀린다.

**규칙 — 발화가 화면을 이긴다.** `_apply_override()` 가 없으면 이전 화면에 남은
출발지가 방금 말한 출발지를 덮는다.

**규칙 — 좌표 확정은 라우터에서.** 서브그래프가 `conditions` 를 제자리에서 고쳐도
그 변경은 부모로 돌아오지 않는다(출력 스키마에 없으면). 위치 해석은 '요청을 이해하는
일'이지 '장소를 찾는 일'이 아니다.

---

### UR-03 — 문화 콘텐츠 통합 탐색

| | |
|---|---|
| **코드** | `app/graph/subgraphs/discovery.py` (534줄) |
| **외부** | `tools/culture_api.py` (500줄) · `tools/region.py` (134줄, UR-18) · `tools/websearch.py` · `tools/local_catalog.py` |
| **데이터** | `places` (2,092행) · `api_cache` |

```
search_catalog    ┐  내장 카탈로그 — 외부 API 없이 즉시 응답
search_events     ├→ normalize → ⟨Send⟩ verify ×N → classify → END
search_always_on  │  공공 문화시설 API
search_web        ┘  Tavily → Exa 폴백
```

| 소스 | 함수 | 제공자 | 없으면 |
|---|---|---|---|
| 카탈로그 | `local_catalog.search` | 내부 DB | 외부 소스가 대체 |
| 기간형 행사 | `culture_api.search_events` | KCISA `CNV_060` | 웹검색 |
| 상시 문화공간 | `culture_api.search_always_on` | 공공데이터포털 + 네이버 지역검색 | 카탈로그 |
| 웹 | `websearch.search` | Tavily → Exa | 근거만 손실 |

**관문 4개 (`normalize`)** — 이 순서로 후보를 거른다.

1. **좌표 없으면 후보 아님.** 이름이 아니라 좌표가 자격이다. 없으면 지도에도
   안 찍히고 이동시간도 못 재는데 사용자에겐 갈 수 있는 곳처럼 보인다.
2. **시·도 판정** (`tools/region.py`, UR-18). 요청한 시·도가 **아니라고 확인된**
   후보만 버린다. 「서초구」 요청에 판교(경기 성남시)는 25km라 아래 3번을 통과한다 —
   거리로는 못 잡는 자리다. 모르는 후보는 통과시키고 3번이 받는다.
3. **거리 상한 60km** (`MAX_ANCHOR_KM`). 출발지·도착지 양쪽을 기준점으로 삼고,
   둘 다 없으면 후보 좌표의 **중앙값**을 기준으로 쓴다. 중앙값은 이상치에 안 끌린다.
   2번이 붙은 뒤로 이 관문의 역할은 **행정구역을 모르는 후보를 받는 것**이다.
4. **기간·제외어 필터.**

통과한 뒤 말한 구(區)에 실제로 있는 후보에 `REGION_BONUS`(0.15)를 얹는다. 자르지
않고 가점만 주는 이유는 구 경계가 생활권과 다르기 때문이다 — 서초구 요청에 200m
건너 강남구를 없는 곳으로 취급하면 그것대로 틀린 결과가 된다.

**지역 필터는 소스에서도 한 번 더 건다** (`culture_api._in_region`).
공공 API는 `sido` 를 넘겨도 전국 결과를 섞어 준다 — 강남 요청에 대구·청주가 따라온다.
`region.py` 와 역할이 다르다. 그쪽은 **응답 본문의 지역명 단서**로 명백한 타지역을
쳐내는 1차 관문(«[대전] 말하지 못한 사랑»)이고, `region.py` 는 주소·지오코딩이 준
**행정구역 값**으로 판정하는 2차 관문이다. 이름 휴리스틱은 '세종문화회관'에서 새고,
행정구역 값은 주소가 없는 후보에서 비므로 둘 다 필요하다.

---

### UR-04 — 신뢰 가능한 정보 확인

| | |
|---|---|
| **코드** | `app/tools/verify.py` (235줄) · `discovery.classify` |
| **데이터** | `place_snapshots` |
| **화면** | `Timeline.tsx` 의 `✓ 공식정보 확인` / `확인 필요` 배지 |

```
verified     공식 출처와 일치        → 그대로
needs_check  정보 부족               → 점수 0.8배 + '확인 필요' 표시
excluded     불일치·종료 확인        → 제외
```

**규칙 — `needs_check` 를 버리지 않는다.** 소규모 공방·독립서점은 공식 정보가
원래 부실하다. 전부 떨구면 상시 문화공간 추천이 통째로 사라진다.

**2단계 분리** — 1단계(15초)에서 예산이 부족하면 검증을 건너뛰고,
`POST /threads/{id}/verify` 가 나중에 채운다. 앱이 자동 호출한다.

---

### UR-05 — 맞춤 일정 자동 생성

| | |
|---|---|
| **코드** | `app/graph/subgraphs/itinerary/` (9모듈, 최대 264줄) — `schedule.py` |
| **출력** | `Itinerary` (항목별 도착·출발·이동수단·선택 이유) |

**스케줄링은 LLM이 아니라 결정론적 코드가 한다.** LLM에게 시각 계산을 맡기면
이동시간과 운영시간을 지어낸다. 재현 가능해야 같은 조건에서 같은 일정이 나온다.

```
① _meal_slot()        식사는 시간대가 정해져 있다 — 자리를 먼저 잡는다
② _apply_dwell()      체류시간 결정 (아래)
③ _best_leg() ×N      구간별 이동수단
④ _measure_legs()     실측 (예산 범위 안에서)
⑤ _reflow()           측정 결과로 시계를 다시 흘린다
⑥ _reserve_to_dest()  도착지까지 갈 시간 확보
```

**체류시간 (`_apply_dwell`)** — 우선순위가 있다.

| 조건 | 처리 |
|---|---|
| 사용자가 말함 | `[dwell_min, dwell_max]` 로 선형 매핑 |
| 말 안 함 + 기록 있음 | `avg_dwell_min / 60` 배율 (0.7~1.6 상한) |
| 기록 없음 | 소스별 기본값 그대로 |

어느 쪽이든 **장소별 상대 순서는 보존한다.** 미술관이 카페보다 오래 걸린다는 건
사용자가 범위를 정했다는 사실과도, 오래 머무는 편이라는 사실과도 별개다.

**점수 함수**

```
score(c) = final_score(c) − travel_min/120 + (10 if 사용자 확정 장소 else 0)
final_score = 0.6 × relevance + 0.4 × personal_score
```

`+10` 은 사용자가 "유지"를 고른 장소가 재계획 때 밀려나지 않게 하는 잠금이다.
**사용자 결정을 뒤집지 않는다는 원칙이 점수 함수 안에 들어 있다.**

---

### UR-06 — 지도 기반 동선 확인

| | |
|---|---|
| **코드** | `app/tools/maps.py` (402줄) · `app/tools/kakao_local.py` (130줄) · `app/tools/routing.py` (220줄) |
| **화면** | `mobile/src/components/NaverMap.tsx` (580줄) |
| **엔드포인트** | `POST /threads/{id}/routes` · `POST /reroute` |

**무료 경로 API 3종을 수단별로 나눠 쓴다.**

| 수단 | 제공자 | 비고 |
|---|---|---|
| 자동차 | NAVER Directions 5/15 | 자동차 전용 |
| 도보·자전거 | OpenRouteService | Matrix로 N×N 1콜 |
| 지하철·버스 | ODsay LAB | `loadLane` 로 노선 선형까지 |

**최단루트는 시간만 보지 않는다.** 경로 API가 알려주지 않는 비용을 시간으로 환산한다.

```python
PARKING_PENALTY_MIN  = {"none": 15, "nearby": 8, "paid": 3, "free": 0, "unknown": 5}
WALK_PREFERENCE_MIN  = 5    # 5분 차이면 걷는 게 낫다
TRANSFER_PENALTY_MIN = 4    # 환승 1회의 체감 부담
```

**규칙 — 명시한 수단은 절대 바꾸지 않는다.** 수단을 섞는 건 `best` 일 때뿐이다.
"도보로 짜줘"라고 했는데 지하철 아이콘이 뜨면 그 계획은 실행할 수 없다.

**실제 경로 선형** — `travel_path` 에 좌표 배열이 들어간다(구간당 80~130점).
직선이 아니라 지하철 노선·도로를 따라 그려진다. 1단계에서는 비고, 2단계
`POST /threads/{id}/routes` 가 채운다.

---

### UR-07 — 날씨 기반 일정 추천

| | |
|---|---|
| **코드** | `itinerary.ctx_weather` · `app/tools/weather.py` (332줄) |
| **외부** | 기상청 API허브 단기예보 |
| **검증** | `validation.check_weather` |

```
ctx_weather → risky_hours 산출 → assemble_constraints 가 실내 우선 배치
            → check_weather 가 남은 야외 일정을 이슈로 올림
```

**대안을 실제 장소로 준다.** '비 오니 실내로 바꾸세요'만으로는 사용자가 직접
찾아야 한다. `indoor_alternatives()` 가 같은 시간대에 갈 수 있는 실내 후보를
**원래 자리에서 가까운 순**으로 골라 카드에 싣는다.

날씨 조회는 예산을 인지한다 — 있으면 좋은 정보지 일정의 전제가 아니다.

---

### UR-08 — 주변 장소 추천

| | |
|---|---|
| **코드** | `detect_gaps` → `dispatch_nearby` ⟨Send⟩ → `nearby_search` → `rerank_nearby` → `fill_gaps` |
| **외부** | **카카오 Local**(1순위, 좌표+반경 네이티브) → 네이버 지역검색(폴백) |

**빈틈 탐지 기준** — 유휴 40분 이상, 종료 후 잔여 60분 이상, 그리고 **첫 일정 앞**.

**반경은 남은 시간에 비례한다** (60분 미만 500m, 이상 1,200m).
"가면 못 돌아오는 추천"을 막는다.

팬아웃된 노드는 전체 State를 못 보므로 예산(`deadline`)을 payload에 실어 보낸다.

---

### UR-09 — 일정 직접 수정

| | |
|---|---|
| **재계획** | `RequestType.PLAN_MODIFY` → `current_plan` → `itinerary` (✅) |
| **수단 변경** | `POST /reroute` — 장소는 그대로, 구간만 재계산 (✅) |
| **선택 반영** | `nodes.finalize` → `hitl_decisions` 저장 (✅) |
| **수정 행동 학습** | `nodes.persist` → `repo.save_plan_edits` → `plan_edits` (✅ 2026-08-17) |
| **되읽기** | `profile.rebuild_profile` → `frequent_removals` · 체류시간 보정 (✅) |

**신호가 두 곳에서 나온다.**

```
① 확정 카드 선택   nodes._decision_signals(state)     drop→remove · replace · reorder · change_transport
② 일정 diff        writer.extract_edit_signals(before, after)
   →  _merge_signals()  같은 사건을 두 번 세지 않는다
   →  repo.save_plan_edits(user_id, plan_id, signals)   신호 하나 = 행 하나
```

①을 따로 두는 이유 — **처음 만든 일정에서 카드로 장소를 빼면 diff 가 아무것도 못 본다.**
비교 대상(`current_itinerary`)은 수정 요청일 때만 불러오기 때문이다. 사용자가 가장 분명하게
«싫다»고 말한 순간이 바로 그때다.

**같은 선택이 두 번 쌓이지 않는다.** 카드에서 나온 신호의 id 는 결정에서 만들고
(`dec-{advisory_id}-{option_id}`), diff 신호의 id 는 «일정·행동·대상»으로 만든다.
저장 쿼리가 이 id 로 중복을 거른다 — 스레드 상태에 남은 예전 결정이 다음 요청에서
다시 지나가도, 회피 가중치가 부풀지 않는다.

**`places.id` 가 아닌 참조는 FK 에서 뺀다.** 외부 API 후보는 `kopis:PF…` 같은 식별자를
들고 있어 그대로 넣으면 INSERT 가 통째로 죽는다. FK 자리는 비우고 원본은 `detail->>'from_ref'`
에 남긴다 — 재집계는 그쪽도 읽는다.

---

### UR-10 — 개인 아카이브 관리

| | |
|---|---|
| **코드** | `POST /visits` → `memory/writer.write_experience` |
| **데이터** | `visits` (32) · `plans` · `experience_embeddings` (32, `vector(1024)`) |
| **검색** | `memory/retriever.py` — HNSW `m=16, ef_construction=64` |

```
방문 기록 → summarize_experience() → 임베딩(NVIDIA, 1024차원) → experience_embeddings
                                   → rebuild_profile() → taste_profiles
```

**3 facet 병렬 검색** — 단일 질의로는 세 종류 이웃을 동시에 못 잡는다.

| facet | 잡는 것 |
|---|---|
| `similar_place` | 비슷한 장소·지역·카테고리 |
| `context_match` | 동행자·이동수단·계절이 비슷했던 상황 |
| `friction_edit` | 불편했던 경험과 그때 한 수정 |

facet 결과는 RRF로 재융합하고 cross-encoder로 리랭크한다
(`nvidia/llama-nemotron-rerank-1b-v2`, 0.45초).

**규칙 — 아카이브 실패가 요청을 죽이지 않는다.** 임베딩 호출까지 가드 안에 넣는다.
바깥에 두었더니 NIM이 502를 낸 순간 예외가 노드를 뚫고 나가 요청 전체가 죽었다.

**장소 연결 (`link_place_ids`)** — 외부 소스 후보를 카탈로그 `places` 행에 잇는다.
좌표 555m + 이름 유사도(pg_trgm 0.35)를 함께 본다. 둘 중 하나만으로는 오연결이 난다.

---

### UR-13 — 대안 확인 및 선택 (HITL)

| | |
|---|---|
| **코드** | `validation.build_confirm_cards` → `nodes.human_review` → `nodes.finalize` |
| **화면** | `AdvisoryCard.tsx` |
| **엔드포인트** | `POST /resume` · `POST /resume/sync` |

```
검증 6종 ∥ → triage → build_confirm_cards → interrupt() → 사용자 선택 → finalize
                                                        ↘ 재계획 필요 → itinerary
```

**분기 조건은 AND다.** `auto_fixable=False` **이면서** `severity >= 2`.
OR로 읽으면 자동으로 고칠 수 있는 사소한 이슈까지 전부 올라가 카드가 쌓이고
정작 중요한 경고가 묻힌다.

**카드에는 항상 다섯 가지가 들어간다** — (1) 발견된 문제 (2) 관련 기록·공식정보
(3) 일정에 미치는 영향 (4) 변경 이유 (5) 선택지.
**첫 선택지는 항상 "그대로 진행"이다.** 변경이 기본값이면 사용자는 사실상
자동 변경을 승인하게 된다.

**규칙 3개 — 전부 실제 사고에서 나왔다.**

| 규칙 | 안 지키면 |
|---|---|
| 카드 이름은 `Issue.place_name` 에서 | seq가 재배치돼 'A 확인 필요' 아래 B 이야기가 적힌다 |
| 카드 id는 `adv-{kind}-{place}` 로 고정 | 재계획마다 새 카드 → 이슈 6건이 카드 21장 |
| `issues`·`advisories` 는 `replace_list` | 해결된 이슈와 사라진 장소의 카드가 영원히 남는다 |

`MAX_REPLAN_ROUNDS=2` 로 `hitl → itinerary` 순환을 끊는다.

---

### UR-14 — AI 판단 근거 확인

| | |
|---|---|
| **코드** | 전 노드가 `Evidence` 를 State에 누적 |
| **엔드포인트** | `GET /threads/{id}/evidence/{evidence_id}` |
| **화면** | `EvidenceSheet.tsx` |

**기능이 아니라 데이터 계약이다.** 노드는 결과와 함께 반드시 근거를 남긴다.

| 종류 | 남기는 곳 |
|---|---|
| `web` | `discovery.search_web` — 후보가 못 된 검색 결과도 근거로는 남는다 |
| `official` | `tools/verify.py` — 공식 출처 대조 |
| `rule` | `validation.build_confirm_cards` — 어떤 검증 규칙이 걸렸는지 |
| `archive` | `memory/retriever.py` — 과거 기록 |

**모바일 페이로드 절감** — 응답에는 `evidence_ids` 만 싣고 원문은 지연 로드한다.

---

### UR-17 — 개인정보 통제

| | |
|---|---|
| **코드** | `db/001_schema.sql` |
| **원칙** | 사용자 삭제 시 파생 데이터가 남지 않는다 |

`users` 를 참조하는 모든 테이블에 `ON DELETE CASCADE` 를 건다 —
`visits` · `plans` · `plan_edits` · `experience_embeddings` · `taste_profiles` ·
`hitl_decisions` · `preference_cards` · `user_collections`.

`place_snapshots` 는 `ON DELETE SET NULL` — 공식정보 스냅샷은 개인정보가 아니라
사실 기록이므로, 사용자가 지워져도 다른 사용자의 검증 근거로 남는다.

---

### UR-28 — 캘린더로 일정 확인 ★핵심 · ✅ 구현 (2026-08-17)

| | |
|---|---|
| **의도** | 만든 일정을 날짜로 다시 연다. 기록·분석 선순환의 진입점 |
| **데이터** | ✅ `plans (id, user_id, plan_date, version, status, payload jsonb)` · `idx_plans_user_date (user_id, plan_date DESC)` |
| **쓰기** | ✅ `nodes.persist` → `db/repo.save_itinerary()` — 일정마다 `plan_date` 와 `Itinerary` 전체를 저장한다 |
| **읽기** | ✅ `db/repo.list_plans(user_id, frm, to)` 요약만 · `db/repo.load_plan(plan_id)` 전체 |
| **엔드포인트** | ✅ `GET /plans/{user_id}?from=&to=` · `GET /plans/detail/{plan_id}` |
| **화면** | ✅ `mobile/app/(tabs)/calendar.tsx` — 월 그리드 → 날짜 탭 → `Timeline` + `NaverMap` |

**새 테이블도 마이그레이션도 필요 없었다.** 일정은 이미 날짜와 함께 쌓이고 있었고,
빠져 있던 것은 기간으로 읽는 질의와 화면 둘뿐이었다.

```
① db/repo.py       list_plans(user_id, frm, to)   plan_date 범위 · jsonb 로 서버에서 요약
                   load_plan(plan_id)             그날 일정 전체(Itinerary)
② api/main.py      GET /plans/{user_id}?from=&to=   기본 범위 = 지난 3개월 ~ 앞으로 1개월
                   GET /plans/detail/{plan_id}      ★ `/plans/{user_id}` 와 한 자리에서
                                                      부딪히므로 경로에 detail 을 넣었다
③ mobile           (tabs)/calendar.tsx
④ 유도             기록 없는 지난 일정에 «기록 남기기» → 기존 app/visit.tsx 로 (UR-10)
```

**규칙 — 캘린더는 읽기 화면이다.** 일정 변경은 기존 재계획 경로(`POST /chat` ·
`POST /reroute`)를 그대로 쓴다. 두 번째 편집 경로를 만들면 `plan_edits` 학습 신호가
두 갈래로 갈려 UR-09가 다시 반쪽이 된다.

**주의 — 목록 응답에 `payload` 전문을 실으면 안 된다.** `Itinerary` 하나가 수십 KB다.
한 달치를 그대로 내리면 NFR-09(모바일 페이로드)가 깨진다. 목록은 요약만, 상세는 별도 호출.

**엔드포인트를 더하면 `tests/test_docs_contract.py::test_api_surface_is_documented` 가
먼저 깨진다.** 그때 `EXPECTED_ROUTES` 와 이 문서 §5 를 함께 고친다 — 순서는 문서가 먼저다.

---

### UR-40 — 과거 불편의 선제 경고 · ✅ 구현 (2026-08-17)

| | |
|---|---|
| **의도** | 기획안 2.1-② «미들그라운드는 주차가 어려웠어요» + 대안 3갈래 |
| **코드** | `validation.check_friction` — 검증 6종 중 하나 |
| **입력** | `archive_hits`(facet `friction_edit` 이 회수) + 이번 `itinerary` |
| **출력** | `Issue(kind="past_friction")` + `Evidence(kind="archive")` → 확인 카드 |
| **화면** | `AdvisoryCard.tsx` — 선택지는 `_options_for()` 가 만든다 |

```
check_friction
  ├ 이번 일정에 실제로 들어온 place_id 만 본다   (후보로 스쳐 간 곳은 카드로 안 만든다)
  ├ friction 태그가 있고 별점 ≤ 3.5 인 기록만    (_FRICTION_RATING_CEIL)
  ├ 장소당 가장 나빴던 기록 하나로 접는다        (한 곳에 기록 셋이면 카드도 셋이 된다)
  └ Issue(severity=2, auto_fixable=False) + Evidence(그 방문의 원문)
```

**왜 archive 가 아니라 validation 에 있나.** 확인 카드는 `validation` **한 곳에서만**
만든다는 원칙(§7) 때문이다. 두 곳에서 만들면 같은 사안이 카드 두 장으로 올라간다.

**태그 이름을 그대로 쓰지 않는다.** `_FRICTION_KO` 가 `parking → «주차가 어려웠»` 로
옮긴다. 그러지 않으면 사용자 화면에 «parking 이 있었어요»가 뜬다.

**별점 상한(3.5)을 둔 이유.** 4.5점을 주고도 «조금 붐볐다»를 남기는 경우가 흔하다.
그것까지 카드로 올리면 정작 3.0점 이하의 진짜 불편이 카드 더미에 묻힌다.

> **이 자리가 한동안 비어 있었다.** UR-11·UR-12 를 걷어내면서 `archive.build_advisories`
> 와 `validation.check_archive` 가 함께 사라졌는데, **소비자(스키마·카드·선택지)는 그대로
> 남았다.** 그래서 기획안의 대표 화면이 «코드상 도달 불가능»한 상태로 몇 달을 보냈다.
> 되살릴 때 검증이 5→6종이 되어 `test_docs_contract.py` 가 먼저 깨졌고, 그 테스트 이름을
> `test_validation_runs_six_checks` 로 바꾸면서 이 문서·ARCHITECTURE §8.1·STRUCTURE 6단계를
> 함께 고쳤다.

---

## 3. 워크플로 — 요청 하나의 전체 경로

```
[1] POST /chat                     main.py         스레드 확보 · SSE 시작
[2] classify                       router/         UR-02  발화 → 조건 + 좌표
[3] ⟨조건부 팬아웃⟩ fan_out          router/         필요한 Agent만 깨운다
     ├─ archive        (개인화)                     UR-10
     ├─ discovery      (후보 탐색)                  UR-03 · UR-04
     └─ current_plan   (수정 요청 시)               UR-09
[4] merge_context                  nodes.py        병렬 브랜치 합류
[5] itinerary                      itinerary/      UR-05 · UR-06 · UR-07 · UR-08
[6] validation (6종 ∥)             validation.py   이슈 판정
[7] hitl        ─── 확인 필요 ───→ interrupt()      UR-13
     └─ 이상 없음 ──┐
[8] finalize ───────┴→ persist                     UR-09 · UR-10
[9] compose                        nodes.py        UR-14  근거를 붙인 답변
──────────────── 여기까지 1단계 (목표 15초) ────────────────
[10] POST /threads/{id}/routes     실제 경로 좌표    UR-06
[11] POST /threads/{id}/verify     공식정보 대조     UR-04
```

**2단계로 나눈 이유** — 사용자는 이미 일정을 보고 있다. 선이 정확해지고 배지가
붙는 건 나중이어도 된다. 1단계에서 전부 하면 15초를 넘긴다.

**실측 분포** (동일 요청 5회): 중앙값 **5.1초**, 최소 4.4초, 최대 17.7초.
편차는 대부분 외부 API 지연이다.

---

## 4. 파일 지도

### 백엔드 (10,496줄 · 54파일)

```
app/
├── api/main.py              878  엔드포인트 23개 · SSE · 2단계 API
├── graph/
│   ├── build.py             142  그래프 조립 + Postgres 체크포인터(커넥션 풀)
│   ├── router/            1,268  UR-02  분류 + 규칙 파서 + 좌표 확정 (6모듈)
│   ├── nodes.py             376  조율 노드 6개 (merge/hitl/finalize/persist/compose)
│   ├── state.py             205  State 정의 + 서브그래프 입출력 스키마
│   ├── reducers.py          135  병합 리듀서 6종 (STRUCTURE.md 4단계 표가 원천)
│   ├── budget.py             59  응답 시간 예산 (타임아웃과 다르다)
│   ├── serde.py                  체크포인트 직렬화 허용 타입
│   └── subgraphs/
│       ├── itinerary/     1,552  UR-05·06·07·08 (9모듈)
│       ├── discovery.py     534  UR-03·04
│       ├── validation.py    360  검증 6종 → triage → 카드
│       └── archive.py       200  UR-10  facet 검색 + 리랭크
├── tools/
│   ├── culture_api.py       500  UR-03  KCISA + 공공데이터포털
│   ├── maps.py              402  UR-06  지오코딩 · 이동행렬 · 주변검색
│   ├── weather.py           332  UR-07  기상청
│   ├── http.py              244  이벤트 루프 인지 HTTP 클라이언트
│   ├── verify.py            235  UR-04
│   ├── routing.py           220  UR-06  NAVER · ORS · ODsay
│   ├── local_catalog.py     199  카탈로그 조회 + 장소 연결
│   ├── websearch.py         141  Tavily → Exa
│   ├── region.py            134  UR-18  시·도 · 시군구 판정
│   ├── kakao_local.py       130  UR-08  좌표 기준 반경 검색 (1순위)
│   └── base.py               61  예산 인지 safe_call · TTL 캐시
├── memory/
│   ├── curation.py          324  즐겨찾기 · 자동 큐레이션
│   ├── retriever.py         233  UR-10  facet 검색 + RRF + 리랭크
│   ├── writer.py            166  UR-10  경험 요약 + 임베딩
│   └── profile.py           269  취향 프로필 재구성
├── llm/provider.py          245  역할별 모델 배분 (router/planner/writer/fast)
├── schemas.py               485  도메인 모델 전부
└── config.py                166  설정 · API 키
```

### 모바일 `src/` (4,042줄 · 화면 `app/` 2,500줄은 별도)

```
mobile/src/
├── components/
│   ├── NaverMap.tsx         585  UR-06  지도 · 구간별 선형 (웹/네이티브 2구현)
│   ├── RoutePoints.tsx      241  UR-02  출발·도착·시각 칩
│   ├── RegionPicker.tsx     241  UR-02  지역 선택
│   ├── SaveToCollection.tsx 188  즐겨찾기
│   ├── Composer.tsx         142  UR-02  발화 입력
│   ├── AdvisoryCard.tsx     114  UR-13  확인 카드
│   ├── EvidenceSheet.tsx    112  UR-14  근거 열람
│   ├── TransportPicker.tsx  111  UR-06  이동수단
│   └── Timeline.tsx          93  UR-05  일정 타임라인
├── api/
│   ├── client.ts            653  SSE + sync 폴백 + 2단계 호출
│   ├── mock.ts              472  오프라인 개발용
│   └── types.ts             265  서버 계약
└── hooks/useCultureMate.ts  257  상태 관리 · fillRoutes()
```

### 데이터 (13 테이블 + 체크포인트 3)

| 축 | 테이블 | UR |
|---|---|---|
| 사실 | `places` · `place_snapshots` · `api_cache` | UR-03 · UR-04 |
| 계획 | `plans` · `plan_edits` | UR-05 · UR-09 · UR-28 |
| 경험 | `visits` · `experience_embeddings` · `taste_profiles` | UR-10 |
| 결정 | `hitl_decisions` | UR-13 |
| 취향 | `preference_cards` | UR-01 · UR-31 |
| 수집 | `user_collections` · `user_collection_places` | — |
| 사용자 | `users` | UR-17 |

---

## 5. 엔드포인트 23개

| 메서드 · 경로 | 용도 | UR |
|---|---|:--:|
| `POST /chat` | 대화 (SSE) | 전체 |
| `POST /chat/sync` | 비스트리밍 폴백 | 전체 |
| `POST /resume` · `/resume/sync` | HITL 선택 반영 | UR-13 |
| `GET /threads/{id}/state` | 스레드 상태 | — |
| `POST /reroute` | 이동수단만 변경 | UR-06 · UR-09 |
| `POST /threads/{id}/routes` | 구간 실측 (2단계) | UR-06 |
| `POST /threads/{id}/verify` | 공식정보 대조 (2단계) | UR-04 |
| `GET /threads/{id}/evidence/{eid}` | 근거 원문 | UR-14 |
| `POST /visits` | 방문 기록 | UR-10 |
| `GET /report/{user_id}` | 취향 집계 | UR-10 · UR-09 |
| `POST /preferences/cards` | 취향 카드 등록 (묶음) | UR-01 · UR-31 |
| `GET /preferences/cards/{user_id}` | 등록한 카드 조회 | UR-01 |
| `GET /plans/{user_id}?from=&to=` | 기간별 일정 목록 (요약만) | UR-28 |
| `GET /plans/detail/{plan_id}` | 그날 일정 전체 | UR-28 |
| `POST /collections` 외 3개 | 즐겨찾기 | — |
| `GET /geocode` · `/whereami` | 좌표 보조 | UR-02 |
| `GET /health` · `/diagnostics` | 운영 | — |

**아직 없는 엔드포인트** — 지금은 없다. 이 칸은 비워 두지 말고, 빠진 것이 생기면
다시 적는다. 없는 것을 있는 것처럼 적는 것보다 **무엇이 빠졌는지 적는 편**이
다음 사람에게 쓸모 있기 때문이다.

더할 때는 `tests/test_docs_contract.py` 의 `EXPECTED_ROUTES` 와 이 표를 **같은 커밋에서**
고친다. 그 테스트가 먼저 깨지는 것이 설계 의도다.

---

## 6. 횡단 관심사

### 6.1 예산 (타임아웃과 다르다)

타임아웃은 시간이 다 되면 **결과 없이** 끊는다. 예산은 각 단계가 남은 시간을 보고
**스스로 범위를 줄여** 어떻게든 결과를 낸다.

```python
COST_VERIFY_BATCH = 2.5    COST_TRAVEL_MATRIX = 3.0
COST_LEG_MEASURE  = 0.4    COST_COMPOSE = 2.5   # 이건 포기할 수 없다
```

**도구 타임아웃도 예산을 본다** (`safe_call(deadline=)`). 고정 12초는 총예산 15초와
모순이었다 — 기상청이 응답하지 않는 것만으로 예산 전체가 날아갔다.

### 6.2 실패 격리

| 실패 | 대응 |
|---|---|
| 외부 API | `safe_call` → 기본값, 그래프 계속 |
| LLM 구조화 출력 | 전 지점 규칙 폴백 (`router`, `plan_facets`, `extract_relevant`) |
| 임베딩·리랭커 | 조용히 비우되 **로그는 남긴다** |
| DB 연결 | 커넥션 풀 + 체크아웃 시 검사 → 자가 복구 |
| 체크포인터 | Postgres 실패 시 InMemorySaver 폴백(경고 로그) |

### 6.3 모델 배분

| 역할 | 모델 | 근거 |
|---|---|---|
| router | `openai:gpt-4o-mini` | 4,730자 스키마. 작은 모델은 지명을 통째로 비운다 |
| planner | `openai:gpt-4o-mini` | 병렬 구간이라 체감 0 → 품질 택함 |
| writer | NIM `llama-3.1-8b` | 응답 시간의 55%. 4o-mini 15~17s vs 8B 3.5~5.2s |
| fast | `openai:gpt-4o-mini` | 2단계라 예산 밖 → 정확도 우선 |
| 임베딩 | NIM 1024차원 | 0.65s · `vector(1024)` 와 고정 |
| 리랭크 | NIM `llama-nemotron-rerank-1b-v2` | 0.45s |

⚠️ `meta/llama-3.3-70b-instruct` 는 무료 티어에서 구조화 출력이 30~60초 걸려
사실상 못 쓴다. **모델을 바꾸기 전에 `scripts/bench_models.py` 로 재 볼 것.**

### 6.4 배포

컨테이너 **1개** (`culturemate`, 포트 8000) — PostgreSQL + API 통합.
베이스 이미지가 `pgvector/pgvector:pg18` 이라 기존 볼륨을 그대로 이어받는다.

> 한 컨테이너에 프로세스 둘은 일반적인 운영 방식이 아니다. postgres가 죽으면
> 컨테이너 전체가 내려간다. 데모용 선택이고, 운영에서는 분리하는 편이 낫다.

---

## 7. 제외된 기능 (이력)

| UR | 기능명 | 제외 사유 | 남은 흔적 |
|:--:|---|---|---|
| **UR-11** | 과거 경험 기반 개인화 | `build_advisories` 만 제거. `personal_score`·facet 검색·리랭크는 **유지** | 경고 카드 생성 없음 |
| **UR-12** | 경험 기반 주의 알림 | 복잡도 대비 효용 낮음 | `validation.check_archive` 삭제 (검증 6→5종) |
| **UR-15** | 취향 리포트 | 대화 경로만 제거 | `report` 서브그래프 삭제. `GET /report/{user_id}` 와 모바일 취향 탭은 **유지** |
| **UR-16** | 일정 이미지 공유 | 미구현 상태로 제외 | 없음 |

**UR-12 제거의 부수 효과** — 사용자 확인 카드는 이제 `validation` **한 곳에서만**
만든다. 두 곳에서 만들면 같은 사안이 카드 두 장으로 올라간다.

**제거가 남긴 구멍 — 2026-08-17 에 메웠다(UR-40).** 아래는 그때의 기록이다.
 UR-11·UR-12를 함께 걷어내면서
**«과거 불편했던 곳을 일정에 넣기 전에 알린다»가 통째로 사라졌다.** 이것은 기획안 2.1-②의
핵심 화면이고, 서비스 한 줄 정의(«기록을 넘어 다음 행동에 개입하는 지능형 아카이브»)가
성립하려면 있어야 하는 기능이다. 지금 아카이브 기록은 `archive_hits.meta` 에 **표시만** 되고
경고로 승격되지 않는다.

- 남은 부품: `Issue.kind="past_friction"`(schemas) · `_options_for()` 선택지 · `friction_edit` facet
- 없던 것: 그 `Issue` 를 **만드는 노드** → `check_friction` 으로 되살렸다
- 복원 설계는 위 **UR-40** 절. 카드는 `validation` 한 곳에서만 만든다는 이 원칙은 유지한다.

`report` 서브그래프는 파일까지 삭제됐다(`app/graph/subgraphs/` 에 4개만 있다).
**`RequestType` 에도 `taste_report` 가 없다** — 취향 리포트는 그래프가 아니라
`GET /report/{user_id}` 로만 간다.

---

## 8. 검증 상태

```
pytest    193 passed, 1 skipped   (수집 194개)
ruff      All checks passed!
외부 API   11/11 (naver_geocode · naver_directions · weather · culture_api ·
                 culture_facility · kakao_local · naver_local_search · websearch ·
                 ors · odsay · llm)
DB        places 2,092 · visits 32 · embeddings 32 · users 1
```

**문서-소스 정합성 테스트 14개** (`tests/test_docs_contract.py`) 가 이 문서군의
숫자를 코드와 대조한다 — 그래프 노드 수, 검증 종류, 엔드포인트 수, 라우팅 테이블,
HITL AND 조건, 주요 파일 줄 수(±30). **구조를 바꾸면 이 테스트가 먼저 깨진다.**
