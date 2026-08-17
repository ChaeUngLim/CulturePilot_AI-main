/**
 * 서버(FastAPI + LangGraph) 스키마 미러.
 * app/schemas.py 의 pydantic 모델과 1:1로 대응한다. 서버가 바뀌면 여기만 고친다.
 */

export type GeoPoint = { lat: number; lng: number; name?: string | null };

export type FrictionTag =
  | 'parking' | 'crowding' | 'accessibility' | 'waiting'
  | 'noise' | 'cost' | 'reservation' | 'transit' | 'weather';

export type Parking = 'free' | 'paid' | 'nearby' | 'none' | 'unknown';
/**
 * best = 구간마다 가장 빠른 수단을 조합한다(최단루트).
 * '지하철+버스'는 없다 — 수단이 아니라 조합이고, 섞는 건 best 가 한다.
 */
export type Transport = 'best' | 'walk' | 'subway' | 'bus' | 'car' | 'bike';
/** 이 구간을 계산한 엔진 */
export type TravelSource = 'naver' | 'ors' | 'odsay' | 'estimate';
/**
 * 공식정보 대조 결과.
 *   verified    공식 출처와 일치
 *   needs_check 정보가 부족해 확인하지 못함 — 버리지 않고 '확인 필요'로 노출한다
 *   excluded    불일치·종료
 *   unknown     아직 대조하지 않음(2단계 검증 전)
 */
export type VerifyStatus = 'verified' | 'needs_check' | 'excluded' | 'unknown';

/** 서버가 발화에서 해석한 조건 중 화면 컨트롤과 1:1로 대응하는 값 */
export type Resolved = {
  transport?: Transport | 'unknown' | null;
  regions?: string[];
  landmark?: string | null;
  origin_name?: string | null;
  destination_name?: string | null;
  /** "09:00" */
  start_time?: string | null;
  end_time?: string | null;
  stop_count?: number | null;
  dwell_min?: number | null;
  dwell_max?: number | null;
  indoor_pref?: string | null;
};

export type ItineraryItem = {
  seq: number;
  /** 사용자가 시각을 지정한 항목 — 스케줄러가 옮기지 않는다 */
  fixed_time?: boolean;
  parking?: Parking;
  parking_note?: string | null;
  purpose?: 'culture' | 'meal' | 'cafe' | 'rest' | 'any';
  candidate_id?: string | null;
  place_id?: string | null;
  name: string;
  kind: string;
  geo?: GeoPoint | null;
  arrive?: string | null;
  depart?: string | null;
  dwell_min: number;
  travel_min_from_prev: number;
  /** 직전 장소와의 거리(km) */
  travel_km_from_prev?: number | null;
  travel_source?: TravelSource;
  travel_transfers?: number | null;
  /** 대중교통 요금 · 자동차 통행료(원) */
  travel_fare?: number | null;
  /**
   * 직전 장소에서 여기까지의 실제 경로 선형. `[[lng, lat], …]`
   * 비어 있으면 실측하지 못한 구간이라, 지도는 직선을 옅게 그린다.
   */
  travel_path?: [number, number][];
  /**
   * 공식정보 대조 결과. 첫 응답에서는 대개 `unknown` 이고(서버 예산이 모자라
   * 검증을 건너뛴다) `POST /threads/{id}/verify` 가 뒤이어 채운다.
   */
  verify_status?: VerifyStatus;
  transport?: string | null;
  indoor?: boolean | null;
  reason?: string | null;
  evidence_ids: string[];
};

export type Itinerary = {
  id: string;
  date?: string | null;
  items: ItineraryItem[];
  total_travel_min: number;
  total_dwell_min: number;
  map_path: GeoPoint[];
  version: number;
  notes: string[];
  /** 어떤 기준으로 계산했는지. best 면 구간마다 수단이 섞여 있다. */
  transport_mode?: string;
  /** 수단별 소요시간(분) 합 — "도보 22분 · 지하철 31분" */
  transport_mix?: Record<string, number>;
  /** 대중교통 요금 · 통행료 합계(원) */
  total_fare?: number;
  /** 하루의 양 끝. 방문할 장소가 아니라서 items 와 따로 온다. */
  origin?: GeoPoint | null;
  destination?: GeoPoint | null;
  origin_name?: string | null;
  destination_name?: string | null;
};

export type AdvisoryOption = {
  id: string;
  label: string;
  action:
    | 'keep' | 'replace' | 'add_parking' | 'change_transport'
    | 'reorder' | 'shift_time' | 'drop' | 'add_place';
  payload: Record<string, unknown>;
  predicted_effect?: string | null;
};

