# 세션 인계

> 다른 로그인·다른 기기에서 **이 파일 하나만 읽고 이어갈 수 있게** 쓴다.
> 대화 이력은 넘어가지 않는다. 넘어가는 것은 저장소와 이 문서뿐이다.
> 작업을 마칠 때마다 §0 과 §2 를 갱신한다.

작성: **2026-08-17**

---

## 0. 지금 상태 (30초)

| 항목 | 값 | 근거 |
|---|---|---|
| 전 구간 동작 | ✅ 일정 생성 → 지도 → HITL → 재계획 | [PROGRESS.md §1](PROGRESS.md) |
| 자동 테스트 | **187 passed · 1 skipped** | 2026-08-17 컨테이너에서 **실제 실행 확인** (UR-18 회귀 22개 추가) |
| 정적 검사 | **All checks passed** (`ruff check app scripts tests`) | 같은 날 실행 확인 |
| 외부 API | **9/10 정상** — KCISA 만 의도적 제외 | [TEST_FUNCTIONAL.md](TEST_FUNCTIONAL.md) · `/diagnostics?probe=true` |
| 규모 | 그래프 11노드 · 라우트 7종 · 엔드포인트 **23개** · 검증 **6종** | 2026-08-17 소스 재확인 |
| 지역 정확도 | **UR-18 시·도 판정 적용** — 「서초구」 요청에서 경기 후보 5건이 실제로 걸러졌다 | [TEST.md D-01](TEST.md) |
| 남은 기능 | **없음.** 기획안 핵심은 전부 닫혔다 — 남은 것은 품질·운영 | [PROGRESS.md §3](PROGRESS.md) |

> ⚠️ **`ruff` 는 반드시 `pyproject.toml` 을 붙여 돌린다.** `Dockerfile` 이 그 파일을
> 이미지에 복사하지 않아, `docker exec culturemate ruff check .` 로 돌리면 프로젝트
> 룰셋이 아니라 ruff **기본값**으로 검사해 96건이 쏟아진다. 코드가 더러운 게 아니라
> 설정이 없는 것이다. 올바른 명령은 [TEST.md §9](TEST.md).

---

## 0.04 2026-08-17 (이어진 세션) — 큰 파일 두 개 분할

UR-18 다음으로 §2 의 «분할» 을 했다. **동작은 하나도 안 바뀌었다.**

| 전 | 후 | 최대 파일 |
|---|---|---|
| `router.py` 1,093줄 | `router/` 6모듈 874줄 | 266줄 (`detect.py`) |
| `subgraphs/itinerary.py` 1,321줄 | `subgraphs/itinerary/` 9모듈 1,525줄 | 258줄 (`schedule.py`) |

줄 수 합이 늘어난 건 모듈마다 docstring 과 import 가 붙었기 때문이다. 각 패키지의
`__init__.py` 첫머리에 **«증상 → 어느 파일»** 표를 뒀다. 다음 사람이 버그를 들고
왔을 때 먼저 볼 곳이다.

### 어떻게 나눴나 — 손으로 옮겨 적지 않았다

리팩터링에서 가장 찾기 어려운 버그는 «옮기다 한 글자 바뀐 것»이다. 그래서
**원본의 줄 범위를 스크립트로 발췌**했고, 끝나고 **AST 로 대조**했다 —
최상위 정의(라우터 74개 · 일정 56개)를 이름별로 꺼내 소스 문자열이 글자 그대로
같은지 봤다. 이 대조가 실제로 두 가지를 잡았다.

| 잡힌 것 | 어떻게 |
|---|---|
| `_span_end` 의 **마지막 `return` 한 줄**이 잘렸다 | 줄 범위 끝을 1 적게 잡았다. 테스트 5개가 `TypeError` 로 죽어서 먼저 드러났고, AST 대조가 «13줄 → 12줄» 로 정확히 짚었다 |
| `ROUTE_TABLE` 을 손으로 옮겨 적었더니 **완전히 다른 표**가 됐다 | 기억으로 쓴 `PLAN_DAY`·`MODIFY_PLAN` 은 존재하지 않는 값이었다(실제로는 `PLAN_CREATE`·`WEATHER_ADJUST`…). 발췌로 교체 |

