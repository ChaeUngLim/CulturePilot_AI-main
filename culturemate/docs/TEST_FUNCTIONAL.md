# 테스트 결과 보고서 — 전 구간 기능 검증

> 웹(`http://localhost:19006`) + 백엔드(`http://localhost:8000`) 기준 전 구간 검증.
> 모바일(Expo Go)은 앱 버전 비호환으로 **이번 범위에서 제외**했다(§5 D-05).
>
> **검증 보고서는 두 편이다 — 범위가 다르므로 결함 번호(D-**)도 서로 독립이다.**
>
> | 문서 | 무엇을 봤나 |
> |---|---|
> | **TEST_FUNCTIONAL.md** (이 문서) | 자동 테스트 · 외부 API 11종 · 조건 해석 · HITL 중단/재개 · 웹 화면 |
> | [TEST.md](TEST.md) | **지역을 바꿔 가며** 같은 요청을 넣었을 때 그 지역 장소가 나오는가 |
>
> 이 문서가 한동안 `TEST - 복사본.md` 라는 이름으로 있었다(2026-08-16 정정).
> 사본이 아니라 **범위가 다른 별도 회차**다 — 지우면 D-01(조건 칩 미갱신)·D-04(웹검색 429)
> 처럼 여기서만 확인된 결함 기록이 사라진다.
>
> 관련 문서 — 기획 의도는 [PLANNING.md](PLANNING.md), 기능별 소스 위치는
> [FUNCTIONAL_MAP.md](FUNCTIONAL_MAP.md), 현재 상태는 [PROGRESS.md](PROGRESS.md),
> 요구사항은 [REQUIREMENTS.md](REQUIREMENTS.md).

**실행일** 2026-08-13 · **환경** Windows 11 · Docker 컨테이너 1개(PostgreSQL 18.4 + FastAPI) ·
Expo SDK 57 웹 빌드

---

## 1. 요약

| 항목 | 결과 |
|---|:--:|
| 자동 테스트 (pytest) | ✅ **127 passed, 1 skipped** <br/>_(2026-08-13 실행 로그 기준(§2) · 현재 194개 → [PROGRESS.md](PROGRESS.md))_ |
| 정적 검사 (ruff) | ✅ **All checks passed** |
| 외부 API 연동 | ✅ **11 / 11** (§3) |
| 기능 시나리오 | ✅ **7 / 7 응답** |
| HITL 중단·재개 | ✅ 정상 |
| 2단계 경로 실측 | ✅ 정상 (구간 좌표 최대 431점) |
| 웹 콘솔 에러 | ✅ **0건** |
| **발견된 결함** | ⚠️ **4건** — 1건 해결, 3건 잔여 (§5) |

**한 줄 결론 —** 핵심 기능은 전부 동작한다. 다만 **응답 시간이 목표(15초)를 넘는
회차가 있고**, 확인 카드가 뜨는 경로에서 화면 조건 칩이 갱신되지 않는 결함이 있다.

---

## 2. 자동 테스트

```
$ ruff check app scripts
All checks passed!

$ pytest -q
127 passed, 1 skipped in 123.68s      # 2026-08-13 실행 당시. 현재는 194개
```

`1 skipped` 는 interrupt 왕복 테스트로, 해당 입력에서 확인 카드가 생기지 않은
회차라 건너뛴다. 환경 문제가 아니다.

테스트에는 **문서-소스 정합성 검사 14개**가 포함된다(`tests/test_docs_contract.py`).
그래프 노드 수·검증 종류·엔드포인트 수·라우팅 표·HITL 조건·주요 파일 줄 수를
코드와 대조하므로, 구조를 바꾸면 이 검사가 먼저 깨진다.

---

## 3. 외부 API 연동 (11/11)

`GET /diagnostics?probe=true` — 각 API를 실제로 한 번씩 호출한 결과.

