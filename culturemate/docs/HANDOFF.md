# 세션 인계 (HANDOFF)

> **다른 로그인·다른 기기에서 이 프로젝트를 이어받을 때 먼저 읽는 문서.**
> 지금 무엇이 되고 있고, 빈 환경에서 어떻게 되살리며, 다음에 무엇부터 손대면 되는지만 적는다.
> 왜 그렇게 설계했는지는 [ARCHITECTURE.md](ARCHITECTURE.md), 지금 상태의 전량은 [PROGRESS.md](PROGRESS.md).

최종 갱신: **2026-08-18** (문서 정합성 정리 · 설계서 3종 신규 작성)

---

## 1. 30초 요약

**전 구간이 실제 데이터로 동작한다.** 외부 API **11종** 연결, 컨테이너 1개로 기동,
일정 생성 → 지도 경로 → HITL 확인 카드 → 재계획까지 앱에서 완주한다.
**기획안이 약속한 핵심 기능 중 미구현으로 남은 것은 없다.**

```
그래프    11 노드 (서브그래프 4 + 조율 7) · 라우트 7종 · 검증 6종
API       엔드포인트 23개
소스      백엔드 Python 10,496줄(54파일) · 모바일 TS 6,542줄
테스트    194개 (193 passed · 1 skipped) · ruff All checks passed
DB        places 2,092 · visits 32 · embeddings 32 · plan_edits 13
응답      중앙값 5.1초 (NFR-01 목표 15초)
```

---

## 2. 새 환경에서 되살리는 순서

### 2.1 필요한 것

- Docker Desktop · Node.js(앱 실행용) · 키 11종 → [SETUP.md](SETUP.md)
- **`.env` 는 저장소에 없다.** 위치는 `culturemate/.env` — `백엔드실행.bat` 이
  `--env-file .env` 를 자기 폴더 기준으로 찾는다. **루트에 두면 키가 하나도 안 들어간다.**
  (`.env.example` 은 이 저장소에 없다. SETUP.md §2 를 보고 직접 만든다.)

### 2.2 실행

**cmd 창**에서 (PowerShell 아님 — `%CD%` 가 확장되지 않는다):

```bat
cd /d C:\Users\31\Documents\CulturePilot_AI-main\culturemate
백엔드실행.bat
```

앱은 **새 cmd 창**에서:

```bat
cd /d C:\Users\31\Documents\CulturePilot_AI-main\culturemate\mobile
npm start -- --web --port 19006
```

백엔드 `http://localhost:8000` · 앱 `http://localhost:19006`.

### 2.3 살아 있는지 확인

```bat
curl "http://localhost:8000/diagnostics?probe=true"
```

**프로브 11종**이 전부 `ok: true` 면 정상이다. 시드가 없으면 개인화가 비므로:

```bat
docker exec culturemate python scripts/seed_demo.py
```

### 2.4 처음 이어받을 때 자주 밟는 함정

| 증상 | 원인 · 조치 |
|---|---|
| `.env` 를 고쳤는데 반영이 안 됨 | **재생성해야 한다.** `docker restart` 로는 안 된다 |
| `체크포인터 연결 실패 → InMemorySaver` | PostgreSQL 준비 전에 API가 떴다. `docker restart culturemate` |
| 일정이 이상하게 보임 | 오래된 스레드가 옛 스키마 객체를 들고 있다. 앱에서 **새 대화**를 시작한다 |
| 실기기·터널에서 서버를 못 찾음 | 앱 헤더의 «● 연결됨/목 모드» → **서버 연결** 화면에서 주소를 바꾼다 (재빌드 불필요) |
| 결과가 조용히 빔 | 외부 API 실패는 전부 `WARNING` 이다. `docker logs culturemate --tail 50` |

---

## 3. 문서 지도 — 무엇을 어디서 보나

### 3.1 설계 (2026-08-18 신규)

| 문서 | 역할 |
|---|---|
| [AGENT_ROLES.md](AGENT_ROLES.md) | **문서 1** — 기획안 4역할 → 설계 9 Agent 도출 · 책임 · 입출력 · 관심사 |
| [AGENT_WORKFLOW.md](AGENT_WORKFLOW.md) | **문서 2** — 실행 순서 · 데이터 흐름 · 분기/병렬/정지 · LangChain 배분 |
| [SYSTEM_C4.md](SYSTEM_C4.md) | **문서 3** — C4 L1~L3 · 포트와 어댑터 · 배포 구성 |
| `MOBIDIC_Architecture_설계서7.pptx` | 위 세 문서를 발표용 **28장**으로 |
| `diagrams/parallel-key-conflict.svg` | 병렬 실행 시 키 충돌과 출력 스키마 (덱 16번 슬라이드와 같은 내용) |

### 3.2 기존