**이 대조를 다시 하려면** 분할 이전 이미지가 필요하다. 이번에는 운이 좋았다 —
`culturemate-api` 이미지가 분할 전에 빌드돼 있어 `docker run --rm --entrypoint sh
culturemate-api -c 'cat /srv/app/graph/router.py'` 로 원본을 꺼냈다. 저장소가 git 이
아니라서 그것 말고는 되돌릴 방법이 없었다. **다음에 큰 것을 옮길 때는 먼저 사본을 뜬다.**

### 옮기면서 드러난 구조

- **라우터는 라우터가 아니었다.** 1,093줄 중 라우팅은 표 하나(30줄)이고 나머지는
  전부 «발화를 어떻게 읽는가»였다. 그래서 파서 쪽을 네 모듈로 갈랐다 —
  `timeparse`(시각·체류) · `endpoints`(출발·도착 절) · `detect`(지역·개수·종류) ·
  `rules`(순서와 병합).
- **순환이 두 번 생길 뻔했다.** `_NOT_LANDMARK` 는 rules·endpoints·detect 셋이 쓰고,
  `_TIME`·`_to_time` 은 endpoints·detect 둘이 쓴다. 시각 파싱을 `timeparse` 로
  따로 빼서 «detect → timeparse ← endpoints» 로 세웠다. 그냥 나누면
  endpoints ↔ detect 가 서로를 부른다.
- **`_detect_dwell` 이 시각 모듈에 있는 이유** — '1시간~2시간'의 '1시'가 시각 파서에
  걸린다. 두 파서가 같은 문자열을 두고 다투는 자리라 한 모듈에 뒀다.

---

## 0.05 2026-08-17 (이어진 세션) — UR-18 행정구역 필터

§2 의 후보 셋 중 **UR-18** 을 골라 닫았다. 기획안 기능이 아니라 «사용자가 가장 먼저
틀렸다고 느끼는 자리»다.

| # | 작업 | 남긴 것 |
|:--:|---|---|
| 1 | **환경 복구** | Docker 엔진이 안 뜬 진짜 원인은 §1.1 의 고아 소켓이 **아니었다** — 아래 참고 |
| 2 | **`tools/region.py` 신규** | 주소·지오코딩 응답 → (시·도, 시군구). 134줄 |
| 3 | **`GeoPoint.sido/sigungu`** | 지오코딩이 좌표와 같이 주는 값이라 같이 싣는다. 마이그레이션 없음 |
| 4 | **`discovery.normalize` 관문 4개로** | 시·도가 다르다고 **확인된** 후보만 제외. 말한 구는 `REGION_BONUS`(0.15) 가점 |
| 5 | **회귀 22개** | `tests/test_region.py`. 165 → **187 passed** |

**설계 판단 셋.**

- **모르는 것은 버리지 않는다.** 행정구역을 알 수 없는 후보(주소를 안 주는 공공 API
  행사)는 통과시키고 거리 상한이 받는다. 반대로 하면 실제로 열리는 행사가 사라진다.
  그래서 `MAX_ANCHOR_KM` 은 **지우지 않고 보조 수단으로 남겼다.**
- **구(區)는 자르지 않고 가점만 준다.** 구 경계는 생활권과 다르다. 서초구 요청에
  200m 건너 강남구를 없는 곳 취급하면 그것대로 틀린 결과다.
- **출발지는 탐색 범위가 아니다.** 「판교역에서 출발해서 서초구」의 허용 시·도는
  서울뿐이다. `region_points()` 가 앵커를 고를 때 쓰는 판단과 같게 맞췄다.

**밟은 함정 — 시·도 이름을 문자열 아무 곳에서나 찾으면 안 된다.**

