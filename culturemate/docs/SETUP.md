# 실행 준비 — 키 발급과 연결

빈 환경에서 앱이 실제로 돌 때까지의 순서입니다. **키 11종**을 받아 `.env` 두 개를
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
| 7 | `KAKAO_REST_API_KEY` | 주변 장소 **반경** 검색 (1순위) | ⑧로 폴백 — 동 단위라 경계 밖을 놓친다 | 권장 |
| 8 | `NAVER_SEARCH_CLIENT_ID/SECRET` | 주변 장소 검색 (⑦ 실패 시) | 상시공간·식당 추천 불가 | ✅ |
| 9 | `ORS_API_KEY` | 도보·자전거 경로 | 거리 기반 추정 | 권장 |
| 10 | `ODSAY_API_KEY` | 지하철·버스 경로 + 노선 선형 | 거리 기반 추정 | 권장 |
| 11 | `TAVILY_API_KEY` / `EXA_API_KEY` | 공식정보 검증 | 전부 '확인 필요' 표시 | 권장 |
| 12 | `TOUR_API_KEY` | 한국관광공사 TourAPI 4.0 | — (아직 배선 전) | 선택 |
| 13 | `MARKET_API_KEY` | 소상공인 상권정보 | — (아직 배선 전) | 선택 |

**키가 아닌데 반드시 있어야 하는 값 넷.** 비면 해당 기능이 조용히 꺼집니다.

| 값 | 무엇 | 비면 |
|---|---|---|
| `CULTURE_API_ENDPOINT` | KCISA 행사 API 요청 주소 | **키가 있어도** 행사가 웹검색 폴백으로 떨어진다 |
| `CULTURE_FACILITY_ENDPOINT` | 문화시설 Base URL | 시설 조회가 통째로 빈다 |
| `EMBED_DIM` | 임베딩 차원 (**1024 고정**) | 스키마 `vector(1024)` 와 어긋나면 삽입 실패 |
| `PG_DSN` | PostgreSQL 접속 문자열 | 저장·조회 전부 실패 |

> ⚠️ **`.env` 는 `culturemate/.env` 에 둡니다.** `백엔드실행.bat` 이 `--env-file .env` 를
> **자기 폴더 기준**으로 찾습니다. 저장소 루트에 두면 **키가 하나도 안 들어간 채** 컨테이너가
> 뜨고, 진단은 전부 «키 없음»으로 나옵니다. (2026-08-18 에 실제로 밟은 함정입니다.)

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

# 임베딩 — 이 모델은 기본 출력이 2048차원이라 dimensions=1024 를 넘겨야 한다.
# 그 처리는 provider.EMBED_DIM_CONFIGURABLE 이 모델별로 갈라서 한다.
MODEL_EMBED=nvidia/llama-nemotron-embed-1b-v2
EMBED_DIM=1024
MODEL_RERANK=nvidia/llama-nemotron-rerank-1b-v2
```

> ⚠️ **임베딩 차원은 1024 로 고정합니다. 2048 은 선택지가 아닙니다.**
> pgvector 의 HNSW 인덱스가 **2000차원 상한**이라, 2048 로 올리면 `idx_exp_embedding` 을
> 만들 수 없고 아카이브 검색이 순차 스캔으로 떨어집니다
> (실제 오류: `column cannot have more than 2000 dimensions for hnsw index`, pgvector 0.8.5).
> 기록이 쌓일수록 좋아진다는 이 서비스의 전제와 정면으로 충돌합니다.
>
> ⚠️ **임베딩 모델을 바꾸면 기존 벡터를 전량 재생성해야 합니다.** 벡터 공간이 달라져
> 옛 값과 새 값이 섞이면 검색 순위가 무너집니다.
>
> ⚠️ 고정 차원 모델(`nv-embedqa-e5-v5`)에 `dimensions` 를 넘기면 400 이 납니다.
> 새 모델을 쓸 때는 `provider.EMBED_DIM_CONFIGURABLE` 에 먼저 등록하세요.

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
CULTURE_FACILITY_ENDPOINT=https://apis.data.go.kr/B553457/nopenapi/rest/cultureartspaces
```

### ④ KCISA 문화공공데이터광장 — 기간형 행사 (필수)