| 문서 | 역할 |
|---|---|
| [PLANNING.md](PLANNING.md) | 기획안(MOBIDIC)이 무엇을 약속했나 · 구현 대조표 |
| [ARCHITECTURE.md](ARCHITECTURE.md) | 왜 이렇게 설계했나 — State · 리듀서 · HITL · DB |
| [STRUCTURE.md](STRUCTURE.md) | 요청 하나가 어떻게 흐르나 — 11단계 |
| [REQUIREMENTS.md](REQUIREMENTS.md) | 무엇을 만들기로 했나 — FR · **UR 전량 추적표(§3.5)** |
| [FUNCTIONAL_MAP.md](FUNCTIONAL_MAP.md) | 이 기능이 어느 파일에 있나 |
| [SETUP.md](SETUP.md) | 키 발급과 실행 |
| [PROGRESS.md](PROGRESS.md) | 지금 무엇이 되고 무엇이 안 되나 |
| [TEST.md](TEST.md) · [TEST_FUNCTIONAL.md](TEST_FUNCTIONAL.md) | 검증 결과 (지역별 / 전 구간) — **실행일 기준 기록이다** |

**숫자가 갈리면 우선순위** — 소스 > PROGRESS > 나머지. TEST 문서 둘은 **그때의 기록**이므로
현재값과 달라도 틀린 것이 아니다.

---

## 4. 다음 착수 지점

### 4.1 바로 손댈 수 있는 것 (새 외부 의존 없음)

| 순위 | 항목 | 왜 · 어디서 |
|:--:|---|---|
| 1 | **UR-35 파생 제약 개인화** | 도보·환승 상한이 아직 전역 상수(`WALK_PREFERENCE_MIN`·`TRANSFER_PENALTY_MIN`)다. 기획안 3.2가 개인 아카이브의 한 축으로 그린 것 |
| 2 | **같은 시·도 안의 거리 감점** | 시·도 판정으로 «다른 지역»은 닫혔지만 서울 안 강서구↔강동구처럼 한 구간이 지나치게 먼 경우는 그대로다 |
| 3 | **UR-30 저장 목록 → 일정 만들기** | `user_collections` 는 있고 일정 생성 경로만 없다 |
| 4 | **UR-32 기록 자동 유도** | `POST /visits` · `app/visit.tsx` 는 있으나 자동으로 뜨지 않는다. 캘린더가 진입점 |

### 4.2 품질·운영

- **카탈로그 실제 장소 확대** — 2,092곳 중 실제는 92곳. `link_place_ids` 연결률이 낮다.
- **Exa 429** — 초당 10회 제한. 연속 테스트에서 자주 발생한다.
- **DB 분리 검토** — 지금은 컨테이너 하나에 PostgreSQL + API. postgres가 죽으면 전체가 내려간다.

### 4.3 ⚠️ 반드시 먼저 처리할 것 — 노출된 키 재발급

개발 중 채팅·터미널에 값이 노출된 키가 있다. **재발급이 유일한 해결이다.**

```
data.go.kr · OpenAI · KCISA · NVIDIA · OpenRouteService(ORS)
```

---

## 5. 작업할 때 지키는 것

1. **소스가 근거다.** 주석·문서·기억은 근거가 아니다. 코드를 읽고 실제로 돌려서 확인한다.
2. **구조를 바꾸면 `tests/test_docs_contract.py` 가 먼저 깨진다.** 노드 수·검증 종류·엔드포인트·라우팅 표·주요 파일 줄 수를 문서와 대조한다. **테스트를 고치기 전에 문서를 먼저 맞춘다.**
3. **라이브러리 작업은 context7 로 문서를 먼저 확인한다** (LangGraph · FastAPI · pgvector · Expo). 이 프로젝트 고유 로직은 해당 없음 — 소스를 읽는다.
4. **키는 `.env` 에만.** 문서·소스에 절대 쓰지 않는다.
5. **`.bat` 은 CP949(ANSI) + CRLF 로 저장한다.** UTF-8로 저장하면 cmd 가 한글 주석을 명령으로 해석한다.

---

## 6. 알아 둘 상태

- **`test_docs_contract.py` 는 «셀 수 있는 것»만 지킨다.** 노드 **집합**과 `set(ROUTE_TABLE) == set(RequestType)` 을 볼 뿐, 문서의 «6종»·«11 노드» 같은 **숫자 문장은 읽지 않는다.** 숫자를 고칠 때는 `build.py` 의 `add_node` 호출을 직접 센다.
- **문서 정합성은 2026-08-18 에 한 번 훑었다.** 테스트 수·줄 수·검증 종류·리듀서 목록·실행 경로·기획안 대조표를 소스와 맞췄다. 그 뒤 코드가 바뀌었다면 같은 항목부터 의심한다.
- **`.env` 에 `MODEL_ROUTER`·`MODEL_PLANNER`·`MODEL_WRITER`·`MODEL_FAST` 가 비어 있다.** 문서([REQUIREMENTS §5.3](REQUIREMENTS.md))가 권장하는 «역할별 혼합 배선»이 지금 환경에는 적용돼 있지 않고, 네 역할 모두 `LLM_BACKEND=nim` 기본값으로 간다. 응답 품질·시간을 재기 전에 이 값부터 확인한다.