| 입력 | 순진하게 찾으면 | 실제로 필요한 규칙 |
|---|---|---|
| `세종문화회관`(서울 종로) | 세종시 | 짧은 이름은 **맨 앞에서만**, 뒤에 경계(공백·쉼표·끝)가 올 때만 |
| `서울주문화센터`(울산 울주) | 서울 | 〃 |
| `경기도 광주시` | 광주광역시 | 〃 (맨 앞의 `경기도` 가 이긴다) |
| `제주도립미술관`·`경기도자박물관` | 제주·경기 | 정식 명칭도 **경계**를 봐야 한다 |

`culture_api._OTHER_REGIONS` 가 '세종'을 목록에서 빼야 했던 것과 **같은 함정**이다.
그래서 `region.of_candidate()` 는 **이름을 아예 보지 않는다** — 주소와 지오코딩
결과만 본다. 이름 판정은 `_in_region` 의 몫으로 남겼다(«[대전]» 태그가 있을 때만).

**실측** — 「내일 판교역에서 출발해서 서초구에서 전시 보고 카페 갈래」

```
discovery: 좌표 없는 후보 9건 제외 (전체 67건)
discovery: 요청 시·도(서울) 밖 후보 5건 제외      ← 새로 걸린 것
discovery: 반경 60km 밖 후보 8건 제외
```

출발지가 판교라 상시공간 탐색이 실제로 `경기도 성남시 분당구 미술관` 을 긁어 왔고,
그 5건은 25km라 **거리로는 영원히 안 걸린다.** 결과 일정 5곳은 전부
위도 37.47~37.48 · 경도 127.02~127.05(서초구)였다.

### 이 세션에서 새로 드러난 환경 문제 (§1.1 보다 먼저 볼 것)

**1. Docker 가 안 뜬 원인은 소켓이 아니라 «전부 NUL 인 설정 파일»이었다.**
`~/.docker/daemon.json`(124바이트)·`~/.docker/windows-daemon.json`(28바이트)이
**내용 전체가 `\x00`** 이었다. 비정상 종료 때 NTFS 가 크기만 기록하고 내용을 못
플러시한 자국이다. 증상은 §1.1 과 똑같이 «An unexpected error occurred» 인데,
소켓 디렉터리를 아무리 비켜 놔도 안 고쳐진다. **로그를 먼저 본다:**

```powershell
Get-Content "$env:LOCALAPPDATA\Docker\log\host\com.docker.backend.exe.log" -Tail 400 |
  Select-String '"error":' | Select-Object -Last 3
```

`parsing daemon config …: invalid character '\x00'` 이 보이면 그 파일을 **옆으로
치우면** 된다(지우지 않는다 — 복구할 내용은 어차피 없다). 없으면 Docker 가 기본값으로 뜬다.
전수 확인은 `~/.docker` · `%APPDATA%\Docker` · `%LOCALAPPDATA%\Docker` 에서
«바이트가 전부 0인 json» 을 찾는 것이다.

**2. `.env` 가 Docker 29 에서 거부된다.** 두 가지를 고쳤다.

| 증상 | 원인 |
|---|---|
| `invalid env file: variable 'TAVILY_API_KEY ' contains whitespaces` | 키 이름 뒤에 **공백 한 칸**. 이 키는 그동안 앱에 전달되지 않고 있었다 — Exa 429 가 잦았던 이유일 수 있다 |
| `verify_top_k: Input should be a valid integer … '12  # 실제로 검증할…'` | **줄 끝 주석**. `--env-file` 은 `#` 뒤를 값의 일부로 넘긴다. 18개 키가 걸렸다 |

줄 끝 주석은 **윗줄로 옮겼다**(설명은 그대로). `.env` 를 다시 쓸 때 주석은 반드시
독립된 줄에 둔다.

---

## 0.1 2026-08-17 세션에서 한 일

기획안(`서비스 기획안 모비딕_남경.pdf`) 대조에서 시작해, **핵심 결함 세 가지를 전부
채우고** 문서군을 정합화했다. 아래 순서가 곧 의존 순서다.