| API | 용도 | 결과 |
|---|---|:--:|
| `naver_geocode` | 주소 → 좌표 | ✅ |
| `naver_directions` | 자동차 경로 | ✅ |
| `kakao_local` | 주변 장소 반경 검색(1순위) | ✅ |
| `naver_local_search` | 주변 장소 — 카카오 실패 시 폴백 | ✅ (`served_by=kakao`) |
| `weather` | 기상청 단기예보 | ✅ |
| `culture_api` | KCISA 기간형 행사 | ✅ |
| `culture_facility` | 공공데이터포털 문화시설 | ✅ |
| `websearch` | Tavily → Exa | ✅ |
| `ors` | 도보·자전거 경로 | ✅ |
| `odsay` | 지하철·버스 경로 | ✅ |
| `llm` | 채팅 모델 | ✅ |

---

## 4. 기능 시나리오 (7건)

각 시나리오는 `POST /chat/sync` → `POST /threads/{id}/routes` 순으로 실행했다.

| ID | 시나리오 | 시간 | 상태 | 장소 | 근거 | 검증됨 | 경로좌표 |
|---|---|---:|---|---:|---:|---:|---:|
| T-01 | 출발·도착·시각 명시 | 9.9s | interrupted | 8 | 0 | 6 | 431 |
| T-02 | 지역만 지정 | 16.2s | done | 2 | 21 | 2 | 158 |
| T-03 | 이동수단 명시(도보) | 12.7s | done | 3 | 19 | 3 | 31 |
| T-04 | 체류시간 명시 | 13.0s | done | 5 | 20 | 5 | 197 |
| T-05 | 날씨 조건(실내) | 11.5s | done | 3 | 20 | 3 | 77 |
| T-06 | 조기 종료(빈틈 채우기) | 6.4s | done | 4 | 20 | 4 | 84 |
| T-07 | 과거 기록 질의 | 17.6s | done | 0 | 12 | 0 | 0 |

### 4.1 조건 해석 검증

| ID | 요청한 조건 | 해석 결과 | 판정 |
|---|---|---|:--:|
| T-03 | "도보로만" | `transport = walk` | ✅ |
| T-04 | "장소마다 1시간씩" | 전 항목 `dwell = 60분` | ✅ |
| T-04 | "오후 2시부터" | `start_time = 14:00` | ✅ |
| T-06 | "2시간 남는데" | `start_time = 14:00`, 4곳 배치 | ✅ |
| T-05 | "비 오는 날 실내" | 국립현대미술관·세화미술관 등 실내만 | ✅ |
| T-01 | "판교역 7시 출발 / 청계산역 21시 도착" | **응답에 미포함** | ⚠️ D-01 |

**체류시간 개인화 확인** — T-01은 조건에 체류시간이 없어 과거 방문 평균(78분)으로
보정됐다: `80·65·80·80·145·115·115·49분`. 장소별 상대 차이가 보존됐다.

### 4.2 HITL 중단·재개

```
1) 최초 요청   10.1s  status = interrupted   확인 카드 1장
   카드: "주식회사현대백화점판교점 확인 필요"
   첫 선택지: "그대로 진행"          ← 규칙 준수 확인
2) 선택 반영    0.1s  status = done   장소 8곳   답변 60자
```

- `interrupt()` 로 그래프가 실제로 정지하고, `Command(resume=...)` 로 정확히 그
  지점부터 재개된다.
- **제목과 내용의 장소가 어긋난 카드 0건** — 7개 시나리오 전체에서 확인.
- 첫 선택지가 항상 "그대로 진행"이다.

### 4.3 웹 화면

```
POST /chat                        200
POST /threads/{id}/routes         200
POST /threads/{id}/verify         200
브라우저 콘솔 에러                 0건
```

노드별 소요: `classify 0.0s · archive 3.2s · discovery 0.7s · itinerary 0.3s ·
validation 0.0s` — `classify 0.0s` 는 규칙 파서가 조건을 모두 잡아 LLM 호출을
건너뛴 경우다.

---

## 5. 발견된 결함

