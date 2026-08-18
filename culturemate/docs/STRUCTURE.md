# CultureMate 소스 구조 — 단계별

`docs/ARCHITECTURE.md` 가 **왜 그렇게 설계했는가**를 적은 문서라면,
이 문서는 **지금 소스가 실제로 어떻게 도는가**를 적는다.
기능(UR) 하나가 어느 파일에 있는지 찾으려면 [FUNCTIONAL_MAP.md](FUNCTIONAL_MAP.md) 를 본다.

요청 한 번이 지나가는 길을 순서대로 따라가고, 각 단계마다 *어느 파일이
책임지는지*, *무엇을 채우는지*, *깨뜨리면 안 되는 규칙이 무엇인지*를 적는다.
규칙 항목은 대부분 실제로 한 번씩 깨져 본 것들이다.

- 검증 기준: `langgraph 1.2.10` · `langchain-core 1.5.3` · `fastapi 0.141.1` ·
  `pydantic 2.13.4` (context7 문서 대조 완료 — 12장 참고)

---

## 0. 전체 지도

```
[모바일]  Composer 입력
   │  POST /chat  (SSE)
   ▼
[API]    app/api/main.py            ── 1단계
   │  graph.astream(stream_mode=["updates","messages"])
   ▼
[그래프]  app/graph/build.py
   │
   ├─ 2단계  classify        router/          요청 이해 + 좌표 확정
   ├─ 3단계  팬아웃           archive / discovery / current_plan
   ├─ 4단계  merge_context   nodes.py         가진 재료 합치기
   ├─ 5단계  itinerary       subgraphs/itinerary/     일정 편성
   ├─ 6단계  validation      subgraphs/validation.py  이슈 판정
   ├─ 7단계  hitl            nodes.py         사람에게 묻기(중단)
   ├─ 8단계  finalize        nodes.py         선택 반영
   ├─ 9단계  persist         nodes.py         아카이브 기록 + 취향 학습
   └─ 10단계 compose         nodes.py         근거를 붙인 답변
   ▼
[모바일]  지도 · 타임라인 · 대화접기 갱신   ── 11단계
```

---

## 1단계 — 요청을 받는다 (`app/api/main.py`)

| 하는 일 | 코드 |
|---|---|
| 스레드 확보 | `chat()` / `chat_sync()` — `thread_id` 하나로 SSE·sync 두 경로가 같은 상태를 본다 |
| 스트리밍 | `_stream_events()` → `graph.astream(payload, stream_mode=["updates","messages"])` |
| 오류 봉합 | `_stream()` 이 전체를 try/except 로 감싸 `error` 이벤트를 내보낸다 |
| 칩 재료 | `_resolved(values)` — 화면 상단 칩에 올릴 값만 추려 `resolved` 로 내려보낸다 |

**규칙**

- SSE 본문 안에서 예외가 나면 연결만 끊기고 클라이언트는 이유를 모른다.
  그래서 `_stream()` 은 **반드시** 예외를 잡아 `event: error` 로 바꿔 보낸다.
- `_resolved()` 는 `start_time_assumed` 가 켜져 있으면 `start_time` 을 **빼고** 내려보낸다.
  시스템이 임의로 채운 값을 칩에 올리면 사용자는 자기가 말한 줄 안다.

**주변 엔드포인트**

```
POST /chat  /chat/sync  /resume  /resume/sync      대화
POST /reroute                                      이동수단 바꿔 재계산
POST /threads/{id}/routes                          구간 실측 — 실제 경로 좌표(2단계)
POST /threads/{id}/verify                          일정 장소 공식정보 대조(2단계)
GET  /threads/{id}/state  /threads/{id}/evidence/{eid}
POST /visits            GET /report/{user_id}      아카이브·취향
POST /collections       DELETE /collections/{id}[/places/{pid}]
GET  /curations/{user_id}                          즐겨찾기
GET  /plans/{user_id}?from=&to=                    캘린더 목록(요약만) — UR-28
GET  /plans/detail/{plan_id}                       그날 일정 전체     — UR-28
POST /preferences/cards                           취향 카드 등록     — UR-01 · UR-31
GET  /preferences/cards/{user_id}                 등록한 카드        — UR-01
GET  /geocode  /whereami  /health  /diagnostics     보조
```

**아직 없는 것** — 지금은 없다. 빠진 자리가 생기면 있는 것처럼 적지 말고 여기 남긴다.

---

## 2단계 — 요청을 이해한다 (`app/graph/router/` · `classify`)

이 노드 하나가 **문장 → 실행 가능한 조건**의 전부를 담당한다. 순서가 곧 우선순위다.

```
① LLM 추출        asyncio.wait_for(...)         구조화 추출, 실패해도 죽지 않는다
② 규칙 보정        _merge_rules(decision, rules) LLM이 놓친 걸 정규식이 채운다
③ 화면값 덮어쓰기   _apply_override(...)          발화가 화면 잔재보다 세다
④ 취향 반영        _apply_taste(conditions, profile)
⑤ 좌표 확정        _resolve_places(conditions)   출발·도착·지역 → 좌표
```

### 2.1 규칙 파서 (`_safe_rules` → `_rule_conditions`)

| 상수 | 잡는 것 |
|---|---|
| `_LABEL_START` / `_LABEL_END` | `출발은 …` `도착지: …` 처럼 **라벨이 앞** |
| `_START_WORD` / `_END_WORD` | `수원역에서 출발해서` 처럼 **라벨이 뒤** |
| `_PARTICLE` | 조사 제거 (`영동대로` 가 `영동대` 가 되지 않게 홑 `로` 는 제외) |
| `_ADDRESSY` | 주소인가 역 이름인가 (`종로3가역` 은 주소가 아니다) |
| `_TRANSPORT` | `최단루트→best` `자가용→car` … |
| `_NOT_PLACE` | `지하철` 같은 수단어가 출발지로 새는 것을 막는다 |

**규칙**