| # | 작업 | 남긴 것 |
|:--:|---|---|
| 1 | **기획안 분석** — PDF 15쪽 + 화면 시안 12컷 판독 | `docs/PLANNING.md` 신규(기획안 원문 + §7 구현 대조표 + §8 어긋난 지점) |
| 2 | **문서 숫자 정정** | 12노드→**11**, 8라우트→**7**, 5서브그래프→**4**(`report` 삭제됨), `taste_report` 라우트 없음. README 포함 |
| 3 | **`.env` 전면 작성 + API 실검증** | 항목마다 «무엇을/어디서/없으면/비용/프로브» 주석. NAVER 두 쌍이 **서로 바뀐 것**을 실측으로 갈랐다 |
| 4 | **앱 크래시 수정** | `NaverMap.tsx` — `useCallback` 이 조기 반환 아래에 있어 지도 로드 실패 시 훅 수가 줄고 화면 전체가 죽었다 |
| 5 | **도착 안내 버그 수정** | `endpoint_notes()` 한 곳으로 모으고, 장소가 바뀌는 **모든** 지점에서 재계산 |
| 6 | **FR-33 종류별 개수** | `schemas.KIND_GROUPS` + `TripConditions.kind_quota`. «문화 2 + 디저트 3» 이 실제로 2+3 으로 나온다 |
| 7 | **UR-28 캘린더** ★핵심 | `repo.list_plans/load_plan` · `GET /plans/{user_id}` · `GET /plans/detail/{id}` · `(tabs)/calendar.tsx` |
| 8 | **UR-40 선제 경고 복원** | `validation.check_friction` — 검증 5→**6종**. 기획안 2.1-② 가 처음으로 실제 데이터에서 뜬다 |
| 9 | **UR-09 수정 행동 기록** | `persist → save_plan_edits()` · `rebuild_profile()` 이 되읽는다 |
| 10 | **문서 체계 정비** | UR 40개에 **선순환 단계 축**(①탐색~⑤분석·ⓧ횡단) 부여 · **번호 규칙** 명문화 · `TEST - 복사본.md` → `TEST_FUNCTIONAL.md` 로 정정 |
| 11 | **UR-01 · UR-31 취향 카드** ★마지막 | `repo.save/load_preference_cards` · `profile._CARDS_SQL`+`apply_preference_cards` · `POST/GET /preferences/cards` · `mobile/app/taste-cards.tsx`. 엔드포인트 21→**23** |

**한 일 중 되돌리기 어려운 것은 없다.** 스키마 변경도, 마이그레이션도 없었다 —
UR-28 은 이미 있던 `plans` 테이블을 읽기만 했고, UR-40 은 이미 있던
`Issue.kind="past_friction"` 의 **생산자만** 되살렸다.

**계약 테스트가 세 번 먼저 깨졌고, 세 번 다 문서를 먼저 고쳤다** — 엔드포인트
19→21, `router.py` 줄 수, 검증 5→6종. 설계 의도대로 작동한 것이다.

### UR-01 을 이렇게 붙였다 (다음 사람이 되짚을 때)

**새 필드도, 마이그레이션도 없다.** 카드를 기존 두 필드로 접었다 —
카테고리 카드는 `preferred_categories` 로(부정이면 **음수**), 장소 부정 카드는
`frequent_removals` 로. 그래서 `personal_score()` 는 **한 줄도 안 고쳤다.**

밟은 함정 넷을 남긴다. 전부 실제로 걸린 것들이다.

| 함정 | 실제로 벌어지는 일 |
|---|---|
| 재집계가 카드를 안 읽으면 | 재집계는 **전량 재계산**이라, 방문 기록 한 건이 들어온 순간 등록해 둔 취향이 전부 지워진다. UR-09 와 똑같은 자리 |
| `preferred_categories` 를 그냥 내림차순 상위로 읽으면 | 음수가 처음 들어오므로 **싫다고 한 카테고리가 검색어가 된다.** 네 곳(`router._apply_taste`·`nodes._taste_summary`·모바일 `report.tsx`·`index.tsx`)에 `> 0` 필터를 함께 넣었다 |
| `rebuild_profile()` 만 부르면 | 반환값이 호출자 손에서 사라진다. 추천 경로는 전부 `load_profile()` 로 **저장된 행**을 읽으므로 `save_profile()` 까지 해야 효과가 난다. "저장은 됐는데 추천은 그대로"가 여기서 갈린다 |
| `c.subject::uuid` 로 조인하면 | 카테고리 이름("전시")이 든 행에서 uuid 문법 오류가 나고, 재집계 전체가 `except` 로 떨어져 **프로필이 통째로 빈 값이 된다.** 반대로 `p.id::text = c.subject` 로 건다 |