| ID | 심각도 | 내용 | 위치 |
|---|:--:|---|---|
| ~~D-01~~ | ~~중~~ | ~~확인 카드가 뜨면 `resolved`가 응답에 없다~~ | ✅ **해결** (2026-08-13) |
| **D-02** | 중 | 응답 시간이 목표 15초를 넘는 회차가 있다 (최대 17.6초) | 외부 API 지연 |
| **D-03** | 하 | 지역만 지정하면 장소가 2곳까지 줄어든다 | `discovery` 후보 부족 |
| **D-04** | 하 | 웹검색 제공자(Exa)가 초당 10회 제한에 걸린다 | `tools/websearch.py` |
| **D-05** | — | 모바일 Expo Go 가 SDK 57 미지원 | 앱 버전 |

### D-01 — 확인 카드 경로에서 조건 칩이 갱신되지 않는다

```python
# app/api/main.py:286  — interrupted 분기
return {"status": "interrupted", "interrupt": ..., "timing": ...}
#      ↑ resolved 키가 없다.  done 분기(293행)와 SSE done(117행)에는 있다.
```

**증상** — "판교역에서 7시 출발"이라고 말했는데 확인 카드가 뜨면 화면 상단 칩이
이전 값 그대로 남는다. 사용자는 자기가 말한 조건이 반영됐는지 알 수 없다.

**재현** — T-01. `resolved` 의 `origin_name`·`start_time` 등이 전부 `None`.

**영향** — 일정 자체는 정상이다. 화면 표시만 어긋난다.

**✅ 해결 (2026-08-13).** `resolved` 계산을 `schemas.resolved_view()` 로 옮기고
`nodes.human_review` 가 **interrupt 페이로드에 직접 실어** 보내게 했다. 응답 분기마다
따로 붙이면 또 빠뜨리므로, 페이로드가 값을 들고 다니게 해 SSE·sync·resume 세 경로가
한 번에 해결된다. 클라이언트도 `InterruptPayload.resolved` 를 읽어 칩을 갱신한다.

```
검증: status = interrupted  카드 1장
      interrupt.resolved  origin_name=판교역  destination_name=청계산역
                          start_time=07:00   end_time=21:00
```

회귀 테스트 `test_interrupt_payload_carries_resolved_conditions` 추가.

### D-02 — 응답 시간 편차

```
7건 중  15초 초과 2건 (T-02 16.2s · T-07 17.6s)
        10초 미만 2건 (T-01 9.9s · T-06 6.4s)
```

내부 처리는 빠르다(`classify 0.0s · itinerary 0.3s`). 편차는 **외부 API 응답
대기**에서 나온다 — 특히 `discovery` 가 문화 API·웹검색을 기다리는 구간.

예산제(`Budget`)가 단계를 축소해 답변은 항상 나오지만, 목표 15초를 지키지 못하는
회차가 있다.

### D-03 — 지역만 지정하면 후보가 부족하다

T-02("성수동")에서 최종 2곳만 배치됐다. 원인 후보 두 가지:

1. 내장 카탈로그 2,092곳 중 **실제 장소는 92곳**이고 나머지는 생성된 더미라,
   특정 동네에서 겹치는 실데이터가 적다.
2. 공공 API가 소규모 공간을 담지 않아 좌표 게이트에서 걸러진다.

### D-04 — 웹검색 429

연속 호출 시 **2차 제공자** Exa 가 `429 rate limit (10 req/s)` 를 반환한다(우선순위는
Tavily → Exa — §3 표와 `tools/websearch.py` 순서). 1순위 Tavily 가 정상이면 영향이
없지만, Tavily 가 0건이라 Exa 로 넘어간 요청은 웹 근거 없이 진행돼 근거 품질이
떨어진다. 자동 테스트를 연속으로 돌릴 때 재현된다.

### D-05 — 모바일 Expo Go 비호환

```
ERROR  Project is incompatible with this version of Expo Go
This project requires a newer version of Expo Go.
```

프로젝트가 `expo 57.0.11 / react-native 0.86.2` 로 최신이라, Play 스토어의
Expo Go 가 아직 SDK 57을 지원하지 않는다.

**네트워크·방화벽·포트는 모두 해결된 상태다** — 폰이 PC에 도달했기 때문에 이
에러를 받은 것이다. 순수하게 앱 버전 문제다.