- `_END_WORD` 는 `끝나고`·`돌아가고` 처럼 **`-고` 로 이어지는 연결어미를 제외**한다
  (`(?!고)` 룩어헤드). "끝나고 다시 수원역" 의 `끝나고` 는 종료가 아니라 접속이다.
- `_END_WORD` 는 **마지막 매치**를 쓴다. 문장 앞쪽의 `끝나고` 를 잡으면 도착지가 밀린다.
- 라벨 절의 끝은 `_span_end()` 로 정확히 끊는다. 예전엔 `+12` 같은 어림수를 썼고,
  그 결과 라벨 절이 다음 절을 통째로 삼켰다.

### 2.2 `_apply_override` — 발화가 화면을 이긴다

```python
if conditions.origin_name:       clean.pop("origin"), clean.pop("origin_name")
if conditions.destination_name:  clean.pop("destination"), clean.pop("destination_name")
if conditions.start_time:        clean.pop("start_time")
if conditions.end_time:          clean.pop("end_time")
```

이 네 줄이 없으면, 이전 화면에 남아 있던 출발지가 방금 말한 출발지를 덮는다.
사용자는 "부산역에서 출발" 이라고 말했는데 지도는 계속 서울을 가리킨다.

### 2.3 `_resolve_places` — 좌표를 여기서 확정하는 이유

> **이 프로젝트에서 가장 오래 걸린 버그다.**

원래는 탐색 서브그래프가 좌표를 채웠다. 서브그래프가 `conditions` 를 제자리에서
고쳐도 그 변경은 부모 상태로 **돌아오지 않는다** — `DiscoveryOutput` 에 `conditions`
키가 없었기 때문이다. LangGraph 는 서브그래프의 `output_schema` 에 있는 키만 부모로
올린다(context7: *"subgraph's output keys must be mapped back to the parent's state"*).

- `InMemorySaver` 에서는 같은 객체를 공유해 **우연히** 동작했다.
- `PostgresSaver` 는 단계마다 직렬화하므로 **통째로 사라졌다.**

지금은 두 겹으로 막았다.

1. 위치 해석을 라우터로 옮겼다. 라우터의 반환값에 담기면 이후 모든 단계가 같은 값을 본다.
2. `DiscoveryOutput` 에 `conditions` 를 명시했다.

의미상으로도 여기가 맞다 — **위치 해석은 '요청을 이해하는 일'이지 '장소를 찾는 일'이 아니다.**

출력 스키마가 막는 것이 하나 더 있다. 서브그래프는 **입력으로 받은 키까지 그대로 되돌려주므로**,
`archive` 와 `discovery` 가 병렬로 끝나는 순간 둘 다 `user_id` 를 반환해 `InvalidUpdateError` 가 난다.
리듀서가 없는 키는 «마지막 값 하나»만 담는 자리라 두 값을 받을 수 없기 때문이다(4단계 표 참고).

> **그림** — [병렬 실행 시 키 충돌이 나는 이유 · 출력 스키마로 막는 법](diagrams/parallel-key-conflict.svg)

### 2.4 출발지 우선순위 (확정)

```
1. 말한 출발지          "출발은 부산역"        → 무조건 이김
2. 말한 지역의 중심     GPS 와 30km 이상 차이   → 지역 중심이 출발지
3. 현재 위치(GPS)      아무 말도 없을 때만      → 기본값일 뿐
```

**현재 위치는 출발지가 아니다.** 정하지 않았을 때 대신 쓰는 값이다.
화면에서도 흐리게(`📍 현재 위치에서 출발`) 그려 둘을 구분한다.

---

## 3단계 — 필요한 것만 펼친다 (`router.fan_out`)

```python
g.add_conditional_edges(..., fan_out, ["archive", "discovery", "current_plan"])
```

요청 종류에 따라 실행할 Agent 만 켠다. **분기 대상은 셋이다** — 예전에 있던 `report`
브랜치는 서브그래프째 삭제됐고, `RequestType` 에도 `taste_report` 가 없다.
취향 리포트는 그래프를 타지 않고 `GET /report/{user_id}` 로만 간다.

### 3.1 archive — 아카이브 검색 (`subgraphs/archive.py`)

```
plan_facets → (Send 팬아웃) facet_search → fuse_rerank → extract_relevant → END
```

facet 으로 쪼개는 이유: "작년에 갔던 곳 말고" 같은 한 문장에 조건이 여러 개 섞여 있어
한 번의 벡터 검색으로는 어느 쪽도 만족시키지 못한다.

### 3.2 discovery — 후보 탐색 (`subgraphs/discovery.py`)

```
search_catalog ┐
search_events  ├→ normalize → (조건부) verify → classify → END
search_always_on│
search_web     ┘
```

**규칙** — `normalize` 는 **좌표가 없는 후보를 버린다.**
블로그 제목(`8월 서울 전시회 추천<BEST5>`)이 장소로 올라오던 사고를
`looks_like_place()` + 지오코딩 게이트로 막는다. 이름이 아니라 좌표가 자격이다.

**규칙 — 지역은 거리가 아니라 시·도로 판정한다** (UR-18, `tools/region.py`).
「서초구」 요청에 판교(경기 성남시)는 25km라 거리 상한 `MAX_ANCHOR_KM`(60km)을
통과한다. 그래서 지오코딩 응답(`addressElements`)·주소에서 시·도를 읽어
`GeoPoint.sido/sigungu` 에 싣고, **요청한 시·도가 아니라고 확인된** 후보를 버린다.
행정구역을 알 수 없는 후보는 통과시키고 거리 상한이 받는다 — 모르는 것까지 버리면
주소를 안 주는 공공 API 행사가 통째로 사라진다. 말한 구(區)에 있는 후보는
`REGION_BONUS` 로 앞에 오되 **잘리지는 않는다**(구 경계 ≠ 생활권).

---

## 4단계 — 재료를 합친다 (`nodes.merge_context`)

각 서브그래프 결과를 한 상태로 모은다. 병합 규칙은 `app/graph/reducers.py` 에 있다.

**리듀서는 6종이다.** 채널마다 «같은 것»의 정의가 달라 하나로 통일할 수 없다.