그리고 `preference_cards.user_id` 는 `users(id)` 를 참조한다. 카드를 넣는 사람은
«아직 아무것도 없는» 사용자라 행이 없어 첫 카드가 FK 로 튕긴다 — `save_preference_cards()`
가 `INSERT INTO users … ON CONFLICT DO NOTHING` 을 먼저 한다. 지금까지 `users` 행을
만드는 곳은 시드 스크립트뿐이었다.

**검증** — 콜드 스타트 사용자를 새로 만들어 카드 전/후 점수를 쟀다.
`0.5 · 0.5` (완전히 평평) → `0.59 · 0.468`. 회귀는 `tests/test_preferences.py` 17개.

### 이 세션에서 확인된, 문서에 안 적히던 사실

- **`docs/TEST - 복사본.md` 는 사본이 아니었다.** TEST.md(지역별)와 **범위가 다른 별도
  회차**이고 결함 번호도 독립이다. 지울 뻔했다 → `TEST_FUNCTIONAL.md` 로 개명.
- **죽은 코드는 없다.** 미참조 함수 11건은 전부 `@app.post` 라우트 핸들러(오탐).
- **불필요한 주석도 없다.** 빈 주석 7줄은 여러 줄 블록의 **문단 구분자**였다.
- 남은 큰 파일은 둘뿐 — `itinerary.py` 1,321줄 · `router.py` 1,091줄. **분할은
  미착수**이며, 결함 수정이 끝난 뒤로 미뤄 두기로 했다(diff 가 뒤섞이지 않게).
  → **2026-08-17 완료.** §0.04 참고.

---

## 1. 새 로그인에서 먼저 할 일

저장소만 있으면 된다. 단, **`.env` 는 저장소에 없다**(`.gitignore`).

1. **`.env` 복원** — `culturemate/.env`. 필요한 키 이름과 발급 절차는
   [SETUP.md](SETUP.md). 키는 문서·소스에 절대 쓰지 않는다.
   현재 쓰는 키 이름(값 아님): `NVIDIA_API_KEY` `OPENAI_API_KEY` `DATA_GO_KR_KEY`
   `CULTURE_API_KEY` `KMA_API_HUB_KEY` `NAVER_CLIENT_ID/SECRET`
   `NAVER_SEARCH_CLIENT_ID/SECRET` `ODSAY_API_KEY` `ORS_API_KEY` `EXA_API_KEY`
   `PG_DSN` `CHECKPOINT_DSN`.

   ⚠️ **재발급 대상** — 개발 중 채팅에 붙여넣은 키가 있다:
   **OpenAI · NVIDIA · ODsay · NCP Maps** (+ 이전부터 알려진 data.go.kr · KCISA).
   새 환경을 만드는 지금이 처리할 때다.

   **키를 넣을 때 반드시 걸리는 함정 넷** — 전부 2026-08-17 에 실측으로 갈랐다.

   | 증상 | 진짜 원인 |
   |---|---|
   | NCP·NAVER 검색이 **양쪽 다 401** | 두 쌍이 **서로 바뀌어** 있었다. NCP 는 짧은 ID(10자)+긴 Secret(40자), Developers 는 그 반대다 |
   | NCP `errorCode 210 Permission Denied` | 키는 맞다. 그 **Application 에 Maps API 가 안 붙어** 있다. `200 Authentication Failed`(키 틀림)와 구분해 읽는다 |
   | ODsay `ApiKeyAuthFailed` | 인코딩 문제가 아니다(`+`/`%2B`/2회 전부 동일). **애플리케이션 등록·서비스 플랫폼(Server IP)** 문제다 |
   | 기상청 사용량이 **0으로 보임** | `KMA_API_HUB_KEY` 가 있으면 `apihub.kma.go.kr` 로만 간다. 공공데이터포털 카운터는 영원히 0이다 |

   `.env` 자체에 항목별 설명 주석이 붙어 있으니 그것을 먼저 읽는 편이 빠르다.

