/**
 * 목(mock) 백엔드.
 *
 * LangGraph 그래프의 '관측 가능한 행동'만 흉내 낸다 — 노드 진행 순서, HITL 인터럽트,
 * 재개 후 재계획. 화면 코드가 목/실서버를 구분하지 않도록 이벤트 형태를 동일하게 맞췄다.
 */
import type {
  Advisory, Evidence, InterruptPayload, Itinerary, ItineraryItem, PreferenceCard,
  StreamEvent, SyncResult, TasteReport,
} from './types';

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const uid = () => Math.random().toString(36).slice(2, 10);

type Seed = {
  place_id: string; name: string; kind: string; category: string;
  lat: number; lng: number; indoor: boolean; dwell: number; reason: string;
};

const SEEDS: Seed[] = [
  { place_id: 'p-daelim', name: '대림미술관', kind: 'venue', category: '미술관', lat: 37.5757, lng: 126.9709, indoor: true, dwell: 80, reason: '취향 프로필의 최상위 카테고리(미술관 0.31) · 공식정보 검증됨' },
  { place_id: 'p-thnx', name: '땡스북스', kind: 'shop', category: '독립서점', lat: 37.5563, lng: 126.9236, indoor: true, dwell: 40, reason: '과거 만족 기록(별점 4.5) · 이동 12분' },
  { place_id: 'p-ddp', name: 'DDP 디자인전시', kind: 'event', category: '전시', lat: 37.5665, lng: 127.0092, indoor: true, dwell: 90, reason: '기간형 행사 · 관심사 "디자인" 일치' },
  { place_id: 'p-seongsu', name: '성수연방', kind: 'venue', category: '복합문화공간', lat: 37.5446, lng: 127.0557, indoor: true, dwell: 60, reason: '악천후 시간대 실내 우선 배치' },
  { place_id: 'p-onion', name: '어니언 성수', kind: 'cafe', category: '카페', lat: 37.5443, lng: 127.0559, indoor: true, dwell: 45, reason: '90분 공백을 휴식 목적으로 채움 · 도보 3분' },
  { place_id: 'p-arario', name: '아라리오뮤지엄', kind: 'venue', category: '미술관', lat: 37.5720, lng: 126.9860, indoor: true, dwell: 70, reason: '대안 후보 · 도보권 주차장 2곳' },
];

const at = (h: number, m: number) => {
  const d = new Date();
  d.setHours(h, m, 0, 0);
  return d.toISOString();
};

function buildItinerary(seeds: Seed[], startHour = 11): Itinerary {
  let cursor = startHour * 60;
  const items: ItineraryItem[] = seeds.map((s, i) => {
    const travel = i === 0 ? 18 : 8 + i * 3;
    cursor += travel;
    const arrive = cursor;
    cursor += s.dwell;
    return {
      seq: i + 1,
      place_id: s.place_id,
      name: s.name,
      kind: s.kind,
      geo: { lat: s.lat, lng: s.lng, name: s.name },
      arrive: at(Math.floor(arrive / 60), arrive % 60),
      depart: at(Math.floor(cursor / 60), cursor % 60),
      dwell_min: s.dwell,
      travel_min_from_prev: travel,
      transport: 'subway',
      indoor: s.indoor,
      reason: s.reason,
      evidence_ids: i === 0 ? ['ev-archive-1'] : [],
    };
  });
  return {
    id: `plan-${uid()}`,
    date: new Date().toISOString().slice(0, 10),
    items,
    total_travel_min: items.reduce((a, b) => a + b.travel_min_from_prev, 0),
    total_dwell_min: items.reduce((a, b) => a + b.dwell_min, 0),
    map_path: items.map((i) => i.geo!),
    version: 1,
    notes: [],
  };
}