| 리듀서 | 쓰는 채널 | 규칙 |
|---|---|---|
| `MERGE_BY_ID` | archive_hits · edit_signals · verifications · place_diffs · gaps · evidence | 같은 id 는 나중 것으로 갱신 |
| `MERGE_BY_ADVISORY_ID` | decisions | advisory 당 하나만 (id 가 아니라 advisory_id 로 묶는다) |
| `merge_candidates` | **candidates · nearby** | 같은 장소는 **정보가 더 풍부한 쪽**을 남기고 점수는 max |
| `replace_list` | **issues · advisories** | 새 값이 오면 통째로 교체 (빈 값은 무시) |
| `append_unique_str` | trace | 순서대로 쌓되 중복은 버린다 |
| `merge_dict` | **context** | 얕은 병합 — `ctx_geo`·`ctx_hours`·`ctx_weather`·`ctx_preference` 넷이 같은 dict 에 각자의 칸을 쓴다 |

`messages` 는 LangGraph 기본 `add_messages` 를 쓴다. 위 6종은 이 프로젝트가 만든 것이다.

**주의 — `transport_mix` 는 State 채널이 아니다.** `Itinerary` 모델의 **필드**(`schemas.py`)이고,
`itinerary` 채널(리듀서 없음)에 통째로 실려 간다. 즉 재계획하면 **얕은 병합이 아니라 교체**된다.
문서가 한동안 이 값을 `merge_dict` 의 예로 들고 있었는데, 소스에서 `merge_dict` 가 붙은 채널은
`context` 하나뿐이다.

**규칙** — `MERGE_BY_ID` 는 **모듈 수준 싱글턴**이어야 한다.
LangGraph 는 같은 채널이 여러 스키마에 나타나면 리듀서의 *함수 객체 동일성*까지 비교한다.
`merge_by_id()` 를 호출할 때마다 새 객체가 생기므로 반드시 상수를 재사용한다.

---

## 5단계 — 일정을 짠다 (`subgraphs/itinerary/`, 9모듈)

가장 큰 모듈이라 안쪽을 다시 단계로 나눈다.

### 5.1 컨텍스트 수집 → `assemble_constraints`

```
ctx_geo         출발·도착·지역 좌표 (2단계에서 이미 확정된 값을 받는다)
ctx_hours       영업시간
ctx_weather     기상청 단기예보 — 야외 장소 판단에 쓴다
ctx_preference  취향 프로필
```

### 5.2 `schedule` — 편성 본체

```
① _meal_slot()        식사는 시간대가 정해져 있다. 먼저 자리를 잡는다
② _apply_dwell()      체류시간 결정 — 사용자가 말한 값이 항상 이긴다
                      말했으면 [dwell_min, dwell_max] 로 선형 매핑
                      말 안 했으면 과거 방문 평균(avg_dwell_min)으로 배율 보정
                      어느 쪽이든 장소별 상대 순서는 보존한다
③ _best_leg() ×N      구간마다 이동수단 결정
④ _measure_legs()     실제 거리·시간 측정 (예산 허용 범위 안에서)
⑤ _reflow()           측정 결과로 시계를 다시 흘린다
⑥ _reserve_to_dest()  도착지까지 갈 시간을 남겨 둔다
⑦ _endpoint_notes()   출발/도착 안내 문구
```

**규칙 — 종류별 몫은 «자리를 남기는 것»이지 «멈추는 것»이 아니다.**
`kind_quota` 가 걸린 그룹의 후보가 편성 시점에 하나도 없을 수 있다 — 카페는 탐색이
아니라 빈틈 채우기의 주변 검색에서 오기 때문이다. 이때 그 몫만큼 자리는 비워 두되
**다른 종류의 배치는 계속해야 한다.** 예전에는 여기서 그냥 `break` 해서, 카페 후보가
없으면 첫 반복에 탈출해 문화까지 한 곳도 못 넣고 **일정이 0곳**으로 나왔다
(「문화생활 추천해주고 디저트 맛집 2개」 — 2026-08-18). 남은 칸이 못 채운 몫보다
많을 때만 계속한다.

**규칙 — 총량(`stop_count`)과 몫(`kind_quota`)은 다른 정보다.**
「문화 2개 디저트 3개」처럼 **말한 종류 전부에 개수가 붙었을 때만** 총량을 그 합으로
확정한다. 「문화생활 + 디저트 2개」처럼 개수를 말하지 않은 종류가 섞이면 총량을
열어 둔다 — 2로 못 박으면 그 두 자리를 디저트가 다 가져간다.
그리고 「문화 **및** 식사 5개」처럼 **여러 종류를 나열하고 개수를 하나만** 말하면
그건 몫이 아니라 총량이다(`router/detect.py`).

### 5.3 이동수단 결정 — `_best_leg(frm, to, preferred, dest_parking)`

```python
BEST_CANDIDATE_MODES = ("walk", "subway", "bus", "car")
PARKING_PENALTY_MIN  = {"none": 15, "nearby": 8, "paid": 3, "free": 0, "unknown": 5}
WALK_PREFERENCE_MIN  = 5      # 5분 차이면 걷는 게 낫다
TRANSFER_PENALTY_MIN = 4      # 환승 한 번의 체감 비용
```

**규칙 — 명시한 수단은 절대 바꾸지 않는다.**
`best` 일 때만 `_fastest_leg()` 로 수단을 섞는다.
"도보로 짜줘" 라고 했는데 지하철 아이콘이 뜨면 그 계획은 실행할 수 없다.

### 5.4 `_reflow(items, day_end)` — 시계 다시 흘리기

측정된 이동시간과 화면의 시각이 어긋나면 일정표 전체가 거짓말이 된다.

- 고정 항목(`fixed_time`)은 **시각을 지키고**, 대신 `앞 일정이 길어 N분 늦을 수 있음` 을 단다.
- `day_end` 를 넘긴 항목은 **버린다**. 갈 수 없는 일정을 남기지 않는다.

### 5.5 빈틈 채우기