2. **기동**

   ```bat
   백엔드실행.bat
   cd culturemate\mobile && npm start
   ```

   백엔드 `http://localhost:8000` · 앱 `http://localhost:19006`.
   `.env` 를 고치면 **컨테이너를 재생성**해야 한다. `restart` 로는 반영되지 않는다.

3. **시드**

   ```bat
   docker exec culturemate python scripts/seed_demo.py
   ```

4. **이어가기 전 확인** — 이 4개가 통과하면 §0 의 상태가 재현된 것이다.
   `tests/` 와 `pyproject.toml` 은 이미지에 없으므로 **반드시 마운트해서** 돌린다.

   ```bat
   docker run --rm -v "%CD%\tests:/srv/tests" -v "%CD%\docs:/srv/docs" ^
     -v "%CD%\app:/srv/app" -v "%CD%\scripts:/srv/scripts" ^
     -v "%CD%\pyproject.toml:/srv/pyproject.toml" -w /srv ^
     --entrypoint pytest culturemate-api -q

   docker run --rm -v "%CD%\app:/srv/app" -v "%CD%\scripts:/srv/scripts" ^
     -v "%CD%\tests:/srv/tests" -v "%CD%\pyproject.toml:/srv/pyproject.toml" -w /srv ^
     --entrypoint ruff culturemate-api check app scripts tests

   curl http://localhost:8000/health
   curl "http://localhost:8000/diagnostics?probe=true"
   ```

   기대값 — `187 passed, 1 skipped` · `All checks passed!` · `{"status":"ok"}` ·
   프로브 **9/10**(`culture_api` 만 실패로 보이는 게 정상 — KCISA 를 끄고
   웹검색·카탈로그가 대신 채운다).

### 1.1 Docker Desktop 이 안 뜨면 (이 PC 에서 반복됨)