> 검증 과정에서 함께 확인한 것: Expo 기본 포트 8081이 Windows 예약 구간
> (8075~8174)이라 자동으로 다른 포트(8175)로 넘어간다. 정상 동작이다.

---

## 6. 미구현 기능

문서에 정의돼 있으나 코드에 없는 항목. **심사에서 가장 먼저 드러나는 부분이라
숨기지 않고 명시한다.**

| ID | 기능 | 상태 | 실제 상황 |
|---|---|:--:|---|
| **UR-01** | 개인 취향 등록(카드) | ✅ | `POST /preferences/cards` → `profile.apply_preference_cards` → `taste-cards.tsx` (2026-08-17) |
| **UR-09** | 일정 직접 수정 | ✅ | 재계획·수단 변경·선택 반영에 더해 **확정 카드 선택과 일정 diff 를 `plan_edits` 에 기록**하고 `rebuild_profile()` 이 되읽는다 (2026-08-17) |
| **FR-25** | 카드 스와이프 온보딩 | ✅ | UR-01과 같은 사안 |
| **FR-26** | 영화 상영 시간표 | ⬜ | 상영 시간표 API가 유료라 보류. 영화관은 `venue` 로만 취급 |

### 왜 이것이 중요한가

`REQUIREMENTS.md §0` 은 일반 추천 서비스와의 차별점을 이렇게 정의한다:

> 개인화 근거 = **방문 기록 + 일정 수정 행동**(거절 기록 포함)

**그 절반(수정 행동)이 저장되지 않았다 — 2026-08-17 에 이었다.**
`extract_edit_signals()` / `apply_edit_signals()` 는 있었지만 입력이 영속화되지 않아
세션을 넘기면 사라졌고, 재집계는 방문 기록만 봤기 때문에 방문이 하나 들어오는 순간
프로필에 남아 있던 반영분까지 지워졌다. 지금은 `persist → repo.save_plan_edits()` 가
남기고 `profile.rebuild_profile()` 이 되읽는다. 회귀 테스트는 `tests/test_edits.py`.

UR-01(콜드 스타트)은 같은 종류의 결함이었다 — 아카이브가 빈 신규 사용자에서
`personal_score` 가 0.5 로 고정돼, 일정은 나오지만 개인화가 성립하지 않았다.
2026-08-17 에 취향 카드로 닫았다. 회귀 테스트는 `tests/test_preferences.py`.

---

## 7. 추가 제안 기능 (신규 UR)

이번 검증에서 드러난 결함과 사용 흐름을 근거로 제안한다. 번호는 기존 UR-17
다음부터 이어 붙였다.

| ID | 기능명 | 근거 | 우선순위 |
|---|---|---|:--:|
| ~~UR-18~~ | ~~조건 해석 결과 상시 노출~~ | ✅ **D-01 수정으로 충족** | — |
| **UR-19** | 응답 시간 가시화 | D-02. 무엇을 기다리는지("문화 콘텐츠 탐색 중…") 단계별로 보여 체감 지연을 줄인다 | 높음 |
| **UR-20** | 장소 데이터 자동 보강 | D-03. 검증된 실제 장소를 카탈로그에 누적해 지역 커버리지를 늘린다 | 높음 |
| **UR-21** | 일정 저장·공유 링크 | 만든 일정을 나중에 다시 열거나 동행자에게 보낼 수단이 없다 | 중간 |
| **UR-22** | 실행 후 피드백 수집 | "실제로 갔나요?" 한 번의 확인으로 아카이브를 채운다. UR-10의 입력 경로가 현재 수동뿐이다 | 중간 |
| **UR-23** | 예산(비용) 조건 | 입장료·식비 상한을 조건으로 받고 합계를 표시한다. `travel_fare` 는 이미 있다 | 중간 |
| **UR-24** | 동행자 유형별 일정 | 아이 동반·부모님·데이트에 따라 체류시간·장소 종류가 달라져야 한다 | 중간 |
| **UR-25** | 오프라인 열람 | 현장에서 네트워크가 끊겨도 확정 일정과 지도를 볼 수 있어야 한다 | 낮음 |
| **UR-26** | 대안 일정 비교 | 2~3개 안을 나란히 보여주고 고르게 한다 | 낮음 |

