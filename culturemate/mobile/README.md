# CultureMate — React Native 앱

Expo(SDK 57) + expo-router. **백엔드 없이도 전체 플로우가 돌아갑니다.**

```bash
npm install
npm start          # QR 스캔 → Expo Go — .env 없이 켜면 목(mock) 모드
```

`.env` 파일은 저장소에 없습니다(`.env.example` 도 없습니다). `EXPO_PUBLIC_API_URL`이
비어 있으면 목 모드로 동작하고, FastAPI를 띄웠다면 `mobile/.env` 를 직접 만들어 값을
채우면 그대로 실서버로 붙습니다 (앱 코드는 그대로). 주소는 앱 안의 **서버 연결
화면(`app/connect.tsx`)에서 런타임에도** 바꿀 수 있습니다.

```bash
# mobile/.env — 실기기는 localhost가 아니라 PC의 LAN IP
EXPO_PUBLIC_API_URL=http://192.168.0.10:8000
```

---

## 화면

| 탭 | 하는 일 |
|---|---|
| **오늘** | 요청 입력 → 노드 진행 표시 → 일정 타임라인 + 지도 → **확인 카드** → 선택 반영 |
| **캘린더** | 월 그리드 → 날짜 탭 → 그날 일정(타임라인+지도) · 기록 없는 지난 일정은 «기록 남기기»로 (UR-28) |
| **아카이브** | 방문 기록 목록, 오늘 일정에서 바로 기록 추가 (별점·불편 태그·사진) |
| **큐레이션** | 내 방문 기록에서 뽑은 테마 지도, 컬렉션 저장·이동수단 재계산 |
| **취향** | 선호 카테고리, 실내/야외·재방문/신규 성향, 주요 불편 요소 |

모달 화면: `visit`(기록 입력) · `connect`(서버 연결) · `taste-cards`(취향 카드 스와이프, UR-01).

핵심 화면을 라우팅으로 쪼개지 않은 이유: 사용자는 "일정 생성 → 경고 확인 → 선택 → 일정 변경"을
**하나의 사건**으로 경험합니다. 화면을 나누면 무엇 때문에 일정이 바뀌었는지 맥락이 사라집니다.

---

## 서버 계약에서 중요한 세 가지

**1. 스트리밍 — RN fetch는 본문 스트리밍을 못 한다**

RN의 네트워킹은 XHR 기반이라 응답 본문을 전부 받은 뒤 넘깁니다. `react-native-sse`(XHR
`onprogress` 폴리필)로 SSE를 받고, 폴리필이 막힌 환경에서는 `/chat/sync`로 폴백합니다.
두 경로 모두 같은 `thread_id`를 쓰므로 서버 상태 모델은 하나입니다.

**2. HITL — 인터럽트가 서버에 있어서 연결이 끊겨도 된다**

`interrupt()`가 체크포인터에 상태를 남기므로, 앱이 백그라운드로 내려가 스트림이 끊겨도
`GET /threads/{id}/state`로 정확히 그 지점을 복원합니다. 모바일에서 연결 단절은 예외가
아니라 상시 조건이라, HITL을 클라이언트 모달 상태가 아니라 서버 인터럽트로 구현한 게
여기서 값을 합니다.

**3. GPS — `conditions_override`로 주입**

`gap_fill`(일정 조기 종료) 라우트는 현재 위치가 없으면 성립하지 않습니다. LLM이 발화에서
추출할 수 없는 값이므로 네이티브에서 얻어 `conditions_override.origin`으로 보내고,
서버는 이를 추출값보다 **우선** 적용합니다.

---

## 지도

네이버 지도 JS SDK를 WebView에 띄웁니다. 서버는 지도 공급자를 모릅니다 — `map_path`
좌표와 `travel_min_from_prev`만 내려주고 렌더링은 전적으로 클라이언트 책임이라,
지도를 갈아끼워도 서버 계약은 그대로입니다.

키 파라미터명이 콘솔 세대에 따라 `ncpKeyId` / `ncpClientId`로 갈립니다. 첫 로드가 실패하면
반대쪽 이름으로 한 번 더 시도하고, 그래도 안 되면 좌표 목록 폴백을 보여줍니다.

```bash
EXPO_PUBLIC_NAVER_MAP_KEY=발급받은_키
EXPO_PUBLIC_NAVER_MAP_KEY_PARAM=ncpKeyId   # 구 콘솔이면 ncpClientId
```

---

## 검증

```bash
npm run typecheck   # tsc --noEmit
npm run verify      # HITL 계약 회귀 테스트 (17항목, 백엔드·시뮬레이터 불필요)
npm run export      # 웹 번들 빌드로 전체 컴파일 확인
```

`npm run verify`가 검사하는 건 UI가 아니라 **계약**입니다.

- 노드 진행 → 인터럽트 → 재개 → 재계획 순서
- 첫 선택지가 항상 '유지' — 변경이 기본값이면 사실상 자동 변경 승인이 됩니다
- 모든 선택지에 예상 효과가 붙는가
- 일정의 시간 정합성(이동시간 확보)
- '유지'만 골랐을 때 불필요한 재계획이 없는가

---

## 구조

```
mobile/
├── app/                        # expo-router
│   ├── _layout.tsx
│   ├── (tabs)/{index,calendar,archive,curation,report}.tsx
│   ├── visit.tsx               # 관람 기록 입력 (모달)
│   ├── connect.tsx             # 서버 연결 — 주소 확인·저장·목 모드 전환 (모달)
│   └── taste-cards.tsx         # 취향 카드 스와이프 (UR-01)
├── src/
│   ├── api/{client,mock,types}.ts   # SSE+sync 클라이언트 / 목 백엔드 / 서버 스키마 미러
│   ├── components/                  # Timeline · AdvisoryCard · EvidenceSheet · NaverMap · Composer …
│   ├── hooks/                       # useCultureMate.ts · useCurrentLocation.ts · context.tsx
│   ├── store/storage.ts             # 오프라인 캐시 + 미동기화 기록 큐
│   └── {config,theme,constants}.ts
└── scripts/{verify-flow,start-tunnel}.mjs
```

`src/api/types.ts`는 서버 `app/schemas.py`의 미러입니다. 서버 스키마가 바뀌면 여기만 고칩니다.
