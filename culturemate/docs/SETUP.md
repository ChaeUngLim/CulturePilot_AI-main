# 실행 준비 — 키 발급과 연결

빈 환경에서 앱이 실제로 돌 때까지의 순서입니다. **키 10종**을 받아 `.env` 두 개를
채우면 됩니다. 전부 무료 티어로 가능하고, 40~60분 걸립니다.

> 기능이 어느 키에 걸려 있는지는 [FUNCTIONAL_MAP.md](FUNCTIONAL_MAP.md),
> 왜 그 공급자를 골랐는지는 [ARCHITECTURE.md](ARCHITECTURE.md) 를 보세요.

---

## 0. 먼저 알아 둘 것

**키가 없어도 앱은 뜹니다.** 모든 외부 호출은 실패해도 그래프를 멈추지 않고
폴백으로 넘어갑니다. 다만 결과가 조용히 빕니다 — 그래서 `/diagnostics` 로
무엇이 죽어 있는지 먼저 확인하는 습관이 중요합니다.

**⚠️ 실제 키를 문서·소스에 적지 마세요.** 키는 `.env` 에만 둡니다(`.gitignore` 대상).
문서에 한 번 들어간 키는 지워도 이력에 남으므로 **재발급이 유일한 해결**입니다.

---

## 1. 키 목록 한눈에

| # | 키 | 용도 | 없으면 | 필수 |
|:--:|---|---|---|:--:|
| 1 | `NVIDIA_API_KEY` | 임베딩·리랭크·서술 생성 | 아카이브 검색 불가 | ✅ |
| 2 | `OPENAI_API_KEY` | 발화 이해(router)·판정(planner) | 규칙 폴백으로 동작 | ✅ |
| 3 | `DATA_GO_KR_KEY` | 공공 문화시설 | 네이버 지역검색으로 대체 | ✅ |
| 4 | `CULTURE_API_KEY` | KCISA 기간형 행사 | 웹검색으로 대체 | ✅ |
| 5 | `KMA_API_HUB_KEY` | 기상청 단기예보 | 날씨 보정 없음 | ✅ |
| 6 | `NAVER_CLIENT_ID/SECRET` | 좌표·자동차 경로 | 좌표 없으면 후보가 안 됨 | ✅ |
| 7 | `NAVER_SEARCH_CLIENT_ID/SECRET` | 주변 장소 검색 | 상시공간·식당 추천 불가 | ✅ |
| 8 | `ORS_API_KEY` | 도보·자전거 경로 | 거리 기반 추정 | 권장 |
| 9 | `ODSAY_API_KEY` | 지하철·버스 경로 + 노선 선형 | 거리 기반 추정 | 권장 |
| 10 | `TAVILY_API_KEY` / `EXA_API_KEY` | 공식정보 검증 | 전부 '확인 필요' 표시 | 권장 |

---

## 2. 발급 순서

### ① NVIDIA NIM — 임베딩·리랭크 (필수)