> ⚠️ **먼저 로그를 본다.** 증상(«An unexpected error occurred»)이 같아도 원인은
> 최소 두 가지다 — 아래의 고아 소켓, 그리고 **전부 NUL 인 `~/.docker/*.json`**.
> 후자는 소켓을 아무리 비켜 놔도 안 고쳐진다. 판별법과 조치는 [§0.05](#이-세션에서-새로-드러난-환경-문제-11-보다-먼저-볼-것).

기동 중 «An unexpected error occurred» 로 죽고 엔진이 안 올라오는 일이 잦다.
원인은 **지울 수 없는 고아 소켓 파일**이다(`The file cannot be accessed by the system`).
파일만 지우려 하면 실패하므로 **디렉터리째 비켜 두고** 새로 만든다.

```powershell
Get-Process | ? { $_.ProcessName -match '^(Docker Desktop|com\.docker\.backend)$' } | Stop-Process -Force
$s = Get-Date -Format "MMdd-HHmmss"
foreach($d in @("$env:LOCALAPPDATA\Docker\run","$env:LOCALAPPDATA\docker-secrets-engine")){
  if(Test-Path $d){ Rename-Item $d "$(Split-Path $d -Leaf).old$s" }
  New-Item -ItemType Directory -Path $d | Out-Null
}
Start-Process "$env:LOCALAPPDATA\Programs\DockerDesktop\Docker Desktop.exe"
```

- 다이얼로그의 **«Reset to factory defaults» 는 절대 누르지 않는다** — 볼륨이 전부
  지워져 `culturemate_pgdata`(시드 데이터)가 날아간다. «Quit» 만 안전하다.
- 재부팅하면 `*.old*` 디렉터리들을 지울 수 있다.

---

## 2. 다음 작업 — 고르는 자리

**기획안이 약속한 핵심 기능은 이제 전부 닫혔다.** UR-28 · UR-40 · UR-09 에 이어
마지막으로 남아 있던 **UR-01 취향 카드**를 2026-08-17 에 채웠다(§0.1). 그래서 이 절은
«정해진 다음 하나»가 아니라 **고르는 자리**다. 우선순위는 [PROGRESS.md §3](PROGRESS.md)
에 있고, 아래 §3 에 «의도적으로 미뤄 둔 것» 둘을 따로 적어 두었다.

**UR-18(§0.05)과 파일 분할(§0.04)은 2026-08-17 에 닫혔다.** 남은 후보를 값이 큰 순서로:

| 후보 | 왜 | 착수 지점 |
|---|---|---|
| **UR-19 장소 데이터 자동 보강** | UR-18 로 «다른 지역이 섞이는» 문제는 닫혔지만, 그래서 이제 **그 지역에 후보가 적은** 문제(D-01 의 나머지 절반·D-03)가 그대로 드러난다. `verify_status='verified'` 후보를 `places` 에 upsert 하면 쓸수록 좋아진다 | `local_catalog.link_place_ids()` 가 매칭 로직을 이미 갖고 있다 |
| **UR-32 기록 자동 유도** | 선순환의 «04 기록» 이 사용자의 자발성에만 걸려 있다. 캘린더(UR-28)가 열렸으니 이제 이어 붙일 자리가 생겼다 | 일정 종료 감지 → `app/visit.tsx` 노출 |


## 3. 그 다음 백로그

우선순위와 함께 [PROGRESS.md §3](PROGRESS.md) 에 있다. 이 세션에서 **의도적으로
미룬 것** 둘은 따로 적어 둔다 — 나중에 «왜 안 했지?» 로 되돌아오지 않게.

**~~C. 출발지 옆 장소가 1번으로 들어온다~~** — ✅ **2026-08-17 해결 (UR-18).**
`discovery._anchors()` 의 «출발지·도착지 사이 회랑» 은 의도된 설계라 그대로 두고,
그 위에 **시·도 판정**을 얹었다(`tools/region.py`). 회랑은 여전히 판교를 통과시키지만
시·도가 서울이 아니어서 걸린다. 상세는 §0.05.

**남은 것 — 같은 시·도 안에서 한 구간이 지나치게 먼 경우.** 서울 안에서
강서구↔강동구(직선 25km)는 시·도 판정도 반경 60km 도 못 잡는다. 구간별 이동시간
상한(예: 40분 초과 시 감점)이 반경보다 나을 수 있다. [PROGRESS.md §3](PROGRESS.md).

**~~분할 (`itinerary.py` · `router.py`)~~** — ✅ **2026-08-17 완료.** 상세는 §0.04.
예고대로 `test_documented_line_counts_are_current` 가 먼저 깨졌고, `STRUCTURE.md`
§15 의 ★ 표기를 새 구조로 바꿔 맞췄다.

---

## 4. 이어갈 사람이 먼저 읽을 것

| 상황 | 문서 |
|---|---|
| 무엇이 되고 안 되나 | [PROGRESS.md](PROGRESS.md) |
| 같은 실수 반복 방지 (증상→원인→조치 표) | [PROGRESS.md §4 · §5](PROGRESS.md) |
| 요청 하나가 어떻게 흐르나 | [STRUCTURE.md](STRUCTURE.md) |
| 이 기능이 어느 파일에 있나 | [FUNCTIONAL_MAP.md](FUNCTIONAL_MAP.md) |
| 기획안이 약속한 것과의 대조 | [PLANNING.md §7 · §8](PLANNING.md) |

**작업 규칙 두 개만 다시 강조한다** —
라이브러리(LangGraph·FastAPI·pgvector·Expo)를 건드리기 전에 **context7 로 현재 문서를 먼저 본다.**
그리고 **소스가 근거다.** 문서가 구현을 앞서 있던 항목이 실제로 여럿 있었다.

---

## 5. 인계 체크리스트

- [ ] `.env` 복원 (또는 재발급)
- [ ] `백엔드실행.bat` 기동 · 시드 투입
- [ ] `pytest -q` · `ruff check .` 통과 확인 → §0 갱신
- [ ] §2 에서 다음 작업 고르기
- [ ] 작업 후 §0 · §2 갱신 + [PROGRESS.md](PROGRESS.md) 반영