```
detect_gaps → (Send 팬아웃) nearby_search → rerank_nearby → fill_gaps → END
```

**규칙** — 끼워 넣는 쪽인 `fill_gaps` 가 `start` 가 없는 빈틈을 **건너뛴다.**
예전에 `utcnow()` 로 시각을 때워서 09:00 계획 한가운데에 16:28 항목이 박혔다.
그리고 삽입 뒤에는 반드시 `_reflow()` 를 다시 돌린다 — 이게 빠지면 삽입된 항목의
시각이 앞뒤와 어긋난 채 남아 목록이 시간순이 아니게 된다.

### 5.6 재계산 진입점 (그래프 밖에서도 쓴다)

| 함수 | 부르는 곳 |
|---|---|
| `reroute_itinerary(itinerary, mode, …)` | `POST /reroute` — 오늘의 일정 탭 |
| `route_places(places, mode, …)` | `POST /reroute` — 큐레이션 탭 |
| `measure_routes(itinerary, mode, …)` | `POST /threads/{id}/routes` — 아래 5.7 |
| `summarize_transport(mix)` | 세 곳 모두 — `"도보 32분 · 지하철 18분"` |

앞의 두 함수는 `origin` / `destination` / `origin_name` / `destination_name` 을 채워
돌려준다. 한쪽만 채우면 지도의 🚩·🏁 핀이 짝을 잃는다.

### 5.7 무거운 것은 첫 응답에서 떼어냈다 (2단계)

응답 예산 15초에는 탐색·검증·편성이 다 들어가야 해서 **구간 실측이 들어갈 자리가 없다.**
넣으면 예산이 밀려 `_measure_legs` 가 통째로 잘리고, 이동시간이 전부 추정이 된다.
그러면 지도는 장소를 직선으로 잇는다 — 지하철이 한강을 가로질러 직진하는 그림이 된다.

```
1) POST /chat              15초 안에 일정을 낸다. 이동시간은 거리 기반 추정('(추정)' 표시)
                           → 화면에 일정·지도가 뜬다. 선은 아직 직선이다.
2) POST /threads/{id}/routes   클라이언트가 이어서 부른다(자기 예산 60초)
                           → travel_path 가 채워지고 지도의 선이 실제 노선으로 바뀐다
```

**규칙 — 사용자를 기다리게 하지 않는다.** 2단계는 화면이 이미 그려진 뒤에 돈다.
실패하면 조용히 넘어간다. 선이 직선으로 남을 뿐 일정은 이미 손에 있다.

**규칙 — 실측 뒤에는 `_reflow()` 를 다시 돌린다.** 재기만 하고 시각을 두면
화면의 도착 시각과 구간 시간이 서로 어긋난 채 남는다(5.4 참고).

**규칙 — 확인 카드 화면에서도 채운다.** HITL 확인 화면에도 지도가 함께 뜨는데,
거기서 직선으로 그려진 동선을 보고 '그대로 진행'을 판단하게 하면 안 된다.
클라이언트는 `done` 과 `interrupt` 두 분기 모두에서 부른다.

**규칙 — 늦게 온 응답이 새 일정을 덮지 않게 한다.** 사용자가 그 사이 다시 물었다면
옛 일정의 경로는 버린다(`routesForRef` 로 `itinerary.id` 를 대조).

### 5.8 공식정보 검증도 같은 이유로 뺐다

검증은 한 묶음에 `COST_VERIFY_BATCH=2.5초` 가 들고 응답 예약 `2.5초` 를 남겨야 한다.
그런데 첫 응답 시점에 남은 시간이 3초 안팎이라 **한 묶음도 못 들어간다** — 실제로
`12건 → 0건` 으로 잘렸고, 모든 장소가 `verify_status='unknown'` 인 채 화면에
'확인 필요'로 떴다. 공식정보 검증이라는 기능이 사실상 꺼져 있던 셈이다.

```
POST /threads/{id}/verify     discovery.verify_itinerary()  자기 예산 60초
```

**후보가 아니라 일정의 장소만 본다.** 후보 12개 중 대부분은 일정에 못 들어가므로
검증해도 화면에 나타나지 않는다 — 같은 호출 수로 훨씬 값진 결과가 나온다.
실측 결과: 7곳을 3초에 대조해 `verified 5 · needs_check 1 · excluded 1`.

**규칙 — 검증은 경로 좌표를 건드리지 않는다.** 클라이언트는 `/routes` 응답 위에
`verify_status` 만 얹는다(`seq` 로 대조). 통째로 갈아끼우면 방금 받은 선형이 날아간다.

---

## 6단계 — 무엇이 문제인지 판정한다 (`subgraphs/validation.py`)

```
START → ⟨병렬⟩ check_hours     운영시간 · 휴관일
               check_travel    앞 장소에서 시간 안에 닿는가
               check_overlap   시각이 겹치는가
               check_weather   악천후 시간대의 야외 일정
               check_revisit   재방문이라면 달라진 점
               check_friction  지난번 불편했던 곳인지 (UR-40)
      → triage → build_confirm_cards → END
```

여섯 검사는 각자 `Issue` 만 쌓고 **판단은 하지 않는다.** 검사마다 '이건 사용자에게
물어야 하나'를 따로 정하면 기준이 검사 수만큼 갈린다. 그 판단은 `triage` 한 곳에 모은다.

`triage` 가 이슈를 심각도로 나누고(자동수정 vs 사용자확인), `build_confirm_cards` 가
**사용자가 고를 수 있는 형태**로만 카드를 만든다. 고칠 방법이 없는 이슈는 카드가 되지 않는다.

**규칙** — `auto_fixable=False` **이면서** `severity >= threshold(2)` 인 이슈만 HITL로 올린다.
두 조건은 **AND**다. OR로 읽으면 자동으로 고칠 수 있는 사소한 이슈까지 전부 카드가 되어,
확인 화면이 쌓이고 정작 중요한 경고가 묻힌다.