const EVIDENCE: Evidence[] = [
  {
    id: 'ev-archive-1', kind: 'archive', title: '대림미술관 · 2025-11-16 방문',
    text: '가족과 차로 방문. 지하 주차장이 좁아 입차 대기 20분. 전시 자체는 만족(별점 4).',
    confidence: 0.86, observed_at: '2025-11-16T14:20:00Z', ref: 'visit:8f21',
  },
  {
    id: 'ev-official-1', kind: 'official', title: '대림미술관 공식 운영 안내',
    text: '화–일 10:00–18:00, 월요일 휴관. 주차 공간 협소로 대중교통 이용 권장.',
    url: 'https://www.daelimmuseum.org', confidence: 0.9, observed_at: new Date().toISOString(),
  },
  {
    id: 'ev-weather-1', kind: 'weather', title: '시간대별 예보',
    text: '15–17시 강수확률 70%, 기온 24°C. 야외 활동 부적합 구간.',
    confidence: 0.75,
  },
  {
    id: 'ev-maps-1', kind: 'maps', title: '이동시간 행렬',
    text: '5개 지점, mode=subway. 총 이동 45분.', confidence: 0.9,
  },
  {
    id: 'ev-rule-1', kind: 'rule', title: '검증 규칙 past_friction',
    text: '일정에 포함된 장소에 사용자의 불편 기록(parking)이 존재합니다.', confidence: 0.9,
  },
];

function buildAdvisories(): Advisory[] {
  return [
    {
      id: 'adv-parking',
      kind: 'friction',
      title: '대림미술관 · 과거 주차 불편 기록',
      message:
        '작년 11월 차로 방문했을 때 지하 주차장 입차에 20분 대기했다고 기록하셨어요. 이번에도 차량 이동 일정입니다.',
      place_id: 'p-daelim',
      target_seq: 1,
      severity: 2,
      evidence_ids: ['ev-archive-1', 'ev-rule-1'],
      options: [
        { id: 'o-keep', label: '그대로 방문', action: 'keep', payload: {}, predicted_effect: '일정 변동 없음. 같은 대기가 재발할 수 있음' },
        { id: 'o-park', label: '인근 주차장 동선에 추가', action: 'add_parking', payload: { place_id: 'p-daelim' }, predicted_effect: '도보 5분 공영주차장 경유, 대기 위험 감소' },
        { id: 'o-transit', label: '대중교통으로 변경', action: 'change_transport', payload: { transport: 'subway' }, predicted_effect: '주차 문제 제거, 총 이동 +12분' },
        { id: 'o-replace', label: '비슷한 미술관으로 교체', action: 'replace', payload: { place_id: 'p-daelim' }, predicted_effect: '아라리오뮤지엄으로 대체 후 동선 재계산' },
      ],
    },
    {
      id: 'adv-weather',
      kind: 'weather',
      title: '15–17시 강수 예보',
      message: '오후 시간대 강수확률 70%입니다. 해당 구간에 야외 이동이 포함돼 있습니다.',
      target_seq: 3,
      severity: 2,
      evidence_ids: ['ev-weather-1'],
      options: [
        { id: 'o-w-keep', label: '그대로 진행', action: 'keep', payload: {}, predicted_effect: '일정 변동 없음' },
        { id: 'o-w-indoor', label: '실내 중심으로 재배치', action: 'reorder', payload: {}, predicted_effect: '실내 장소를 오후로 이동, 순서 재계산' },
      ],
    },
  ];
}

const NODE_SCRIPT: { node: string; keys: string[]; delay: number }[] = [
  { node: 'classify', keys: ['request_type', 'conditions', 'flags'], delay: 320 },
  { node: 'archive', keys: ['archive_hits', 'taste_profile', 'advisories'], delay: 520 },
  { node: 'discovery', keys: ['candidates', 'verifications'], delay: 560 },
  { node: 'merge_context', keys: ['context'], delay: 200 },
  { node: 'itinerary', keys: ['itinerary', 'gaps', 'nearby'], delay: 620 },
  { node: 'validation', keys: ['issues', 'advisories', 'needs_user_confirm'], delay: 420 },
];

type Session = { itinerary: Itinerary; advisories: Advisory[]; awaiting: boolean };
const sessions = new Map<string, Session>();

function wantsPlan(message: string) {
  return /일정|코스|하루|플랜|짜줘|추천/.test(message);
}