### 우선순위 근거

**UR-18·UR-19가 높은 이유** — 둘 다 **신뢰**의 문제다. 조건이 반영됐는지 모르면
사용자는 결과를 믿지 못하고, 15초를 아무 표시 없이 기다리면 고장으로 오해한다.
구현 비용도 작다(UR-18은 응답에 키 하나 추가).

**UR-20이 높은 이유** — D-03의 근본 원인이자 UR-01·UR-09의 전제이기도 하다.
장소 데이터가 얇으면 개인화를 고쳐도 붙일 대상이 없다.

**UR-22를 중간에 둔 이유** — 아카이브 기반 개인화가 이 프로젝트의 차별점인데,
지금은 사용자가 직접 방문 기록을 남겨야만 데이터가 쌓인다. 실제로는 아무도 안
남긴다. UR-09(`plan_edits`)가 닫히면서 **일정을 고치는 것만으로도 신호가 쌓이게** 됐지만,
방문 기록 자체를 유도하는 문제는 그대로다.

---

## 8. 개선 방향

### 8.1 즉시 (구현 1시간 이내)

| 대상 | 조치 |
|---|---|
| D-04 | 웹검색 호출에 간격 제어(초당 10회 이하) 또는 재시도 백오프 추가 |

### 8.2 단기 (반나절)

| 대상 | 조치 |
|---|---|
| ~~UR-09~~ | ✅ 2026-08-17 — `persist` 가 확정 카드 선택(삭제·교체·순서·수단변경)과 일정 diff 를 `plan_edits` 에 남기고 `rebuild_profile()` 이 읽는다 |
| UR-19 | SSE `update` 이벤트에 이미 `elapsed_s` 가 실려 있다. 화면에서 단계명을 사람 말로 바꿔 표시하면 된다 |
| D-02 | `discovery` 의 외부 호출에 예산 기반 조기 종료를 더 공격적으로 적용. 현재는 `safe_call(deadline=)` 로 상한만 건다 |

### 8.3 중기 (수일)

| 대상 | 조치 |
|---|---|
| ~~UR-01~~ | **완료 2026-08-17** — `POST /preferences/cards` + `taste-cards.tsx`. `rebuild_profile()` 이 `_CARDS_SQL` 로 되읽는다 |
| UR-20 | `verify_status='verified'` 인 후보를 `places` 에 upsert. 이미 `link_place_ids()` 가 매칭 로직을 갖고 있다 |
| D-03 | `scripts/generate_catalog.py` 가 실제 공공 데이터를 더 받아오도록 확장 |
| D-05 | 개발 빌드(`eas build --profile development`) 또는 SDK 다운그레이드 결정 |

### 8.4 구조 개선 (선택)

- **DB 분리** — 현재 컨테이너 하나에 PostgreSQL + API가 있다. 데모용 선택이며
  postgres가 죽으면 컨테이너 전체가 내려간다. `docker-compose.yml` 을 남겨 뒀으므로
  되돌리기는 쉽다.
- **노출된 키 재발급** — 개발 중 채팅에 붙여넣은 키가 있다
  (data.go.kr · OpenAI · KCISA · NVIDIA).

---

## 9. 재현 방법

```bat
:: 백엔드
cd /d C:\Users\31\Documents\CulturePilot_AI-main\culturemate
백엔드실행.bat

:: 자동 테스트
docker exec culturemate ruff check app scripts
docker run --rm -v "%CD%\tests:/srv/tests" -v "%CD%\docs:/srv/docs" ^
  -v "%CD%\app:/srv/app" -v "%CD%\scripts:/srv/scripts" ^
  -v "%CD%\pyproject.toml:/srv/pyproject.toml" -w /srv ^
  --entrypoint pytest culturemate-api -q

:: 외부 API
curl "http://localhost:8000/diagnostics?probe=true"

:: 웹 앱
cd mobile
npm start
```

시드 데이터가 없으면 개인화가 동작하지 않는다:

```bat
docker exec culturemate python scripts/seed_demo.py
```