1. [build.nvidia.com](https://build.nvidia.com) 로그인
2. 아무 모델 클릭 → 우측 **[Get API Key]** → **[Generate Key]**
3. `nvapi-` 로 시작하는 키 복사

무료 크레딧 제공, 카드 등록 불필요.

```ini
LLM_BACKEND=nim
NVIDIA_API_KEY=nvapi-여기에-붙여넣으세요
MODEL_EMBED=                                   # 비움 → NIM 기본 1024차원
MODEL_RERANK=nvidia/llama-nemotron-rerank-1b-v2
```

> ⚠️ **리랭커 모델명은 카탈로그에 있어도 살아 있다는 뜻이 아닙니다.**
> `nv-rerankqa-mistral-4b-v3` → 404, `llama-3.2-nv-rerankqa-1b-v2` → EOL(410).
> 바꿀 때는 반드시 직접 호출해 보세요.

### ② OpenAI — 발화 이해 (필수)

[platform.openai.com](https://platform.openai.com) → API keys → Create.

```ini
OPENAI_API_KEY=sk-proj-...
MODEL_ROUTER=openai:gpt-4o-mini          # 발화 이해 — 정확도가 결과를 좌우
MODEL_PLANNER=openai:gpt-4o-mini         # 관련성 판정
MODEL_WRITER=meta/llama-3.1-8b-instruct  # 서술 생성 — 응답 시간의 절반
MODEL_FAST=openai:gpt-4o-mini            # 사실 추출
```

**역할별로 공급자를 섞는 게 기본값입니다.** `MODEL_*` 앞에 `openai:` 를 붙이면
그 역할만 유료로 갑니다. 근거는 [REQUIREMENTS.md §5.3](REQUIREMENTS.md) 에
실측값과 함께 있습니다.

> ⚠️ `meta/llama-3.3-70b-instruct` 는 무료 티어에서 구조화 출력이 30~60초 걸려
> 사실상 못 씁니다. 모델을 바꾸기 전에 `scripts/bench_models.py` 로 재 보세요.

### ③ 공공데이터포털 — 문화시설 (필수)

1. [data.go.kr](https://www.data.go.kr) 회원가입
2. **"문화시설"** 검색 → *한국문화정보원_전국문화시설정보* → **[활용신청]** (자동승인, 10,000회/일)
3. 마이페이지 → 오픈API → **일반 인증키(Decoding)** 복사

> **Encoding이 아니라 Decoding 키**입니다. 코드가 URL 인코딩을 직접 처리하므로
> Encoding 키를 넣으면 `%2F` 가 이중 인코딩되어 인증에 실패합니다.

```ini
DATA_GO_KR_KEY=발급받은_Decoding_키
CULTURE_FACILITY_ENDPOINT=https://apis.data.go.kr/B553457/nopenapi/rest/publicperformancedisplays
```

### ④ KCISA — 기간형 행사 (필수)

[culture.go.kr](https://www.culture.go.kr) → 문화체육관광부_문화예술공연(통합) 신청.
**포털 키와 다른 키입니다** — 섞으면 403(code 30)이 납니다.

```ini
CULTURE_API_KEY=KCISA_서비스키
```

### ⑤ 기상청 API허브 (필수)

[apihub.kma.go.kr](https://apihub.kma.go.kr) 가입 → 마이페이지에서 인증키 확인.

```ini
KMA_API_HUB_KEY=발급받은_인증키
```

> 발표 시각(02·05·08·11·14·17·20·23시) 직후 15분은 이전 회차가 나옵니다.

### ⑥ NCP Maps — 좌표·자동차 경로 (필수)

1. [console.ncloud.com/maps/application](https://console.ncloud.com/maps/application)
2. **[Application 등록]** → **Dynamic Map · Directions 5 · Geocoding · Reverse Geocoding** 체크
3. Web 서비스 URL에 `http://localhost:19006` 추가
4. **Client ID / Client Secret** 복사

```ini
# culturemate\.env  (서버 — Secret 포함)
NAVER_CLIENT_ID=Client_ID
NAVER_CLIENT_SECRET=Client_Secret
```
```ini
# culturemate\mobile\.env  (앱 — Client ID만!)
EXPO_PUBLIC_NAVER_MAP_KEY=Client_ID
EXPO_PUBLIC_NAVER_MAP_KEY_PARAM=ncpKeyId
```

> Secret을 `mobile\.env` 에 넣지 마세요. `EXPO_PUBLIC_*` 은 앱 번들에 그대로 박힙니다.

### ⑦ NAVER 검색 API — 주변 장소 (필수)

**⑥과 다른 사이트, 다른 자격증명입니다.**

1. [developers.naver.com/apps/#/register](https://developers.naver.com/apps/#/register)
2. 사용 API에서 **[검색]** 선택 → WEB 설정 → `http://localhost`

```ini
NAVER_SEARCH_CLIENT_ID=
NAVER_SEARCH_CLIENT_SECRET=
```

### ⑧ OpenRouteService — 도보·자전거 (권장)

[openrouteservice.org/dev](https://openrouteservice.org/dev) 가입 → API key.
무료 2,000회/일.

```ini
ORS_API_KEY=
```

### ⑨ ODsay — 지하철·버스 (권장)

[lab.odsay.com](https://lab.odsay.com) 가입 → API 신청.
**노선 선형(`loadLane`)까지 제공**하므로 지도에 실제 지하철 노선이 그려집니다.

```ini
ODSAY_API_KEY=
```

### ⑩ Tavily / Exa — 공식정보 검증 (권장)

- [app.tavily.com](https://app.tavily.com) — 무료 1,000회/월 (`tvly-` 로 시작)
- [exa.ai](https://exa.ai) — 폴백. 초당 10회 제한이 있어 연속 호출 시 429가 납니다

```ini
TAVILY_API_KEY=tvly-...
EXA_API_KEY=
```

---

## 3. 실행

**cmd 창**에서 (PowerShell 아님 — `%CD%` 가 확장되지 않습니다):

```bat
cd /d C:\Users\31\Documents\CulturePilot_AI\culturemate
백엔드실행.bat
```

배치 파일이 하는 일: 이전 컨테이너 정리 → 이미지 빌드 → 기동 → 시드 데이터 여부 확인.

직접 치실 거면:

```bat
docker rm -f culturemate
docker build -t culturemate-api .
docker run -d --name culturemate --env-file .env -e POSTGRES_USER=culturemate -e POSTGRES_PASSWORD=culturemate -e POSTGRES_DB=culturemate -p 8000:8000 -v culturemate_pgdata:/var/lib/postgresql -v "%CD%\app:/srv/app" -v "%CD%\scripts:/srv/scripts" -v "%CD%\db:/docker-entrypoint-initdb.d:ro" culturemate-api uvicorn app.api.main:app --host 0.0.0.0 --port 8000 --reload
```

> **컨테이너 하나에 PostgreSQL + API가 함께 들어 있습니다.** 베이스 이미지가
> `pgvector/pgvector:pg18` 이라 볼륨을 그대로 이어받습니다. PostgreSQL 기동에
> 10~20초 걸리니 `health` 가 뜰 때까지 기다리세요.
>
> `docker compose build` 를 쓰면 안 됩니다 — 이미지에 compose 라벨이 구워져
> Docker Desktop이 컨테이너를 프로젝트 그룹으로 접고 ID·포트가 `-` 로 보입니다.

앱은 **새 cmd 창**에서:

```bat
cd /d C:\Users\31\Documents\CulturePilot_AI\culturemate\mobile
npm start -- --web --port 19006
```

```ini
# culturemate\mobile\.env
EXPO_PUBLIC_API_URL=http://localhost:8000
EXPO_PUBLIC_USER_ID=00000000-0000-0000-0000-000000000001
```

> 폰에서 볼 때만 PC의 LAN IP(`ipconfig` 의 IPv4)로 바꿉니다. 다시 빌드하기 싫으면
> 앱 안에서 바꿔도 됩니다 — 헤더 오른쪽의 «● 연결됨 / ● 목 모드» 를 누르면
> **서버 연결** 화면이 열리고, 거기서 주소를 넣고 «연결 테스트» 로 확인한 뒤
> 저장하면 그 값이 `.env` 보다 우선합니다.
> `EXPO_PUBLIC_USER_ID` 는 **UUID여야 합니다** — 아니면 방문 기록 저장이
> 외래키 제약으로 실패합니다.

---

## 4. 확인

```bat
curl "http://localhost:8000/diagnostics?probe=true"
```

각 API를 실제로 한 번씩 호출합니다. 10개 전부 `ok: true` 면 정상입니다.

```
naver_geocode · naver_directions · weather · culture_api · culture_facility
naver_local_search · websearch · ors · odsay · llm
```

시드 데이터(장소 2,092곳 · 방문 기록 32건)를 넣으면 개인화가 동작합니다:

```bat
docker exec culturemate python scripts/seed_demo.py
```

실제 일정이 나오는지:

```bat
curl -X POST http://localhost:8000/chat/sync -H "Content-Type: application/json" -d "{\"user_id\":\"00000000-0000-0000-0000-000000000001\",\"thread_id\":\"t1\",\"message\":\"이번 주말 서울에서 하루 문화생활 일정 짜줘\"}"
```

---

## 5. 문제가 생기면

| 증상 | 확인 |
|---|---|
| 일정이 비어 있음 | `/diagnostics?probe=true` → `culture_api`·`culture_facility`·`naver_local_search` |
| 이동시간이 전부 `(추정)` | `ORS_API_KEY`·`ODSAY_API_KEY` 확인. 없으면 거리 기반으로 내려갑니다 |
| 지도에 직선만 그려짐 | 2단계 `POST /threads/{id}/routes` 가 실패. 앱이 자동 호출하므로 로그 확인 |
| 날씨가 안 뜸 | 발표 시각 직후 15분은 이전 회차 |
| 검증이 전부 '확인 필요' | Tavily·Exa 키 확인 |
| 방문 기록 저장 실패 | `EXPO_PUBLIC_USER_ID` 가 UUID인지, `users` 행이 있는지 |
| `체크포인터 연결 실패 → InMemorySaver` | PostgreSQL이 준비되기 전에 API가 떴습니다. `docker restart culturemate` |
| 다른 지역이 섞여 나옴 | 카탈로그가 비면 광역 API에 의존합니다. 시드를 넣으세요 |

로그는 이렇게 봅니다:

```bat
docker logs culturemate --tail 50
```

**외부 API 실패는 전부 `WARNING` 으로 남고 그래프는 계속 진행됩니다.**
결과가 조용히 비는 경우 여기부터 보세요.
