/**
 * CultureMate API 클라이언트.
 *
 * 전송 경로가 둘이다.
 *   1) SSE (POST /chat)      — react-native-sse 폴리필. RN의 fetch는 본문 스트리밍을 못 한다.
 *   2) sync (POST /chat/sync) — 폴리필이 안 되는 환경/네트워크용 폴백.
 * 두 경로 모두 같은 thread_id를 쓰므로 서버 상태 모델은 하나다.
 *
 * 서버 주소가 비어 있으면 목 어댑터로 붙는다. 화면 코드는 이 차이를 알지 못한다.
 * 주소는 연결 화면에서 바뀌므로 모듈 로드 시점이 아니라 호출 시점에 읽는다(apiUrl()).
 */
import { Platform } from 'react-native';
import EventSource from 'react-native-sse';

import { apiUrl, isMock, normalizeUrl, USER_ID } from '@/config';
import {
  mockEvidence, mockReport, mockResume, mockStream, mockSync,
} from './mock';
import type {
  Collection, Decision, Evidence, GeoPoint, Itinerary, PlanSummary, PreferenceCard,
  StreamEvent, SyncResult, TasteReport, Transport, VisitInput,
} from './types';

type SSEEvent = 'token' | 'update' | 'interrupt' | 'done';

const TIMEOUT_MS = 60_000;

async function post<T>(path: string, body: unknown): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const res = await fetch(`${apiUrl()}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
    if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
    return (await res.json()) as T;
  } finally {
    clearTimeout(timer);
  }
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${apiUrl()}${path}`);
  if (!res.ok) throw new Error(`${res.status}`);
  return (await res.json()) as T;
}

async function del(path: string): Promise<void> {
  const res = await fetch(`${apiUrl()}${path}`, { method: 'DELETE' });
  if (!res.ok) throw new Error(`${res.status}`);
}