[culture.go.kr](https://www.culture.go.kr) → 로그인 → 오픈API → **활용신청**.
**포털 키와 다른 키입니다** — 섞으면 403(code 30)이 납니다.

```ini
CULTURE_API_KEY=KCISA_서비스키
CULTURE_API_ENDPOINT=https://api.kcisa.kr/openapi/CNV_060/request
```

> ⚠️ **키만 넣고 주소를 비우면 행사가 통째로 웹검색 폴백으로 떨어집니다.**
> 진단에서 `culture_api` 가 `from_api: 0 · from_fallback: N` 으로 나오면 이 경우입니다.
>
> ⚠️ **KCISA 는 API 단위로 활용신청합니다.** 계정 키 하나로 전부 열리지 않습니다 —
> 신청한 API 는 200, 신청하지 않은 API 는 **401 Unauthorized** 가 옵니다.
>
> 주소를 모르면 `docker exec culturemate python scripts/find_culture_api.py` 로 훑습니다.
> 다만 이 스크립트의 후보 목록에 **접두사 없는 `CNV_060` 이 빠져 있어**(2026-08-18 확인)
> 못 찾을 수 있습니다. 그때는 발급처 마이페이지의 «활용신청 상세» 요청 URL 을 그대로 넣으세요.

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

### ⑦ 카카오 Local — 주변 장소 반경 검색 (권장)

주변 카페·식당·문화공간을 **좌표 기준 반경**으로 찾습니다.
NAVER 지역검색에는 반경 파라미터가 없어 좌표 → 주소 → 동(洞) 키워드로 우회했고,
그래서 앵커가 동 경계에 있으면 **200m 옆 가게가 목록에 아예 없었습니다.**
없으면 NAVER 로 내려가므로 기능은 돌지만 정확도가 떨어집니다.

1. [developers.kakao.com/console/app](https://developers.kakao.com/console/app) → 애플리케이션 추가
2. **[카카오맵] > [사용 설정] ON** ← 2024-12-01 이후 필수
3. [앱 키] → **REST API 키** 복사 (JavaScript/Admin 키 아님)

```ini
KAKAO_REST_API_KEY=
```

> 무료 100,000건/일 · 월 300만건. Bizwallet 을 연결하지 않으면 초과 시 과금이 아니라
> **호출이 막힙니다** — 의도치 않은 청구가 생기지 않습니다.
>
> ⚠️ 무료 쿼터는 개발자 계정의 **«첫 번째로 활성화한 앱»에만** 주어집니다.

### ⑧ NAVER 검색 API — 주변 장소 폴백 (필수)

**⑥과 다른 사이트, 다른 자격증명입니다.** ⑦(카카오)이 1순위이고 여기는 폴백이지만,
카카오 키가 막히면 주변 검색이 통째로 비므로 **둘 다 받아 둡니다.**

1. [developers.naver.com/apps/#/register](https://developers.naver.com/apps/#/register)
2. 사용 API에서 **[검색]** 선택 → WEB 설정 → `http://localhost`

```ini
NAVER_SEARCH_CLIENT_ID=
NAVER_SEARCH_CLIENT_SECRET=
```

### ⑨ OpenRouteService — 도보·자전거 (권장)

[openrouteservice.org/dev](https://openrouteservice.org/dev) 가입 → API key.
무료 티어 한도는 [REQUIREMENTS.md §5](REQUIREMENTS.md) 인벤토리 표에 한 곳으로 모아 두었다
— 발급 시점 기준이므로 정확한 값은 ORS 콘솔에서 확인한다.

```ini
ORS_API_KEY=
```

### ⑩ ODsay — 지하철·버스 (권장)

[lab.odsay.com](https://lab.odsay.com) 가입 → API 신청.
**노선 선형(`loadLane`)까지 제공**하므로 지도에 실제 지하철 노선이 그려집니다.

```ini
ODSAY_API_KEY=
```

### ⑪ Tavily / Exa — 공식정보 검증 (권장)

- [app.tavily.com](https://app.tavily.com) — 무료 1,000회/월 (`tvly-` 로 시작)
- [exa.ai](https://exa.ai) — 폴백. 초당 10회 제한이 있어 연속 호출 시 429가 납니다

```ini
TAVILY_API_KEY=tvly-...
EXA_API_KEY=
```

### ⑫ 한국관광공사 TourAPI 4.0 · 소상공인 상권정보 (선택 · 아직 배선 전)

둘 다 **공공데이터포털**에서 발급합니다. `DATA_GO_KR_KEY` 와 같은 인증키를 쓸 수 있지만
**활용신청은 서비스마다 따로** 해야 합니다 — 신청하지 않은 서비스는 같은 키로도
`code 30`(SERVICE_KEY_IS_NOT_REGISTERED_ERROR)이 돌아옵니다.

```ini
# 관광지·문화시설·축제 — data.go.kr 에서 "한국관광공사_국문 관광정보 서비스" 활용신청
TOUR_API_KEY=
TOUR_API_BASE_URL=https://apis.data.go.kr/B551011/KorService2

# 반경 내 상가업소·주요상권 — "소상공인시장진흥공단_상가(상권)정보" 활용신청
MARKET_API_KEY=
MARKET_API_BASE_URL=https://apis.data.go.kr/B553077/api/open/sdsc2
```

> ❗ **이 두 값은 지금 아무 코드도 읽지 않습니다.** `app/` 에 참조가 0건이라
> 채워 넣어도 동작이 달라지지 않고 `/diagnostics` 에도 나타나지 않습니다.
> 배선하려면 `tools/tour_api.py` 신설 + discovery·maps 연결 + 진단 프로브가 필요합니다.
>
> ⚠️ TourAPI 4.0 은 오퍼레이션 접미사가 `1 → 2` 로 바뀌었습니다
> (`areaBasedList2` · `searchKeyword2` · `locationBasedList2`). 예전 주소를 쓰면 빈 응답이 옵니다.

### ⑬ PostgreSQL 접속 (필수)

컨테이너 하나에 PostgreSQL 과 API 가 함께 들어 있어 로컬 소켓으로 붙습니다.
배치 파일이 사용자·비밀번호·DB 이름을 `-e` 로 넘기므로, 값을 바꿀 일은 거의 없습니다.

```ini
PG_DSN=
```

> 시드 데이터는 named volume(`culturemate_pgdata`)에 있어 **컨테이너를 지워도 남습니다.**
> `docker compose down -v` 와 `docker volume rm` 만 조심하면 됩니다.

---

## 3. 실행

**cmd 창**에서 (PowerShell 아님 — `%CD%` 가 확장되지 않습니다):

```bat
cd /d C:\Users\31\Documents\CulturePilot_AI-main\culturemate
백엔드실행.bat
```

배치 파일이 하는 일: 이전 컨테이너 정리 → 이미지 빌드 → 기동 → 시드 데이터 여부 확인.

직접 치실 거면 (WATCHFILES 두 개는 Windows 바인드 마운트에서 `--reload` 감시자가 죽지 않게 하는 값입니다 — 배치 파일과 동일):

```bat
docker rm -f culturemate
docker build -t culturemate-api .
docker run -d --name culturemate --env-file .env -e POSTGRES_USER=culturemate -e POSTGRES_PASSWORD=culturemate -e POSTGRES_DB=culturemate -e WATCHFILES_FORCE_POLLING=true -e WATCHFILES_POLL_DELAY=2 -p 8000:8000 -v culturemate_pgdata:/var/lib/postgresql -v "%CD%\app:/srv/app" -v "%CD%\scripts:/srv/scripts" -v "%CD%\db:/docker-entrypoint-initdb.d:ro" culturemate-api uvicorn app.api.main:app --host 0.0.0.0 --port 8000 --reload
```

> **컨테이너 하나에 PostgreSQL + API가 함께 들어 있습니다.** 베이스 이미지가
> `pgvector/pgvector:pg18` 이라 볼륨을 그대로 이어받습니다. PostgreSQL 기동에
> 10~20초 걸리니 `health` 가 뜰 때까지 기다리세요.
>
> `docker compose build` 를 쓰면 안 됩니다 — 이미지에 compose 라벨이 구워져
> Docker Desktop이 컨테이너를 프로젝트 그룹으로 접고 ID·포트가 `-` 로 보입니다.

앱은 **새 cmd 창**에서:

```bat
cd /d C:\Users\31\Documents\CulturePilot_AI-main\culturemate\mobile
npm start -- --web --port 19006
```

```ini
# culturemate\mobile\.env
EXPO_PUBLIC_API_URL=http://localhost:8000
EXPO_PUBLIC_USER_ID=00000000-0000-0000-0000-000000000001
```

> 위 주소는 **PC 웹 브라우저 전용**입니다. 폰에서 열려면 §3.1 을 보세요.
> `EXPO_PUBLIC_USER_ID` 는 **UUID여야 합니다** — 아니면 방문 기록 저장이
> 외래키 제약으로 실패합니다.

### 3.1 폰(Expo Go)에서 실행 — 같은 Wi-Fi

**공유기 관리자 로그인도, 포트포워딩도 필요 없습니다.** 폰이 PC와 같은 Wi-Fi 에
붙어 있기만 하면 됩니다. 같은 랜 안에서 서로 부르는 것이지 외부에 여는 게 아닙니다.

**① PC 의 LAN IP 를 확인합니다.**

```bat
ipconfig
```

`IPv4 주소` 중 `192.168.x.x` 또는 `10.x.x.x` 로 시작하는 것을 씁니다.
`172.x` 로 시작하는 것은 Docker·WSL 가상 어댑터라 **폰에서 못 닿습니다** — 고르지 마세요.
어댑터가 여럿이면 폰이 붙은 Wi-Fi 와 **앞 세 자리가 같은** 것을 고릅니다
(폰 IP 가 `192.168.10.x` 면 PC 도 `192.168.10.x`).

**② `culturemate\mobile\.env` 를 그 주소로 바꿉니다.**

```ini
EXPO_PUBLIC_API_URL=http://192.168.10.21:8000
#EXPO_PUBLIC_API_URL=http://localhost:8000   # PC 웹 브라우저용
```

`localhost` 를 그대로 두면 **앱은 뜨는데 API 만 전부 실패**합니다. 폰 입장에서
`localhost` 는 PC 가 아니라 폰 자신이기 때문입니다.

**③ 앱을 띄웁니다.** 포트를 지정하는 이유는 아래 표 참고.

```bat
cd /d C:\Users\31\Documents\CulturePilot_AI-main\culturemate\mobile
npx expo start --port 19000
```

**④ 폰의 Expo Go 로 QR 을 스캔합니다.**

#### 폰에서 「Checking for new update…」에서 멈춘다면

게이지가 안 올라가면 **번들을 못 받고 있는 것**입니다. 순서대로 봅니다.

1. **cmd 창이 살아 있나.** 「계속하려면 아무 키나 누르십시오」가 보이면 그건
   `.bat` 의 `pause` 이고, **Metro 는 이미 종료된 상태**입니다. 번들이 99% 에서
   멈춘 게 아니라 거기서 죽은 겁니다.
2. **폰 브라우저로 `http://<PC IP>:8000/health` 를 열어 봅니다.**
   JSON 이 보이면 백엔드까지 닿는 것이고, 안 열리면 방화벽입니다(아래).
3. **폰과 PC 가 같은 Wi-Fi 인지.** 5GHz/2.4GHz 를 다른 SSID 로 쓰는 공유기면
   갈릴 수 있습니다. 게스트 네트워크는 기기 간 통신이 막혀 있어 안 됩니다.

#### Windows 방화벽

기본 인바운드 정책이 차단이라 처음 실행할 때 **「Windows 보안 경고」** 창이 뜹니다.
**「액세스 허용」을 누르면** 규칙이 자동으로 만들어집니다. 실수로 「취소」를 눌렀거나
창이 안 떴다면, **관리자 권한 cmd** 에서 직접 엽니다.

```bat
netsh advfirewall firewall add rule name="CultureMate API 8000" dir=in action=allow protocol=TCP localport=8000
netsh advfirewall firewall add rule name="CultureMate Expo 19000" dir=in action=allow protocol=TCP localport=19000
```

#### 포트를 지정하는 이유

| 포트 | 무엇 | 비고 |
|---|---|---|
| 8000 | 백엔드 | 도커가 `0.0.0.0` 으로 공개하므로 LAN 에서 보입니다 |
| 19000 | Expo (권장) | |
| 8081 | Expo 기본값 | **피하는 편이 낫습니다.** 일부 Windows 환경에서 예약 포트 구간(8075~8174)에 걸려 `Port 8081 is reserved by the OS` 로 죽습니다 |

#### 다른 망이거나 방화벽을 못 여는 경우 — 터널

```bat
cd /d C:\Users\31\Documents\CulturePilot_AI-main\culturemate
앱실행-터널.bat
```

`cloudflared` 로 백엔드를 외부 주소로 열고, 그 주소를 `.env` 에 **자동으로** 써넣은 뒤
`expo start --tunnel` 을 실행합니다(`mobile/scripts/start-tunnel.mjs`).
터널 주소는 실행할 때마다 바뀌므로 손으로 옮겨 적지 마세요.
`cloudflared` 가 없으면 `winget install --id Cloudflare.cloudflared`.

#### 어댑터가 여러 개인 PC — `hostUri` 가 `127.0.0.1` 로 나가는 문제

폰이 「Checking for new update…」에서 멈추는 원인 중 **가장 찾기 어려운 것**입니다.
Expo 는 매니페스트에 «번들을 어디서 받아라»를 적어 보내는데, 랜카드가 많으면
(유선·무선·WSL·Bluetooth·169.254 자동할당) 주소를 못 고르고 `127.0.0.1` 을 적습니다.
폰 입장에서 그건 **폰 자신**이라 영원히 못 받습니다. 접속은 되는데 게이지가 안 올라갑니다.

확인:

```bat
curl -H "Expo-Platform: android" -H "Accept: application/expo+json,application/json" http://localhost:19000
```

응답의 `hostUri` 가 `127.0.0.1` 이면 이 경우입니다. **호스트명을 직접 박아** 띄웁니다.

```bat
set REACT_NATIVE_PACKAGER_HOSTNAME=192.168.10.21
npx expo start --host lan --port 19000
```

#### `.env` 를 안 고치고 앱 안에서 바꾸기

헤더 오른쪽의 «● 연결됨 / ● 목 모드» 를 누르면 **서버 연결** 화면이 열립니다.
주소를 넣고 «연결 테스트» 로 확인한 뒤 저장하면 그 값이 `.env` 보다 우선합니다.
재빌드가 필요 없어 실기기·터널처럼 주소가 매번 바뀌는 환경에서 편합니다.

> ⚠️ **저장된 주소가 `.env` 를 이깁니다.** `app/_layout.tsx` 가 기동 때
> `if (stored !== null) setApiUrl(stored)` 로 덮어씁니다. 한 번 저장하면 `.env` 를
>아무리 고쳐도 안 바뀌므로, 목 모드에서 못 빠져나오면 **여기서** 고쳐야 합니다.
>
> 터널은 주소가 실행할 때마다 바뀌므로, 다시 켤 때마다 폰에 옛 주소가 남아
> **또 목 모드로 뜹니다.** LAN 주소는 고정이라 한 번만 저장하면 됩니다.

---

## 4. 확인

```bat
curl "http://localhost:8000/diagnostics?probe=true"
```

각 API를 실제로 한 번씩 호출합니다. **프로브 11종**이 전부 `ok: true` 면 정상입니다.

```
naver_geocode · naver_directions · weather · culture_api · culture_facility
kakao_local · naver_local_search · websearch · ors · odsay · llm
```

> `naver_local_search` 의 `served_by` 를 함께 봅니다 — `kakao` 면 카카오가 답한 것이고,
> `naver` 면 카카오가 실패했거나 키가 없어 폴백으로 내려간 것입니다.

> `TOUR_API_KEY` · `MARKET_API_KEY` 는 배선 전이라 여기 나타나지 않습니다(§2 ⑫).
>
> **`culture_api` 는 `ok: true` 여도 한 번 더 봅니다.** `from_api` 가 0 이고
> `from_fallback` 이 N 이면 키·주소가 아니라 **웹검색이 대신 답한 것**입니다 —
> `CULTURE_API_ENDPOINT` 를 확인하세요(§2 ④).
>
> **`.env` 를 고쳤으면 컨테이너를 재생성해야 합니다.** `docker restart` 로는 반영되지 않습니다.

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
