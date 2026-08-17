# CultureMate

**아카이브 기반 문화생활 개인화 멀티에이전트** — LangChain / LangGraph

과거 방문 경험과 일정 수정 행동을 기억해, 다음 문화생활 일정의 판단 근거로 쓰는 서비스.
추천을 자동으로 바꾸지 않고 **근거와 선택지를 제시한 뒤 사용자가 결정**하게 한다.

📄 설계(왜): [`ARCHITECTURE.md`](docs/ARCHITECTURE.md) · 🧭 구조(어떻게): [`STRUCTURE.md`](docs/STRUCTURE.md) · 🗺 기능별 소스: [`FUNCTIONAL_MAP.md`](docs/FUNCTIONAL_MAP.md)
🔑 키 발급·실행: [`SETUP.md`](docs/SETUP.md) · 📊 현재 상태: [`PROGRESS.md`](docs/PROGRESS.md) · ✅ 검증 결과: [`TEST.md`](docs/TEST.md) · 📋 요구사항: [`REQUIREMENTS.md`](docs/REQUIREMENTS.md)
🎯 기획안 대조(MOBIDIC): [`PLANNING.md`](docs/PLANNING.md) · 🧪 기능 검증: [`TEST_FUNCTIONAL.md`](docs/TEST_FUNCTIONAL.md)

---

## 무엇이 다른가

| | 일반 추천 서비스 | CultureMate |
|---|---|---|
| 아카이브 | 기록 보관 | 다음 일정의 **판단 근거** — 탐색보다 먼저 조회 |
| 개인화 신호 | 별점·관심 카테고리 | + **삭제·교체·순서·체류시간 변경** 등 수정 행동 |
| 재방문 | 신규 장소와 동일 취급 | 마지막 방문 이후 **달라진 점** 8개 필드 비교 |
| 일정 변경 | AI가 자동 수정 | 근거·영향·선택지 제시 후 **사용자 확정** (`interrupt`) |
| 계획 이탈 | 대응 없음 | 공백·조기종료 시 **현장 재계획** |

---

## 빠른 시작

**앱만 먼저 보기** — 백엔드 없이 전체 플로우(일정 생성 → 확인 카드 → 재계획)가 돕니다.

```bash
cd mobile && npm install && npm start        # mobile/.env 없이 켜면 목(mock) 모드
```

**백엔드까지**

```bash
# .env 를 만들어 키를 채운다 — 항목별 설명은 docs/SETUP.md
백엔드실행.bat        # 컨테이너 1개 = PostgreSQL + API
curl localhost:8000/health
# mobile/.env 의 EXPO_PUBLIC_API_URL 에 PC의 LAN IP를 넣으면 앱이 실서버로 붙습니다
```

```bash
# 로컬 개발 (Python 3.11+ 필요)
pip install -r requirements.txt
export LLM_BACKEND=fake              # 외부 API 없이 그래프만 돌려보기
pytest -q                            # 128 passed, 1 skipped
python scripts/render_graph.py main  # 그래프 → Mermaid
uvicorn app.api.main:app --reload
```

> **Python 3.11+ 필수.** LangGraph의 `interrupt()`는 async 노드에서 runnable config를
> contextvar로 전파받는데, 이 전파가 3.11+ 의 asyncio 컨텍스트 인자에 의존한다.

---

## 구조

```
사용자 요청
   └─ classify ──────── 요청 유형 7종 → 실행 계획(PlanFlags)
        ├─ archive      개인 아카이브 3-facet 병렬 검색 → RRF → 경고 카드
        ├─ discovery    행사·상시공간·웹 병렬 탐색 → 공식정보 검증
        └─ current_plan 기존 일정 로드
             └─ merge_context
                  └─ itinerary   지도·날씨·운영시간 병렬 분석 → 결정론적 편성 → 공백 채우기
                       └─ validation   5종 병렬 검증 → 자동/수동 분류
                            ├─ hitl     interrupt() → 사용자 선택 → 재계획
                            └─ finalize → persist → compose
```