function parse(raw: string | null | undefined): any {
  try {
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

/**
 * 웹 전용 SSE 리더.
 *
 * react-native-sse 는 RN 네이티브(XHR)를 전제로 만들어져 브라우저에서는
 * 이벤트가 오지 않는다. 브라우저에는 fetch + ReadableStream 이 있으므로
 * 그걸로 직접 읽는다. 실패하면 호출부가 sync 경로로 내려간다.
 */
function streamWeb(
  path: '/chat' | '/resume',
  body: unknown,
  onEvent: (e: StreamEvent) => void,
): () => void {
  const controller = new AbortController();
  let received = false;

  (async () => {
    try {
      const res = await fetch(`${apiUrl()}${path}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
        body: JSON.stringify(body),
        signal: controller.signal,
      });
      if (!res.ok || !res.body) throw new Error(`${res.status}`);

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        // SSE 프레임은 빈 줄로 구분된다
        let sep = buffer.indexOf('\n\n');
        while (sep !== -1) {
          const frame = buffer.slice(0, sep);
          buffer = buffer.slice(sep + 2);
          const event = /^event:\s*(.+)$/m.exec(frame)?.[1]?.trim();
          const data = /^data:\s*([\s\S]*)$/m.exec(frame)?.[1];
          if (event && data) {
            received = true;
            dispatch(event, parse(data), onEvent);
          }
          sep = buffer.indexOf('\n\n');
        }
      }
      if (!received) throw new Error('스트림에서 이벤트를 받지 못했습니다');
    } catch (err) {
      if (controller.signal.aborted) return;
      // 스트리밍이 막힌 환경 — 같은 thread_id 로 비스트리밍 경로를 다시 탄다
      void fallbackToSync(path, body, onEvent, err);
    }
  })();

  return () => controller.abort();
}

function dispatch(event: string, d: any, onEvent: (e: StreamEvent) => void): void {
  if (event === 'token' && d?.text) onEvent({ type: 'token', text: d.text });
  else if (event === 'update' && d?.node)
    onEvent({ type: 'update', node: d.node, keys: d.keys ?? [] });
  else if (event === 'interrupt') {
    const payload = Array.isArray(d) ? d[0] : d;
    if (payload) onEvent({ type: 'interrupt', payload });
  } else if (event === 'done') {
    onEvent({
      type: 'done', answer: d?.answer ?? '',
      itinerary: d?.itinerary ?? null, evidence: d?.evidence ?? [],
      // 서버가 발화에서 읽어낸 조건. 여기서 버리면 출발·도착·이동수단 칩이
      // 질문을 따라가지 못한다 — 세 경로 모두에서 실어 날라야 한다.
      resolved: d?.resolved ?? null,
    });
  }
}

async function fallbackToSync(
  path: '/chat' | '/resume',
  body: any,
  onEvent: (e: StreamEvent) => void,
  reason: unknown,
): Promise<void> {
  console.warn('SSE 실패 → sync 폴백:', reason);
  try {
    const res = path === '/chat'
      ? await post<SyncResult>('/chat/sync', body)
      : await post<SyncResult>('/resume/sync', body);
    emitSync(res, onEvent);
  } catch (e) {
    onEvent({ type: 'error', message: e instanceof Error ? e.message : String(e) });
  }
}

/** sync 응답을 스트림 이벤트 형태로 바꿔 화면 코드가 경로를 구분하지 않게 한다. */
function emitSync(res: SyncResult, onEvent: (e: StreamEvent) => void): void {
  if (res.status === 'interrupted') {
    onEvent({ type: 'interrupt', payload: res.interrupt });
    return;
  }
  onEvent({
    type: 'done', answer: res.answer, itinerary: res.itinerary,
    evidence: [], resolved: res.resolved ?? null,
  });
}

/** SSE 스트리밍(네이티브). 실패하면 sync 폴백으로 내려간다. */
function streamLive(
  path: '/chat' | '/resume',
  body: unknown,
  onEvent: (e: StreamEvent) => void,
): () => void {
  let closed = false;
  const es = new EventSource<SSEEvent>(`${apiUrl()}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    pollingInterval: 0,   // 자동 재연결 금지: 인터럽트로 정상 종료되는 스트림이다
    timeout: TIMEOUT_MS,
  });

  const close = () => {
    if (closed) return;
    closed = true;
    es.removeAllEventListeners();
    es.close();
  };

  es.addEventListener('token', (e) => {
    const d = parse((e as any).data);
    if (d?.text) onEvent({ type: 'token', text: d.text });
  });
  es.addEventListener('update', (e) => {
    const d = parse((e as any).data);
    if (d?.node) onEvent({ type: 'update', node: d.node, keys: d.keys ?? [] });
  });
  es.addEventListener('interrupt', (e) => {
    const d = parse((e as any).data);
    const payload = Array.isArray(d) ? d[0] : d;
    if (payload) onEvent({ type: 'interrupt', payload });
    close();
  });
  es.addEventListener('done', (e) => {
    const d = parse((e as any).data);
    onEvent({
      type: 'done',
      answer: d?.answer ?? '',
      itinerary: d?.itinerary ?? null,
      evidence: d?.evidence ?? [],
      resolved: d?.resolved ?? null,
    });
    close();
  });
  es.addEventListener('error', (e: any) => {
    if (closed) return;
    close();
    void fallbackToSync(path, body, onEvent, e?.message ?? 'SSE error');
  });

  return close;
}

/** 플랫폼에 맞는 스트리밍 경로를 고른다. 화면 코드는 이 차이를 모른다. */
function stream(
  path: '/chat' | '/resume',
  body: unknown,
  onEvent: (e: StreamEvent) => void,
): () => void {
  return Platform.OS === 'web'
    ? streamWeb(path, body, onEvent)
    : streamLive(path, body, onEvent);
}

type ChatOptions = {
  threadId: string;
  message: string;
  /** GPS 등 네이티브만 아는 값. 서버에서 LLM 추출값보다 우선한다. */
  conditionsOverride?: Record<string, unknown> | null;
  onEvent: (e: StreamEvent) => void;
};

/** 대화 시작. 반환값은 취소 함수. */
export function sendChat({ threadId, message, conditionsOverride, onEvent }: ChatOptions): () => void {
  if (isMock()) {
    const controller = new AbortController();
    void mockStream(threadId, message, onEvent, controller.signal);
    return () => controller.abort();
  }
  return stream('/chat', {
    user_id: USER_ID,
    thread_id: threadId,
    message,
    conditions_override: conditionsOverride ?? null,
  }, onEvent);
}

/** HITL 선택 반영 후 재개. */
export function sendResume(
  threadId: string,
  decisions: Decision[],
  onEvent: (e: StreamEvent) => void,
): () => void {
  if (isMock()) {
    void mockResume(threadId, decisions, onEvent);
    return () => {};
  }
  return stream('/resume', { user_id: USER_ID, thread_id: threadId, decisions }, onEvent);
}

/** 비스트리밍 경로 — 스트리밍이 막힌 환경용 폴백. */
export async function chatSync(
  threadId: string,
  message: string,
  conditionsOverride?: Record<string, unknown> | null,
): Promise<SyncResult> {
  if (isMock()) return mockSync(threadId, message);
  return post<SyncResult>('/chat/sync', {
    user_id: USER_ID, thread_id: threadId, message,
    conditions_override: conditionsOverride ?? null,
  });
}

export async function resumeSync(threadId: string, decisions: Decision[]): Promise<SyncResult> {
  if (isMock()) {
    let out: SyncResult = { status: 'done', answer: '', itinerary: null, evidence_ids: [], advisories: null };
    await mockResume(threadId, decisions, (e) => {
      if (e.type === 'done') {
        out = {
          status: 'done', answer: e.answer, itinerary: e.itinerary,
          evidence_ids: e.evidence.map((x) => x.id), advisories: null,
        };
      }
    });
    return out;
  }
  return post<SyncResult>('/resume/sync', { user_id: USER_ID, thread_id: threadId, decisions });
}

/** 앱 복귀 시 상태 복원. 체크포인터가 있으므로 연결이 끊겨도 이어서 진행된다. */
export async function getThreadState(threadId: string) {
  if (isMock()) return null;
  return get<{ values: any; next: string[]; interrupts: any[] }>(`/threads/${threadId}/state`);
}

/** 근거 원문 지연 로드 (UR-14). 목록에는 id만 내려온다. */
export async function getEvidence(threadId: string, evidenceId: string): Promise<Evidence | null> {
  if (isMock()) return mockEvidence(evidenceId) ?? null;
  try {
    return await get<Evidence>(`/threads/${threadId}/evidence/${evidenceId}`);
  } catch {
    return null;
  }
}

export async function postVisit(input: VisitInput): Promise<boolean> {
  if (isMock()) return true;
  try {
    await post('/visits', input);
    return true;
  } catch {
    return false;
  }
}

export async function fetchReport(_threadId: string): Promise<TasteReport | null> {
  if (isMock()) return mockReport();
  // 전용 엔드포인트 — 대화 그래프를 거치지 않아 왕복이 한 번이고 실패 지점이 적다.
  const res = await get<TasteReport & { has_data: boolean }>(
    `/report/${encodeURIComponent(USER_ID)}`);
  return res.has_data || res.narrative ? res : null;
}

/**
 * 카드로 등록한 초기 취향을 올린다 (UR-01 · UR-31).
 *
 * 한 장씩이 아니라 **한 묶음**으로 보낸다. 스와이프는 초당 한 장씩 넘어가는데
 * 장마다 왕복하면 지하철에서 절반이 유실되고, 사용자는 어디까지 저장됐는지 모른다.
 *
 * 서버는 저장 직후 프로필을 재집계해 저장하므로, 돌아온 `preferred_categories` 는
 * «내 카드가 실제로 반영된 결과»다. 화면이 그걸 그대로 보여주면 사용자는 방금 한
 * 행동이 추천에 닿았는지 확인할 수 있다.
 */
export async function savePreferenceCards(
  cards: PreferenceCard[],
): Promise<{ saved: number; preferredCategories: Record<string, number> } | null> {
  if (isMock()) {
    const { mockSavePreferenceCards } = await import('./mock');
    return mockSavePreferenceCards(cards);
  }
  try {
    const res = await post<{ saved: number; preferred_categories: Record<string, number> }>(
      '/preferences/cards',
      { user_id: USER_ID, cards: cards.map(({ subject, verdict, experienced }) => ({
        subject, verdict, experienced })) });
    return { saved: res.saved ?? 0, preferredCategories: res.preferred_categories ?? {} };
  } catch {
    // 저장 실패를 성공으로 보이면 사용자는 카드를 다시 넘기지 않는다. null 로 알린다.
    return null;
  }
}

/** 이미 평가한 카드 — 화면이 «다시 물어보지 않기» 위해 필요하다 (UR-01). */
export async function fetchPreferenceCards(): Promise<PreferenceCard[]> {
  if (isMock()) {
    const { mockPreferenceCards } = await import('./mock');
    return mockPreferenceCards();
  }
  try {
    const res = await get<{ cards: PreferenceCard[] }>(
      `/preferences/cards/${encodeURIComponent(USER_ID)}`);
    return res.cards ?? [];
  } catch {
    // 목록을 못 읽는다고 카드 화면을 막지 않는다. 처음인 것처럼 보여준다.
    return [];
  }
}

/** 아카이브 기반 큐레이션 컬렉션 */
export async function fetchCollections(): Promise<Collection[]> {
  if (isMock()) {
    const { mockCollections } = await import('./mock');
    return mockCollections();
  }
  const res = await get<{ collections: Collection[] }>(
    `/curations/${encodeURIComponent(USER_ID)}`);
  return res.collections ?? [];
}

/**
 * 이동수단만 바꿔 동선을 다시 계산한다.
 *
 * 전체 그래프를 다시 돌리지 않는다 — 장소는 그대로고 구간만 다시 재므로 2~3초면 끝난다.
 *  · 오늘의 일정: threadId 를 주면 서버가 갖고 있는 일정을 다시 잰다
 *  · 큐레이션:   places 를 주면 가까운 순서로 엮어 코스로 만든다
 */
export async function reroute(opts: {
  transport: Transport;
  threadId?: string;
  places?: { place_id?: string | null; name: string; lat: number; lng: number;
             indoor?: boolean | null; parking?: string; parking_note?: string | null }[];
  origin?: GeoPoint | null;
  /** 좌표 대신 이름만 줘도 된다 — 서버가 주소 API로 바꾼다 */
  originName?: string | null;
  destination?: GeoPoint | null;
  destinationName?: string | null;
  /** "09:00" — 주면 각 장소의 도착 시각까지 채워 준다 */
  startTime?: string | null;
}): Promise<Itinerary | null> {
  if (isMock()) {
    const { mockReroute } = await import('./mock');
    return mockReroute(opts.transport, opts.places);
  }
  const res = await post<{ itinerary: Itinerary | null }>('/reroute', {
    transport: opts.transport,
    thread_id: opts.threadId ?? null,
    places: opts.places ?? null,
    origin: opts.origin ?? null,
    origin_name: opts.originName ?? null,
    destination: opts.destination ?? null,
    destination_name: opts.destinationName ?? null,
    start_time: opts.startTime ?? null,
  });
  return res.itinerary ?? null;
}

/**
 * 일정의 장소들을 내 컬렉션에 담는다(즐겨찾기).
 *
 * 같은 이름이 있으면 거기에 더하고, 없으면 새로 만든다. 클라이언트가 이름을
 * 고르게 하므로 서버는 '이름' 하나만 알면 된다 — id 를 주고받지 않는다.
 */
/**
 * 확정된 일정의 구간을 실측해 **실제 경로 좌표**를 받아 온다.
 *
 * 일정 생성과 나눈 이유: 서버의 응답 예산(15초)에는 탐색·검증·편성이 다 들어가야
 * 해서 구간 실측이 잘린다. 그러면 지도가 장소를 직선으로 잇는데, 지하철이 한강을
 * 가로질러 직진하는 그림이 된다.
 *
 * 그래서 일정을 먼저 보여 주고 이걸 뒤이어 부른다. 사용자는 기다리지 않고,
 * 지도의 선만 잠시 뒤 실제 노선으로 바뀐다. 실패해도 조용히 넘어간다 —
 * 선이 직선으로 남을 뿐 일정은 이미 화면에 있다.
 */
export async function fetchRoutes(
  threadId: string, transport?: string,
): Promise<Itinerary | null> {
  try {
    const q = transport ? `?transport=${encodeURIComponent(transport)}` : '';
    const res = await fetch(`${apiUrl()}/threads/${threadId}/routes${q}`,
                            { method: 'POST' });
    if (!res.ok) return null;
    const data = await res.json();
    return (data?.itinerary as Itinerary) ?? null;
  } catch {
    return null;
  }
}

/**
 * 일정의 장소를 공식정보와 대조한다(2단계).
 *
 * `fetchRoutes` 와 같은 이유로 첫 응답에서 뺐다. 서버의 15초 예산에는 탐색·편성이
 * 먼저 들어가 검증이 늘 잘리고, 그대로 두면 모든 장소가 '확인 필요'로 남는다.
 * 실패는 삼킨다 — 검증이 없을 뿐 일정은 이미 화면에 있다.
 */
export async function fetchVerification(threadId: string): Promise<Itinerary | null> {
  try {
    const res = await fetch(`${apiUrl()}/threads/${threadId}/verify`, { method: 'POST' });
    if (!res.ok) return null;
    const data = await res.json();
    return (data?.itinerary as Itinerary) ?? null;
  } catch {
    return null;
  }
}

export async function saveCollection(opts: {
  title: string;
  placeIds: string[];
  emoji?: string;
  subtitle?: string | null;
  note?: string | null;
}): Promise<{ collection_id: string; title: string; added: number }> {
  if (isMock()) {
    return { collection_id: 'mock', title: opts.title, added: opts.placeIds.length };
  }
  return post('/collections', {
    user_id: USER_ID,
    title: opts.title,
    place_ids: opts.placeIds,
    emoji: opts.emoji ?? '⭐',
    subtitle: opts.subtitle ?? null,
    note: opts.note ?? null,
  });
}

/** 내 컬렉션 삭제 */
export async function deleteCollection(collectionId: string): Promise<void> {
  if (isMock()) return;
  await del(`/collections/${encodeURIComponent(collectionId)}`
    + `?user_id=${encodeURIComponent(USER_ID)}`);
}

/** 내 컬렉션에서 장소 하나 빼기 */
export async function removeFromCollection(
  collectionId: string, placeId: string,
): Promise<void> {
  if (isMock()) return;
  await del(`/collections/${encodeURIComponent(collectionId)}`
    + `/places/${encodeURIComponent(placeId)}`
    + `?user_id=${encodeURIComponent(USER_ID)}`);
}

/** 좌표 → 자치구 이름. 실패하면 null (칩에는 '현재 위치'만 표시된다). */
export async function whereAmI(lat: number, lng: number): Promise<string | null> {
  if (isMock()) return '서울 종로구';
  try {
    const res = await get<{ region: string | null }>(
      `/whereami?lat=${lat}&lng=${lng}`);
    return res.region ?? null;
  } catch {
    return null;
  }
}

/** 장소명·주소 → 좌표. 출발지를 직접 지정할 때 쓴다. */
export async function geocode(
  place: string,
): Promise<{ lat: number; lng: number; label: string } | null> {
  if (!place.trim()) return null;
  if (isMock()) return { lat: 37.5551, lng: 126.9368, label: place };
  try {
    const res = await get<{ lat: number | null; lng: number | null; label: string | null }>(
      `/geocode?q=${encodeURIComponent(place)}`);
    if (res.lat == null || res.lng == null) return null;
    return { lat: res.lat, lng: res.lng, label: res.label ?? place };
  } catch {
    return null;
  }
}

export async function health(): Promise<boolean> {
  if (isMock()) return true;
  try {
    const res = await fetch(`${apiUrl()}/health`);
    return res.ok;
  } catch {
    return false;
  }
}

/** 서버가 어떤 외부 키를 갖고 있는지 (`GET /diagnostics`). 연결 화면에서만 쓴다. */
export type ServerDiagnostics = {
  llm: { requested: string; effective: string; keys: Record<string, boolean> };
  naver_maps: boolean;
  naver_local_search: boolean;
  culture_api: boolean;
  weather: { key: boolean; source: string };
  websearch: boolean;
};

export type ProbeResult = {
  ok: boolean;
  /** 왕복 시간(ms). 주소는 맞는데 느린 건지 아예 안 붙는 건지 구분된다. */
  ms: number;
  error?: string;
  diagnostics?: ServerDiagnostics;
};

/**
 * 후보 주소에 실제로 붙여 본다 — **저장하기 전에** 확인하는 용도다.
 *
 * `health()` 와 달리 현재 설정을 건드리지 않는다. 잘못된 주소를 먼저 저장해 놓고
 * 앱 전체가 안 되는 걸로 확인하는 순서가 되면, 사용자는 주소가 틀린 건지 서버가
 * 죽은 건지 구분할 수 없다.
 */
export async function probeServer(raw: string, timeoutMs = 6_000): Promise<ProbeResult> {
  const base = normalizeUrl(raw);
  if (!base) return { ok: false, ms: 0, error: '주소가 비어 있습니다' };

  const started = Date.now();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(`${base}/health`, { signal: controller.signal });
    const ms = Date.now() - started;
    if (!res.ok) return { ok: false, ms, error: `서버가 ${res.status} 로 응답했습니다` };

    // 진단은 덤이다 — 없다고 연결 실패로 보지 않는다(구버전 서버일 수 있다).
    let diagnostics: ServerDiagnostics | undefined;
    try {
      const d = await fetch(`${base}/diagnostics`, { signal: controller.signal });
      if (d.ok) diagnostics = ((await d.json())?.configured as ServerDiagnostics) ?? undefined;
    } catch {
      /* 무시 */
    }
    return { ok: true, ms, diagnostics };
  } catch (e) {
    const ms = Date.now() - started;
    const aborted = controller.signal.aborted;
    return {
      ok: false,
      ms,
      error: aborted
        ? `${timeoutMs / 1000}초 안에 응답이 없습니다 — 주소·포트·방화벽을 확인해 주세요`
        : e instanceof Error ? e.message : String(e),
    };
  } finally {
    clearTimeout(timer);
  }
}

/**
 * 기간별 확정 일정 목록 — 캘린더가 월 그리드를 채울 때 쓴다 (UR-28).
 *
 * 서버 기본 범위는 지난 3개월~앞으로 1개월이다. 캘린더는 과거를 되짚는 화면이라
 * 앞보다 뒤가 넓다 — 기록을 안 남긴 지난 일정이 여기서 드러나야 한다.
 */
export async function fetchPlans(from?: string, to?: string): Promise<PlanSummary[]> {
  if (isMock()) {
    const { mockPlans } = await import('./mock');
    return mockPlans();
  }
  const q = new URLSearchParams();
  if (from) q.set('from', from);
  if (to) q.set('to', to);
  const qs = q.toString() ? `?${q}` : '';
  try {
    const res = await get<{ plans: PlanSummary[] }>(
      `/plans/${encodeURIComponent(USER_ID)}${qs}`);
    return res.plans ?? [];
  } catch {
    // 캘린더가 비는 건 참을 수 있다. 오늘의 일정까지 막지 않는다.
    return [];
  }
}

/** 캘린더에서 날짜를 눌렀을 때 펼칠 그날 일정 전체 (UR-28). */
export async function fetchPlan(planId: string): Promise<Itinerary | null> {
  if (isMock()) {
    const { mockPlanDetail } = await import('./mock');
    return mockPlanDetail(planId);
  }
  try {
    const res = await get<{ itinerary: Itinerary }>(
      `/plans/detail/${encodeURIComponent(planId)}`);
    return res.itinerary ?? null;
  } catch {
    return null;
  }
}