**규칙 — 카드의 이름은 `Issue.place_name` 에서 온다.** 예전에는 `target_seq` 로 일정에서
이름을 다시 찾았는데, 그 사이 `_reflow` 가 순서를 재배치하면 그 자리의 장소가 바뀐다.
그래서 **'A 확인 필요' 제목 아래 B 이야기가 적힌 카드**가 나왔다. seq 는 흔들려도
검사 시점에 박아 둔 이름은 흔들리지 않는다.

**규칙 — 카드 id 는 `adv-{kind}-{place}` 로 고정한다.** `Issue.id` 는 검사할 때마다
새로 생기므로 거기서 따오면 재계획(`hitl → itinerary → validation`)을 돌 때마다
같은 문제가 새 카드가 된다. 실제로 이슈 6건이 카드 21장이 됐다.

---

## 7단계 — 사람에게 묻는다 (`nodes.human_review`)

```python
g.add_conditional_edges("validation", needs_confirm, ["hitl", "finalize"])
```

**규칙 1 — 카드가 실제로 있을 때만 묻는다.**
`needs_user_confirm` 만 보고 분기하면 "0/0 선택됨" 인 빈 확인 화면이 뜬다.
사용자는 아무것도 할 수 없고 일정도 나오지 않는다.

**규칙 2 — `human_review` 는 `async def` 여야 한다.**
동기 노드는 스레드 풀에서 실행되어 runnable config 컨텍스트를 잃고 `interrupt()` 가
동작하지 않는다.

**규칙 3 — `interrupt()` 페이로드는 JSON 직렬화 가능한 값만 담는다.**
전부 `model_dump(mode="json")` 을 거쳐 넣는다. context7 문서도 체크포인터 간 이식을
위해 단순 직렬화 가능 타입을 권한다.

**규칙 4 — 사람에게 보일 문장과 기계 계약을 분리한다.**

```python
"instruction": _confirm_prompt(...)                          # 사람이 읽는다
"contract":    "decisions[] = [{advisory_id, option_id, note?}]"   # 화면에 안 보인다
```

`각 advisory마다 option_id를 하나씩 선택해 주세요` 가 채팅창에 그대로 뜨던 사고를
이 분리 + 클라이언트의 `JARGON` 필터로 막는다.

재개는 `Command(resume={"decisions":[…]})` 로 정확히 이 지점부터.

---

## 8~10단계 — 반영 · 기록 · 답변 (`nodes.py`)

### 8단계 `finalize`
사용자 선택을 일정에 적용하고 `Evidence(kind="rule", title="사용자 확정")` 을 남긴다.

### 9단계 `persist` — 개인화 순환을 닫는 고리

```
save_itinerary(user_id, itinerary)
save_decisions(user_id, decisions)
_decision_signals(state)              →  확정 카드에서 고른 것 (drop·replace·reorder·수단변경)
extract_edit_signals(before, after)   →  두 일정 버전의 diff
_merge_signals(①, ②)                 →  같은 사건을 두 번 세지 않는다
save_plan_edits(user_id, plan_id, …)  →  plan_edits   신호 하나 = 행 하나
_learn_from_edits(user_id, signals)   →  apply_edit_signals → save_profile
```

**카드 경로를 따로 두는 이유** — 처음 만든 일정에서 카드로 장소를 빼면 diff 는 아무것도
못 본다. 비교 대상(`current_itinerary`)은 **수정 요청일 때만** 불러오기 때문이다.
사용자가 가장 분명하게 «싫다»고 말한 순간이 하필 그때라, 카드 선택을 직접 읽는다.

**규칙** — 마지막 줄을 빼먹으면 `frequent_removals` 가 영원히 비어 있고,
사용자가 몇 번을 지운 장소든 다음 추천에 그대로 다시 올라온다.
아카이브가 *다음 판단의 근거*가 되려면 방문 기록만이 아니라 **거절 기록**도 남아야 한다.

집계(`rebuild_profile`)는 '방문한 뒤'에만 배울 수 있다. 하지만 추천을 지우거나 바꾼
순간에도 배울 것이 있고, 오히려 그쪽이 더 분명한 신호다.

저장 실패는 여기서 삼킨다(`logger.warning` 만 남긴다) — 개인화는 일정 생성의 전제가 아니다.
DB가 죽었다고 이미 만든 일정을 사용자에게 못 보여줄 이유가 없다.

**여기가 캘린더(UR-28)의 쓰기 지점이다.** `save_itinerary()` 는 `plans` 에
`plan_date` 와 `Itinerary` 전체(`payload jsonb`)를 남긴다. 즉 캘린더에 필요한 데이터는
매 요청마다 쌓이고 있고, 2026-08-17 에 읽는 쪽이 붙었다.

```
쓰기   persist → save_itinerary()      →  plans(user_id, plan_date, payload)
읽기   repo.list_plans(user_id, frm, to)   GET /plans/{user_id}?from=&to=   목록(요약만)
       repo.load_plan(plan_id)             GET /plans/detail/{plan_id}      그날 일정 전체
화면   mobile app/(tabs)/calendar.tsx
```

**규칙** — 목록 응답에 `payload` 전문을 실으면 안 된다. `Itinerary` 하나가
수십 KB라 한 달치를 그대로 내리면 모바일 페이로드 제약이 깨진다(§ARCHITECTURE 10.6).
그리고 **캘린더에서는 일정을 고치지 않는다.** 편집 경로가 둘이 되면 여기 9단계의
`extract_edit_signals` 가 한쪽 경로의 수정만 보게 된다.

**`plan_edits` 는 2026-08-17 부터 채워진다(UR-09).** 그전까지는 `edit_signals` 를
계산만 하고 테이블에 남기지 않아 프로세스가 죽으면 사라졌고, 재집계(`rebuild_profile`)가
한 번 돌면 방문 기록만 보고 프로필을 새로 만들어 흔적까지 지웠다.
지금은 `save_plan_edits()` 가 남기고 `rebuild_profile()` 이 다시 읽는다.

### 10단계 `compose`
근거를 함께 서술한 최종 응답. LLM 호출이 실패하면 `_fallback_answer()` 가
일정을 시간순 텍스트로 옮겨 적는다 — 답변 없이 끝내지 않는다.