/** 지역별 시드 — 지역 칩을 눌렀을 때 목 모드에서도 다른 결과가 나오게 한다. */
const REGION_SEEDS: Record<string, Seed[]> = {
  '강남': [
    { place_id: 'p-coex', name: '별마당도서관', kind: 'venue', category: '복합문화공간', lat: 37.5090, lng: 127.0596, indoor: true, dwell: 50, reason: '실내 선호 반영 · 도보 접근' },
    { place_id: 'p-platform', name: '플랫폼엘 컨템포러리', kind: 'venue', category: '미술관', lat: 37.5237, lng: 127.0369, indoor: true, dwell: 60, reason: '과거 만족 기록(별점 4.0) · 한산함' },
    { place_id: 'p-horim', name: '호림박물관 신사분관', kind: 'venue', category: '박물관', lat: 37.5230, lng: 127.0230, indoor: true, dwell: 70, reason: '취향 카테고리 상위 · 공식정보 검증됨' },
  ],
  '서초': [
    { place_id: 'p-sac', name: '예술의전당', kind: 'venue', category: '공연장', lat: 37.4797, lng: 127.0114, indoor: true, dwell: 120, reason: '기간형 공연 진행 중' },
    { place_id: 'p-hangaram', name: '한가람미술관', kind: 'venue', category: '미술관', lat: 37.4790, lng: 127.0119, indoor: true, dwell: 85, reason: '도보 3분 · 동선 효율' },
  ],
  '부산': [
    { place_id: 'p-f1963', name: 'F1963', kind: 'venue', category: '복합문화공간', lat: 35.1620, lng: 129.1120, indoor: true, dwell: 130, reason: '과거 만족 기록(별점 5.0)' },
    { place_id: 'p-bcc', name: '영화의전당', kind: 'venue', category: '독립영화관', lat: 35.1710, lng: 129.1290, indoor: true, dwell: 110, reason: '독립영화 상영 · 실내' },
  ],
};

function seedsFor(message: string): Seed[] {
  for (const [key, seeds] of Object.entries(REGION_SEEDS)) {
    if (message.includes(key)) return seeds;
  }
  return SEEDS.slice(0, 4);
}

function mockAnswer(itinerary: Itinerary | null, decisions?: string[]) {
  if (!itinerary || itinerary.items.length === 0) {
    return '조건에 맞는 결과를 찾지 못했어요. 날짜나 지역을 조금 넓혀볼까요?';
  }
  const lines = itinerary.items.map((i) => {
    const t = i.arrive ? new Date(i.arrive).toTimeString().slice(0, 5) : '--:--';
    return `${t}  ${i.name} (${i.dwell_min}분) — ${i.reason ?? ''}`;
  });
  const head = decisions?.length
    ? `선택하신 내용을 반영해 일정을 다시 짰어요. (${decisions.join(', ')})`
    : '조건에 맞는 하루 일정을 만들었어요.';
  return `${head}\n\n${lines.join('\n')}\n\n총 이동 ${itinerary.total_travel_min}분 · 체류 ${itinerary.total_dwell_min}분`;
}

