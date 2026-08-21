# CulturePilot_AI

아카이브 기반 문화생활 개인화 멀티에이전트 · LangGraph + FastAPI + React Native(Expo)

프로젝트 본체는 `culturemate/` 아래에 있다.

---

## 라이브러리 관련 작업에는 MCP context7 을 먼저 쓴다

**대상** — 아래에 해당하면 코드를 고치기 전에 `context7` 로 현재 문서를 확인한다.

- LangGraph · LangChain (State·리듀서·서브그래프·`interrupt()`·체크포인터·`Send`)
- FastAPI · Pydantic · psycopg / psycopg_pool
- pgvector · PostgreSQL18
- Expo · React Native (SDK 버전, 네이티브 모듈 가용성)
- 그 밖에 **버전에 따라 동작이 갈리는** 외부 패키지·이미지

**쓰는 법**

```
1) mcp__context7__resolve-library-id   라이브러리 이름 → /org/project
2) mcp__context7__query-docs           개념 하나당 질의 하나
```

**왜 필요한가 — 실제로 겪은 것들**

| 사고 | context7 이 막을 수 있었던 것 |
|---|---|
| 서브그래프가 고친 `conditions` 가 부모로 안 돌아옴 | *"subgraph's output keys must be mapped back to the parent's state"* |
| pg18 컨테이너 기동 실패 | 마운트 지점이 `/var/lib/postgresql` 로 바뀐 변경점 |
| 리듀서 `Channel already exists with a different type` | 같은 채널이 여러 스키마에 있으면 **함수 객체 동일성**까지 본다 |
| Expo Go 비호환 | SDK 버전별 Expo Go 지원 범위 |

**쓰지 않아도 되는 경우** — 이 프로젝트 고유 로직(라우터 규칙 파서, 예산 계산,
지역 필터, 스케줄러)은 문서에 없다. 그건 **소스를 읽고 직접 돌려서** 확인한다.

---

## 이 프로젝트의 작업 규칙

1. **소스가 근거다.** 주석·문서·기억은 근거가 아니다. 코드를 읽고 실제로 돌려서 확인한다.
   문서가 구현을 앞서 있던 항목이 실제로 여럿 있었다(UR-01·UR-09).

2. **구조를 바꾸면 `tests/test_docs_contract.py` 가 먼저 깨진다.** 그래프 노드 수,
   검증 종류, 엔드포인트 수, 라우팅 표, HITL 조건, 주요 파일 줄 수를 문서와 대조한다.
   테스트를 고치기 전에 **문서를 먼저 맞추는지** 확인한다.

3. **모델을 바꾸기 전에 잰다.** `scripts/bench_models.py`. 단, 이 스크립트의 기본
   라우터 작업은 축소 스키마라 실제보다 훨씬 빠르게 나온다.

4. **키는 `.env` 에만.** 문서·소스에 절대 쓰지 않는다. 한 번 들어가면 이력에 남아
   재발급이 유일한 해결이다.

5. **`.bat` 은 CP949(ANSI) + CRLF 로 저장한다.** UTF-8 로 저장하면 cmd 가 한글
   주석을 명령으로 해석해 깨진다.

---

## 실행

```bat
:: 백엔드 (컨테이너 1개 = PostgreSQL + API)
cd culturemate && 백엔드실행.bat

:: 앱 (웹) — 포트를 지정하지 않으면 Expo 기본 8081 로 뜬다
cd culturemate\mobile && npm start -- --web --port 19006
```

- 백엔드 `http://localhost:8000` · 앱(웹) `http://localhost:19006` (포트 지정 시)
- `.env` 를 고치면 **재생성**해야 한다. `restart` 로는 반영되지 않는다.
- 시드: `docker exec culturemate python scripts/seed_demo.py`

## 문서

**세션을 이어받았다면 `culturemate/docs/HANDOFF.md` 를 먼저 읽는다.**
지금 상태 · 새 환경 복원 절차 · 다음 작업의 착수 지점이 거기 있다.

| 문서 | 역할 |
|---|---|
| `culturemate/docs/HANDOFF.md` | **세션 인계** — 지금 상태 · 환경 복원 · 다음 착수 지점 |
| `culturemate/docs/PLANNING.md` | 기획안(MOBIDIC)이 무엇을 약속했나 · 구현 대조표 |
| `culturemate/docs/ARCHITECTURE.md` | 왜 이렇게 설계했나 |
| `culturemate/docs/STRUCTURE.md` | 요청 하나가 어떻게 흐르나 |
| `culturemate/docs/REQUIREMENTS.md` | 무엇을 만들기로 했나 |
| `culturemate/docs/FUNCTIONAL_MAP.md` | 이 기능이 어느 파일에 있나 |
| `culturemate/docs/SETUP.md` | 키 발급과 실행 |
| `culturemate/docs/PROGRESS.md` | 지금 무엇이 되고 무엇이 안 되나 |
| `culturemate/docs/TEST.md` | 검증 결과 · 결함 · 신규 UR |