---

## 11단계 — 화면 (`mobile/`)

### 11.1 상태 한 곳 (`src/hooks/useCultureMate.ts`)

| 값 | 뜻 |
|---|---|
| `resolved` | 서버가 해석한 조건. **모든 칩의 원천** |
| `restored` | 복원된 이전 일정인가 (새 결과와 섞이면 안 된다) |
| `dismissConfirm()` | 확인 카드를 접고 입력을 되돌려 준다 |
| `confirmText()` | `JARGON` 정규식으로 내부 용어를 걸러낸 문구 |
| `fillRoutes()` | 일정을 띄운 뒤 실제 경로 좌표를 받아 지도만 갱신 (5.7) |

**규칙** — `resolved` 는 **세 경로 모두**에서 전달돼야 한다:
웹 SSE · 네이티브 SSE · sync 폴백. 한 곳만 빠져도 "대화접기가 갱신되지 않는" 증상이 된다.

### 11.2 공통 컴포넌트 — 같은 장소는 어디서나 같게

| 컴포넌트 | 쓰는 화면 |
|---|---|
| `PlaceFacts` | 오늘의 일정 · 큐레이션 (주차·실내·종류·도착수단 칩) |
| `TransportPicker` | 두 탭 모두 — `DEFAULT_TRANSPORT = 'best'` |
| `RoutePoints` | 두 탭 모두 — 출발/시각/도착/시각 |
| `NaverMap` | 두 탭 모두 — 🚩 시작 · 🏁 끝 · 구간 라벨 · **수단별 선 스타일** |
| `SaveToCollection` | 오늘의 일정 → 즐겨찾기 |

화면마다 따로 만들면 한쪽만 주차를 빼먹거나 실내 판정 기준이 갈린다.
사용자 입장에서는 같은 장소가 화면마다 다르게 보이는 셈이라 신뢰를 잃는다.

**규칙** — 장소 종류 라벨(`KIND_LABEL`)은 `src/constants.ts` **한 곳**에만 둔다.
예전에 `constants.ts` 와 `PlaceFacts.tsx` 에 같은 표가 두 벌 있었다.

**규칙 — `NaverMap.tsx` 에는 지도를 그리는 코드가 두 벌 있다.** 고칠 때 둘 다 고친다.

| 함수 | 쓰는 곳 |
|---|---|
| `drawMap()` | 웹 — 네이버 JS SDK 를 직접 호출 |
| `buildHtml()` | 네이티브 WebView · iframe — 같은 로직을 HTML 문자열로 |

한쪽만 고치면 웹에서는 되는데 폰에서는 안 되거나 그 반대가 된다. 실제로 구간별
폴리라인을 넣을 때 `buildHtml` 만 고쳐서 웹이 `LEG_STYLE is not defined` 로 죽었다.

구간 선 스타일(`LEG_STYLE`)은 수단을 선 모양으로 구분한다 — 도보는 점선, 지하철은
굵은 실선, **실측 좌표가 없는 구간은 옅은 점선**이다. 직선인데 굵은 실선으로 그리면
지도가 없는 정보를 있는 것처럼 말하게 된다.

### 11.3 이동수단 선택지 (순서 고정)

```
✨ 최단루트(기본) → 🚗 자가용 → 🚶 도보 → 🚈 지하철 → 🚌 버스
```

`지하철+버스` 키워드는 전 소스에서 제거됐다. 항상 하나는 켜져 있어야
지도의 숫자가 무엇을 뜻하는지 분명해진다 — 같은 걸 다시 눌러도 해제되지 않는다.

### 11.4 캘린더 탭 (UR-28) — ✅ `app/(tabs)/calendar.tsx`

탭은 다섯이다: `index`(오늘의 일정) · **`calendar`** · `archive` · `curation` · `report`.
컴포넌트를 새로 만들지 않고 기존 것을 그대로 쓴다.

| 화면 요소 | 재사용할 것 |
|---|---|
| 월/주 그리드 | 신규 (`app/(tabs)/calendar.tsx`) |
| 날짜 탭 → 그날 일정 | `Timeline` · `NaverMap` · `PlaceFacts` · `EvidenceSheet` **그대로** |
| 기록 없는 지난 일정 → 기록 남기기 | 기존 `app/visit.tsx` 로 이동 |
| 데이터 | `fetchPlans()` → `GET /plans/{user_id}` · `fetchPlan(id)` → `GET /plans/detail/{id}` |

**규칙 두 가지.**

1. **캘린더는 읽기 전용이다.** 일정 변경은 «오늘의 일정» 탭의 재계획 경로 하나로 모은다.
   두 경로에서 고치면 9단계 `extract_edit_signals` 가 한쪽 수정만 본다.
2. **`useCultureMate` 상태를 건드리지 않는다.** 그 훅은 «진행 중인 한 건»의 상태 기계
   (`idle → running → awaiting_confirm → done`)다. 과거 목록을 같은 훅에 넣으면
   `restored` 와 새 결과가 섞여, 11.1이 경고하는 그 증상이 그대로 재현된다.

---

## 12. 외부 의존 — 무엇을 어디서 가져오는가

| 용도 | 제공자 | 파일 | 비고 |
|---|---|---|---|
| 지오코딩(주소) | NCP Geocoding | `tools/maps.py` `geocode()` | 1순위. `addressElements` 로 시·도까지 받는다(UR-18) |
| 지오코딩(POI) | NAVER 지역검색 | `tools/maps.py` `place_lookup()` | 주소로 안 잡히면 |
| 자동차 경로 | NAVER Directions 5/15 | `tools/maps.py` | **자동차 전용** |
| 도보 경로 | OpenRouteService | `tools/routing.py` | `foot-walking`, Matrix 로 N×N 1콜 |
| 대중교통 | ODsay LAB | `tools/routing.py` | `SearchPathType` 1=지하철 2=버스 |
| 노선 선형 | ODsay `loadLane` | `routing._lane_path()` | 경로검색의 `mapObj` → 좌표열 |
| 행사 | 공공 문화데이터 | `tools/culture_api.py` | 엔드포인트 미설정이면 웹검색으로 대체 |
| 문화시설 | 문화시설조회서비스 | `tools/culture_api.py` | 미설정이면 네이버 지역검색만 |
| 날씨 | 기상청 API허브 | `tools/weather.py` | 컨테이너는 UTC, 발표는 KST |
| 웹검색 | Tavily → Exa | `tools/websearch.py` | 앞이 0건이면 뒤로 넘어간다 |
| LLM · 임베딩 · 리랭크 | NVIDIA NIM | `llm/provider.py` | 임베딩은 백엔드를 바꿔도 NIM 유지 |