export type Advisory = {
  id: string;
  kind: 'friction' | 'revisit_diff' | 'conflict' | 'weather' | 'budget';
  title: string;
  message: string;
  place_id?: string | null;
  target_seq?: number | null;
  severity: number;
  evidence_ids: string[];
  options: AdvisoryOption[];
};

export type Evidence = {
  id: string;
  kind: 'archive' | 'official' | 'web' | 'weather' | 'maps' | 'profile' | 'rule';
  title: string;
  text: string;
  url?: string | null;
  ref?: string | null;
  observed_at?: string | null;
  confidence: number;
};

export type Decision = { advisory_id: string; option_id: string; note?: string | null };

export type InterruptPayload = {
  type: 'confirm_plan_changes';
  itinerary: Itinerary | null;
  advisories: Advisory[];
  evidence: Evidence[];
  instruction: string;
  /** 확인 카드가 떠도 화면 상단 조건 칩은 갱신돼야 한다.
   *  예전에는 done 응답에만 있어서, 카드가 뜨는 순간 칩이 이전 값에 멈췄다. */
  resolved?: Resolved | null;
};

/** POST /chat/sync · /resume/sync 응답 */
export type SyncResult =
  | { status: 'interrupted'; interrupt: InterruptPayload }
  | {
      status: 'done';
      answer: string;
      itinerary: Itinerary | null;
      evidence_ids: string[];
      advisories: Advisory[] | null;
      resolved?: Resolved | null;
    };

/** SSE 이벤트 (POST /chat) */
export type StreamEvent =
  | { type: 'token'; text: string }
  | { type: 'update'; node: string; keys: string[] }
  | { type: 'interrupt'; payload: InterruptPayload }
  | {
      type: 'done'; answer: string; itinerary: Itinerary | null;
      evidence: Evidence[]; resolved?: Resolved | null;
    }
  | { type: 'error'; message: string };

export type VisitInput = {
  user_id: string;
  place_id: string;
  plan_id?: string | null;
  rating?: number | null;
  review?: string | null;
  friction: FrictionTag[];
  companions?: string | null;
  transport?: string | null;
  photos: string[];
};

// ------------------------------------------------------------ 취향 카드
/**
 * 카드 한 장의 판단 (UR-01 · UR-31).
 *
 * `verdict` 4값과 `experienced` 의 조합이 기획안 2.4-③의 3지 반응이다.
 * 서버 스키마(`preference_cards`)의 CHECK 제약과 같은 4값이라 여기서 줄이지 않는다 —
 * 줄이면 앱이 못 보내는 값을 DB만 알고 있게 된다.
 */
export type Verdict = 'recommend' | 'dislike' | 'interested' | 'not_interested';

export type PreferenceCard = {
  subject: string;
  verdict: Verdict;
  experienced: boolean;
  created_at?: string | null;
};

export type TasteReport = {
  stats: {
    preferred_categories: Record<string, number>;
    indoor_bias: number;
    avg_travel_min: number | null;
    avg_dwell_min: number | null;
    novelty_bias: number;
    friction_sensitivity: Record<string, number>;
  };
  narrative: string;
};

// ---------------------------------------------------------------- 큐레이션
export type CuratedPlace = {
  place_id: string;
  name: string;
  category?: string | null;
  address?: string | null;
  region?: string | null;
  lat: number;
  lng: number;
  indoor?: boolean | null;
  parking?: Parking | null;
  parking_note?: string | null;
  url?: string | null;
  visits: number;
  rating?: number | null;
  dwell_min?: number | null;
  friction: string[];
  last_visit?: string | null;
  /** 왜 이 컬렉션에 들어왔는지 */
  reason: string;
};

export type Collection = {
  key: string;
  title: string;
  subtitle: string;
  emoji: string;
  count: number;
  places: CuratedPlace[];
  /** 사용자가 직접 담은 컬렉션인지. 자동 테마와 다루는 방식이 다르다. */
  mine?: boolean;
  collection_id?: string;
};

/**
 * 캘린더 목록의 한 칸 (UR-28).
 *
 * ★ `Itinerary` 전문이 아니라 **요약만** 온다. 하나가 수십 KB라 한 달치를 그대로
 * 받으면 목록을 여는 것만으로 페이로드가 터진다. 날짜를 눌렀을 때
 * `fetchPlan(id)` 이 그날 일정 전체를 따로 받는다.
 */
export type PlanSummary = {
  id: string;
  plan_date: string | null;
  status: string;
  stop_count: number | null;
  first_place: string | null;
  destination_name: string | null;
  starts_at: string | null;
  updated_at: string | null;
};