/** SSE 스트리밍 흉내 */
export async function mockStream(
  threadId: string,
  message: string,
  onEvent: (e: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const plan = wantsPlan(message);
  const script = plan ? NODE_SCRIPT : NODE_SCRIPT.slice(0, 3);

  for (const step of script) {
    if (signal?.aborted) return;
    await sleep(step.delay);
    onEvent({ type: 'update', node: step.node, keys: step.keys });
  }
  if (signal?.aborted) return;

  if (!plan) {
    const answer =
      '기록을 찾아봤어요. 최근 6개월 방문 12곳 중 미술관 5, 독립서점 3, 복합문화공간 2곳이었어요. 자세한 일정을 만들까요?';
    for (const chunk of answer.match(/.{1,14}/g) ?? []) {
      await sleep(45);
      onEvent({ type: 'token', text: chunk });
    }
    onEvent({ type: 'done', answer, itinerary: null, evidence: EVIDENCE.slice(0, 2) });
    return;
  }

  const itinerary = buildItinerary(seedsFor(message));
  const advisories = buildAdvisories();
  sessions.set(threadId, { itinerary, advisories, awaiting: true });

  onEvent({
    type: 'interrupt',
    payload: {
      type: 'confirm_plan_changes',
      itinerary,
      advisories,
      evidence: EVIDENCE,
      instruction: '각 항목마다 선택지를 하나씩 골라 주세요.',
    },
  });
}

/** Command(resume=...) 흉내 — 선택에 따라 일정을 다시 만든다 */
export async function mockResume(
  threadId: string,
  decisions: { advisory_id: string; option_id: string }[],
  onEvent: (e: StreamEvent) => void,
): Promise<void> {
  const session = sessions.get(threadId);
  const chosen = decisions.map((d) => {
    const adv = session?.advisories.find((a) => a.id === d.advisory_id);
    return adv?.options.find((o) => o.id === d.option_id);
  }).filter(Boolean) as { action: string; label: string }[];

  const needsReplan = chosen.some((o) =>
    ['replace', 'reorder', 'drop', 'change_transport', 'shift_time', 'add_parking'].includes(o.action));

  if (needsReplan) {
    await sleep(400);
    onEvent({ type: 'update', node: 'itinerary', keys: ['itinerary'] });
    await sleep(380);
    onEvent({ type: 'update', node: 'validation', keys: ['issues'] });
  }
  await sleep(220);
  onEvent({ type: 'update', node: 'finalize', keys: ['itinerary', 'evidence'] });
  await sleep(180);
  onEvent({ type: 'update', node: 'persist', keys: ['edit_signals'] });

  let itinerary = session?.itinerary ?? buildItinerary(SEEDS.slice(0, 4));
  if (chosen.some((o) => o.action === 'replace')) {
    const replaced = SEEDS.filter((s) => s.place_id !== 'p-daelim').slice(0, 3);
    itinerary = buildItinerary([SEEDS[5], ...replaced]);
  } else if (chosen.some((o) => o.action === 'add_parking')) {
    itinerary = { ...itinerary, notes: ['공영주차장(도보 5분) 경유 추가'] };
  } else if (chosen.some((o) => o.action === 'change_transport')) {
    itinerary = {
      ...itinerary,
      items: itinerary.items.map((i) => ({
        ...i, transport: 'subway',
        travel_min_from_prev: Math.round(i.travel_min_from_prev * 1.3),
      })),
      notes: ['이동수단을 대중교통으로 변경'],
    };
  } else if (chosen.some((o) => o.action === 'reorder')) {
    itinerary = buildItinerary([...SEEDS.slice(0, 4)].reverse());
  }

  if (session) sessions.set(threadId, { ...session, itinerary, awaiting: false });

  const answer = mockAnswer(itinerary, chosen.map((o) => o.label));
  for (const chunk of answer.match(/.{1,16}/g) ?? []) {
    await sleep(28);
    onEvent({ type: 'token', text: chunk });
  }
  onEvent({ type: 'done', answer, itinerary, evidence: EVIDENCE });
}

export async function mockSync(threadId: string, message: string): Promise<SyncResult> {
  let result: SyncResult | null = null;
  await mockStream(threadId, message, (e) => {
    if (e.type === 'interrupt') result = { status: 'interrupted', interrupt: e.payload };
    if (e.type === 'done') {
      result = {
        status: 'done', answer: e.answer, itinerary: e.itinerary,
        evidence_ids: e.evidence.map((x) => x.id), advisories: null,
      };
    }
  });
  return result ?? { status: 'done', answer: '', itinerary: null, evidence_ids: [], advisories: null };
}

export function mockEvidence(id: string): Evidence | undefined {
  return EVIDENCE.find((e) => e.id === id);
}

export async function mockReport(): Promise<TasteReport> {
  await sleep(500);
  return {
    stats: {
      preferred_categories: { 미술관: 0.31, 독립서점: 0.22, 복합문화공간: 0.18, 전시: 0.16, 공방: 0.13 },
      indoor_bias: 0.42,
      avg_travel_min: 23.4,
      avg_dwell_min: 68.2,
      novelty_bias: -0.18,
      friction_sensitivity: { parking: 0.38, crowding: 0.27, waiting: 0.18, accessibility: 0.11, cost: 0.06 },
    },
    narrative:
      '최근 6개월간 미술관과 독립서점 중심으로 문화생활을 하셨습니다. 실내 선호가 뚜렷하고(+0.42), 평균 이동 23분·체류 68분으로 한 장소에 오래 머무는 편입니다.\n\n신규 탐색보다 재방문을 조금 더 선호하시며(-0.18), 일정 수정 이력에서는 혼잡한 대형 전시를 반복적으로 삭제하신 패턴이 보입니다.\n\n불편 요소는 주차(38%)가 가장 크고 혼잡도(27%)가 뒤를 잇습니다. 차량 이동 일정에서 주차 정보를 먼저 확인해 드리는 이유입니다.',
  };
}


// ---------------------------------------------------------------- 취향 카드
/**
 * 목 모드의 카드 저장 (UR-01).
 *
 * 서버와 같은 규칙으로 «반영된 결과»를 계산해 돌려준다 — 목에서 아무 변화도 안 보이면
 * 카드 화면이 실제로 무엇을 바꾸는지 백엔드 없이는 확인할 수 없다.
 * 가중치는 `app/memory/profile.py` 의 `_CARD_WEIGHTS` 와 같은 값이다.
 */
const MOCK_CARD_WEIGHT: Record<PreferenceCard['verdict'], number> = {
  recommend: 0.15, interested: 0.08, not_interested: -0.08, dislike: -0.15,
};

const mockCardStore: PreferenceCard[] = [];

export async function mockSavePreferenceCards(cards: PreferenceCard[]) {
  await sleep(400);
  for (const c of cards) {
    const i = mockCardStore.findIndex((x) => x.subject === c.subject);
    if (i >= 0) mockCardStore[i] = c; else mockCardStore.push(c);   // UNIQUE (user, subject)
  }
  const preferredCategories: Record<string, number> = {};
  for (const c of mockCardStore) {
    const w = MOCK_CARD_WEIGHT[c.verdict] * (c.experienced ? 1.5 : 1);
    preferredCategories[c.subject] = Number(
      ((preferredCategories[c.subject] ?? 0) + w).toFixed(4));
  }
  return { saved: cards.length, preferredCategories };
}

export async function mockPreferenceCards(): Promise<PreferenceCard[]> {
  await sleep(200);
  return [...mockCardStore];
}


// ---------------------------------------------------------------- 큐레이션
export async function mockCollections() {
  await sleep(400);
  const P = (name: string, lat: number, lng: number, reason: string,
             extra: Partial<{ rating: number; visits: number; friction: string[] }> = {}) => ({
    place_id: `mk-${name}`, name, lat, lng, reason,
    category: null, address: null, region: '서울', indoor: true, url: null,
    visits: extra.visits ?? 1, rating: extra.rating ?? null,
    dwell_min: null, friction: extra.friction ?? [], last_visit: null,
  });

  return [
    // 내가 담은 테마가 항상 맨 앞. 자동 테마와 섞이면 무엇이 내 의도인지 흐려진다.
    {
      key: 'mine:demo', collection_id: 'demo', mine: true,
      title: '주말 데이트 코스', subtitle: '2곳 · 내가 담은 곳', emoji: '❤️', count: 2,
      places: [
        P('리움미술관', 37.5385, 126.9990, '일정에서 담음'),
        P('한남동 책방', 37.5350, 127.0010, '일정에서 담음'),
      ],
    },
    {
      key: 'favorites', title: '다시 가고 싶은 곳',
      subtitle: '별점 4.5 이상 · 만족도가 높았던 장소', emoji: '⭐', count: 3,
      places: [
        P('필름포럼 독립영화관', 37.5610, 126.9420, '별점 5.0 · 2번 방문', { rating: 5.0, visits: 2 }),
        P('F1963', 35.1620, 129.1120, '별점 5.0 · 평균 130분 체류', { rating: 5.0 }),
        P('국립현대미술관 서울', 37.5785, 126.9800, '별점 4.8 · 2번 방문', { rating: 4.8, visits: 2 }),
      ],
    },
    {
      key: 'no_parking_worry', title: '주차 걱정 없던 곳',
      subtitle: '차로 갔는데 주차 불편이 없었던 장소', emoji: '🚗', count: 2,
      places: [
        P('서대문자연사박물관', 37.5790, 126.9370, '별점 4.5 · 주차 여유', { rating: 4.5 }),
        P('F1963', 35.1620, 129.1120, '별점 5.0', { rating: 5.0 }),
      ],
    },
    {
      key: 'caution', title: '다시 갈 땐 확인할 곳',
      subtitle: '주차·혼잡·접근성 불편을 기록한 장소', emoji: '⚠️', count: 3,
      places: [
        P('대림미술관', 37.5757, 126.9709, '별점 3.8 · 2번 방문 · 불편: 주차·대기',
          { rating: 3.8, visits: 2, friction: ['parking', 'waiting'] }),
        P('코엑스 별마당도서관', 37.5090, 127.0596, '별점 3.0 · 불편: 혼잡',
          { rating: 3.0, friction: ['crowding'] }),
        P('예술의전당', 37.4797, 127.0114, '별점 4.0 · 불편: 주차',
          { rating: 4.0, friction: ['parking'] }),
      ],
    },
    {
      key: 'rainy_day', title: '비 오는 날 갈 곳',
      subtitle: '실내 · 만족도가 높았던 장소', emoji: '☔', count: 2,
      places: [
        P('리움미술관', 37.5384, 126.9990, '별점 4.5 · 평균 110분 체류', { rating: 4.5 }),
        P('땡스북스', 37.5563, 126.9236, '별점 4.8 · 2번 방문', { rating: 4.8, visits: 2 }),
      ],
    },
  ];
}

/** 목 모드에서도 이동수단 전환이 눈에 보이게 — 수단별 속도만 흉내 낸다. */
export function mockReroute(
  transport: string,
  places?: { name: string; lat: number; lng: number; place_id?: string | null;
             indoor?: boolean | null; parking?: string; parking_note?: string | null }[],
) {
  const speed: Record<string, number> = {
    car: 1, subway: 1.7, bus: 2.4, walk: 5.5,
  };
  const k = speed[transport] ?? 2;
  const base = places
    ? {
        id: 'mock-route', date: null, notes: [], version: 1,
        total_dwell_min: 0, total_travel_min: 0,
        map_path: places.map((p) => ({ lat: p.lat, lng: p.lng, name: p.name })),
        items: places.map((p, i) => ({
          seq: i + 1, place_id: p.place_id ?? null, name: p.name, kind: 'venue',
          dwell_min: 0, evidence_ids: [], indoor: p.indoor ?? null,
          parking: (p.parking ?? 'unknown') as never,
          parking_note: p.parking_note ?? null,
          geo: { lat: p.lat, lng: p.lng, name: p.name },
          travel_min_from_prev: i === 0 ? 0 : Math.round(6 * k),
          travel_km_from_prev: i === 0 ? null : Math.round(18 * k) / 10,
          travel_source: 'estimate' as const,
          transport,
        })),
      }
    : null;
  if (base) base.total_travel_min = base.items.reduce((n, i) => n + i.travel_min_from_prev, 0);
  return base as never;
}

/** 캘린더 목 데이터 (UR-28). 백엔드 없이도 월 그리드가 채워진다. */
export async function mockPlans() {
  const day = (back: number) => {
    const d = new Date();
    d.setDate(d.getDate() - back);
    return d.toISOString().slice(0, 10);
  };
  return [
    { id: 'plan-1', plan_date: day(0), status: 'active', stop_count: 3,
      first_place: '성수 온기 북스', destination_name: '카페',
      starts_at: null, updated_at: null },
    { id: 'plan-2', plan_date: day(6), status: 'active', stop_count: 4,
      first_place: '국립현대미술관 덕수궁', destination_name: null,
      starts_at: null, updated_at: null },
    { id: 'plan-3', plan_date: day(21), status: 'active', stop_count: 2,
      first_place: '성남아트센터', destination_name: null,
      starts_at: null, updated_at: null },
  ];
}

export async function mockPlanDetail(_planId: string) {
  // mockSync 는 확인 카드가 뜨는 회차를 돌려줄 수 있어 itinerary 가 없을 수 있다.
  // 캘린더는 '이미 확정된 일정'을 여는 화면이므로 done 회차만 쓴다.
  const res = await mockSync('mock-thread', '일정 보여줘');
  return res.status === 'done' ? (res.itinerary ?? null) : null;
}