**규칙 — 날씨 제공자는 두 갈래다.** `KMA_API_HUB_KEY` 가 있으면 기상청 API허브를,
없으면 공공데이터포털을 쓴다(`config.weather_source`). 둘은 **엔드포인트와 인증
파라미터가 다르다**(API허브는 `authKey`). 키만 바꿔 끼우면 400이 돌아온다.

**규칙 — 임베딩 차원은 스키마와 묶여 있다.** `EMBED_DIM=1024` 는
`db/001_schema.sql` 의 `vector(1024)` 와 짝이다. 모델을 바꾸려면 스키마와 기존
임베딩을 함께 재생성해야 하므로, LLM 백엔드를 교체해도 임베딩만은 NIM 무료를 유지한다.

**경로 API 3종(NAVER · ORS · ODsay)은 전부 무료 티어다.** 유료 제공자를 섞으면
배포에 고정비가 생기고, 키가 없는 환경에서는 그 구간이 통째로 비어 일정이 성립하지 않는다.
`tests/test_tools.py::test_routing_uses_only_free_providers` 가 `app` · `scripts` · `mobile` ·
설정 파일까지 훑어 이 전제를 지킨다.

측정 불가 시 `tools/maps.py` 의 추정으로 내려간다:

```python
FALLBACK_SPEED = {"walk": 4.5, "bike": 14.0, "bus": 14.0, "subway": 22.0, "car": 22.0}  # km/h
FIXED_OVERHEAD = {"bus": 8, "subway": 7}   # 대기·환승
DETOUR_FACTOR  = 1.35                      # 직선거리 → 실제거리
```

**규칙 — 경로 좌표는 세 제공자가 수단별로 나눠 준다.** 하나가 다 주지 않는다.

```
도보·자전거   ORS      GeoJSON geometry.coordinates
자동차        NAVER    routes[0].path
지하철·버스   ODsay    경로검색 → info.mapObj → loadLane → lane[].section[].graphPos[]
```

ODsay 만 **호출이 두 번**이다(경로검색 + loadLane). 무료 1,000건/일이므로
구간당 2회씩 나가는 셈이라, 확정된 일정에만 부르고 `ttl=86400` 을 건다 —
노선 선형은 하루에 바뀌지 않는다.

구간당 좌표가 수백 개라 `_thin_path()` 로 120점까지 솎아낸다. **양 끝점은 반드시
남긴다** — 잘리면 선이 장소에서 떨어진 채 시작해 다른 길을 그린 것처럼 보인다.

**규칙** — 공용 `httpx.AsyncClient`(`tools/http.py`)는 **만든 이벤트 루프에 묶어 둔다.**
커넥션 풀이 그 루프에 매여 있어서, 루프가 바뀐 뒤 같은 객체를 재사용하면
`RuntimeError: Event loop is closed` 로 터진다. `is_closed` 는 클라이언트의 상태만 볼 뿐
루프의 생사는 모르므로 그것만으로는 막지 못한다.

운영에서는 uvicorn 루프가 하나뿐이라 드러나지 않는다. 드러난 곳은 테스트였다 —
`asyncio_mode = "auto"` 는 함수마다 새 루프를 주므로, 앞선 테스트가 연 클라이언트가
뒤 테스트를 깨뜨렸다. 종료 시 `close_client()` 는 `lifespan` 에서 부른다.

---

## 13. 예산 — 시간이 아니라 비용으로 줄인다 (`app/graph/budget.py`)

타임아웃은 "느려서 잘렸다" 는 결과만 남기고 무엇을 포기했는지 모른다.
그래서 단계마다 비용을 매기고 `Budget.allows()` 로 **미리** 판단한다.

```python
COST_VERIFY_BATCH  = 2.5
COST_TRAVEL_MATRIX = 3.0
COST_LEG_MEASURE   = 0.4
COST_COMPOSE       = 2.5   # 예약분 — 이건 포기할 수 없다
```

`reserve=COST_COMPOSE` 가 기본값이라, 무엇을 건너뛰든 **답변 생성 몫은 항상 남는다.**
건너뛴 단계는 `log_skip(stage, budget, reason)` 으로 흔적을 남긴다.

---

## 14. 직렬화 (`app/graph/serde.py`)

`JsonPlusSerializer(allowed_msgpack_modules=ALLOWED_TYPES)` — 우리 도메인 타입만 허용한다.
기본값은 '전부 허용 + 경고' 이고 향후 차단 예정이며, 체크포인트 DB 쓰기 권한을 얻은
공격자가 역직렬화로 코드 실행을 노리는 경로이기도 하다.

새 Pydantic 모델을 State 에 넣으면 **`ALLOWED_TYPES` 에 추가해야 한다.**

### 14.1 시각은 두 종류다 — 섞으면 안 된다

| | 무엇 | 어떻게 | 어디서 |
|---|---|---|---|
| **기록 시각** | 언제 일어났는가 | tz 붙은 UTC — `schemas.utc_now()` | `observed_at` `occurred_at` `updated_at` `checked_at` `decided_at` |
| **벽시계 시각** | 몇 시에 거기 있는가 | tz 없는 지역 시각 | 일정의 `arrive` `depart`, `Gap.start/end` |