핵심 설계 결정 다섯 가지:

1. **필요한 Agent만 실행** — 정적 라우팅 테이블 7행. LLM 오케스트레이터는 확장 시점에 `fan_out()` 하나만 교체.
2. **아카이브 3-facet 병렬 검색** — 유사 장소 / 상황 일치 / 불편·수정행동. 단일 질의로는 못 잡는 이웃들.
3. **하이브리드 + RRF + 리랭크** — dense(pgvector HNSW) + lexical(tsvector) → RRF → cross-encoder. 최신성 감쇠 × 불편 가중 × 상황 일치로 사후 보정.
4. **결정론적 스케줄링** — 이동시간·운영시간 만족은 코드가, 배치 이유 서술은 LLM이.
5. **HITL은 서버 인터럽트** — 체크포인터에 상태를 남기므로 모바일 연결이 끊겨도 재개된다.

---

## API

| 엔드포인트 | 용도 |
|---|---|
| `POST /chat` | SSE 스트리밍 (`token` / `update` / `interrupt` / `done`) |
| `POST /chat/sync` | 비스트리밍 폴백 — React Native fetch는 본문 스트리밍 미지원 |
| `POST /resume`, `/resume/sync` | HITL 선택 반영 후 재개 |
| `GET /threads/{id}/state` | 앱 복귀 시 상태 복원 |
| `GET /threads/{id}/evidence/{eid}` | 판단 근거 원문 지연 로드 (UR-14) |
| `POST /visits` | 관람 기록 저장 → 아카이브 임베딩 → 프로필 갱신 |
| `POST /preferences/cards` | 취향 카드 등록 → 프로필 재집계 (UR-01 · 콜드 스타트 개인화) |
| `GET /diagnostics?probe=true` | 어떤 키가 설정됐고 어떤 API가 실제 응답하는지 진단 |

**클라이언트: React Native (Expo).** 현장 재계획이 핵심 시나리오라 모바일 우선.
GPS 현재 위치는 `conditions_override`로 주입하고, 지도 렌더링은 서버가 내려준 `map_path`
좌표만 사용한다(지도 공급자 교체가 서버 계약에 영향을 주지 않는다).
→ [`mobile/README.md`](mobile/README.md) · 설계서 [§10](docs/ARCHITECTURE.md)

---

## 설정

`LLM_BACKEND` 로 모델 공급자를 바꾼다. 기본은 NVIDIA NIM이고, 그래프 코드는 구체 클래스를
import 하지 않는다 (`app/llm/provider.py`에서 역할 이름으로만 요청).

```bash
LLM_BACKEND=nim        # ChatNVIDIA / NVIDIAEmbeddings / NVIDIARerank
LLM_BACKEND=openai     # ChatOpenAI / OpenAIEmbeddings
LLM_BACKEND=fake       # 외부 호출 없이 그래프 구조만 테스트
```

주요 튜닝 파라미터는 [`docs/SETUP.md`](docs/SETUP.md) 참조. 특히 `FRICTION_BOOST`(불편 기록 가중)는
"경고를 놓치는 비용 > 불필요한 경고 비용"이라는 판단이 들어간 값이라 운영 데이터로 재조정한다.

---

## 테스트

```
tests/test_graph.py      메인·서브그래프 컴파일, 7종 라우트 팬아웃
tests/test_archive.py    RRF 융합, 최신성 감쇠, 불편 가중, facet 교차 신호
tests/test_itinerary.py  시간 역전 없음, 이동시간 확보, 종료시각 준수, 확정 장소 잠금
tests/test_hitl.py       triage 분기, 카드 옵션·근거, 재계획 판단, 결정 반영
```

외부 API·DB·LLM 없이 전 구간이 실행된다 (`LLM_BACKEND=fake`).

```
mobile/  npm run typecheck   타입 검사
         npm run verify      HITL 계약 회귀 테스트 17항목 (백엔드·시뮬레이터 불필요)
         npm run export      웹 번들 빌드로 전체 컴파일 확인
```