DB의 시각 컬럼은 **전부 `timestamptz`** 라 읽어오면 aware 로 돌아온다. 쓸 때만 naive 를
넣으면 같은 필드에 두 종류가 섞여 비교가 터진다. 그래서 기록 시각은 한 함수로 모았다.

- `datetime.utcnow()` 는 쓰지 않는다 — UTC 값을 tz 없이 돌려주는 데다 폐기 예정이다.
- 오늘 날짜가 필요하면 `weather.today_kst()` 다. **`utcnow().date()` 를 쓰면 안 된다** —
  컨테이너는 UTC로 돌기 때문에 자정~오전 9시 사이에 어제 날짜로 일정을 짠다.
- 일정의 `arrive`/`depart` 에 tz 를 붙이면 안 된다. 이건 '오후 3시에 거기 있다'는
  약속이지 UTC 어느 순간이 아니다.

---

## 15. 디렉터리

```
app/
  api/main.py            HTTP · SSE · 칩 재료(_resolved)
  graph/
    build.py             그래프 조립 (여기서 전체 흐름을 읽는다)
    router/              2단계 — 요청 이해 + 좌표 확정
      __init__.py          라우팅 표 · classify · fan_out
      detect.py            지역·지점·개수·종류별 몫 탐지   ★ 295줄
      endpoints.py         출발·도착 절 분리              ★ 248줄
      rules.py             규칙 파서 순서 · LLM 결과 병합
      timeparse.py         시각·체류시간 표현
      enrich.py            클라이언트 값 · 취향 · 좌표
    nodes.py             4·7·8·9·10단계
    state.py             State 스키마 + 서브그래프 입출력 스키마
    reducers.py          병합 규칙 (싱글턴 주의)
    budget.py            비용 기반 축소
    serde.py             체크포인트 직렬화 화이트리스트
    subgraphs/
      archive.py         아카이브 검색 (facet 팬아웃)
      discovery.py       후보 탐색 + 검증
      itinerary/         일정 편성
        __init__.py        서브그래프 조립 · 공개 이름
        schedule.py        순서·시각 배치                ★ 264줄
        placement.py       하루 경계 · 배치 판단 · 쿼터   ★ 236줄
        legs.py            구간 이동 측정 · 최단루트
        gaps.py            공백 탐지 → 주변 추천 → 채우기
        routes.py          그래프 밖 재계산(API 전용)
        context.py         병렬 컨텍스트 · 제약 구성
        dwell.py           체류시간
        notes.py           출발·도착 안내 문구
      validation.py      이슈 판정 + 확인 카드
                         (report.py 는 삭제됨 — 서브그래프는 4개다)
  memory/
    retriever.py         벡터 검색
    writer.py            경험 기록 · extract_edit_signals
    profile.py           취향 집계 · apply_edit_signals · save_profile
    curation.py          큐레이션 · 컬렉션
  tools/                 외부 API (12장 표) · region.py(시·도 판정, UR-18)
  db/                    repo.py(질의) · session.py(풀)
  llm/                   provider.py · prompts.py

mobile/
  app/(tabs)/            index(오늘의 일정) · calendar(UR-28) · curation · archive · report
  app/visit.tsx          관람 기록 추가 (모달)
  app/connect.tsx        서버 연결 — 주소 확인·저장·목 모드 전환 (모달)
  src/
    hooks/useCultureMate.ts    상태 한 곳
    components/                공통 컴포넌트 (11.2)
    api/client.ts + mock.ts    SSE · sync · 목 어댑터 · probeServer
    config.ts                  apiUrl() · isMock() — 런타임에 바뀐다(연결 화면)
    store/storage.ts           오프라인 캐시 + 저장된 서버 주소
    constants.ts               KIND_LABEL · FRICTION_LABEL (단일 원천)

db/001_schema.sql        사용자 · 장소 · 방문 · 경험임베딩 · 취향집계 · 컬렉션
docs/                    ARCHITECTURE(왜) · STRUCTURE(어떻게) · REQUIREMENTS(무엇을)
                         FUNCTIONAL_MAP(어디에) · PLANNING(기획안 대조) · SETUP · PROGRESS · TEST
tests/                   194개 (193 passed · 1 skipped)
  test_docs_contract.py  문서 ↔ 소스 정합성을 고정 (노드·라우트·라우팅 표·HITL 조건)
```

`tests/` 는 런타임 이미지에 들어가지 않는다(`Dockerfile` 은 `app`·`db`·`scripts` 만 복사).
컨테이너에서 돌리려면 붙여 준다:

```
docker compose run --rm --no-deps \
  -v "$PWD/tests:/srv/tests" -v "$PWD/pyproject.toml:/srv/pyproject.toml" \
  api python -m pytest -q
```

---

## 16. 이번 정리에서 걷어낸 것

| 대상 | 처리 |
|---|---|
| `mobile/src/api/index.ts` | 삭제 (빈 재export) |
| `mobile/src/components/OriginPicker.tsx` | 삭제 → 타입만 `RoutePoints.types.ts` 로 |
| `reducers._higher_score`, `reducers.take_last` | 삭제 (호출부 없음) |
| `router._detect_region` | 삭제 (`_detect_regions` 로 대체됨) |
| `types.ts` `RequestType`·`VerifyStatus`·`ChatRequest` | 삭제 (서버 계약만 베낀 미사용 타입) |
| `ui.tsx` `Divider`, `mock.ts` `MOCK_SEEDS` | 삭제 |
| `constants.CATEGORY_LABEL` ↔ `PlaceFacts.KIND_LABEL` | 중복 → `constants.KIND_LABEL` 하나로 |
| `db/repo._jsonb` | `jsonb` 로 공개 (모듈 두 곳에서 쓴다) |
| `profile.apply_edit_signals` | **삭제 대신 연결** — 9단계 참고 |
| 미사용 import · `noqa` 31건 | ruff 로 제거 |

주석 처리된 죽은 코드는 전 소스에서 0건이다.
남은 `#` 은 전부 *왜 이렇게 했는지*를 적은 설명이며, 대부분 한 번씩 깨져 본 자리에 붙어 있다.
