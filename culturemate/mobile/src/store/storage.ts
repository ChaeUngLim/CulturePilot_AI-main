/**
 * 오프라인 캐시. 확정 일정은 로컬에 남겨 네트워크 없이도 타임라인·지도를 볼 수 있게 한다.
 * 모바일에서 연결 단절은 예외가 아니라 상시 조건이다.
 */
import AsyncStorage from '@react-native-async-storage/async-storage';

import type { Itinerary, VisitInput } from '@/api/types';

const K = {
  itinerary: 'cm:itinerary',
  thread: 'cm:thread',
  visits: 'cm:visits',
  mode: 'cm:mode',
  server: 'cm:server',
};

/**
 * 서버가 바뀌면 이전 서버의 캐시를 버린다.
 * 목 모드에서 만든 일정이 실서버 화면에 남아 있으면 "왜 응답이 없는데 일정이 보이지"
 * 라는 잘못된 관찰로 이어진다. 스레드 id 도 서버마다 따로이므로 함께 버린다.
 *
 * 판별 키는 목/실서버가 아니라 **주소 그 자체**다. 연결 화면이 생기면서
 * 실서버 A → 실서버 B 도 흔한 전환이 됐는데, 둘 다 'live' 로 같으면 걸러지지 않는다.
 */
export async function syncMode(apiUrl: string): Promise<void> {
  const current = apiUrl || 'mock';
  const saved = await AsyncStorage.getItem(K.mode);
  if (saved !== current) {
    await AsyncStorage.multiRemove([K.itinerary, K.thread]);
    await AsyncStorage.setItem(K.mode, current);
  }
}

// ------------------------------------------------------------- 서버 주소
// 연결 화면에서 고른 주소. 빌드타임 EXPO_PUBLIC_API_URL 보다 우선한다.
//   null  — 저장한 적 없음 → 빌드타임 기본값을 쓴다
//   ''    — 사용자가 목 모드를 고름 (기본값이 있어도 무시한다)

export async function loadServerUrl(): Promise<string | null> {
  return AsyncStorage.getItem(K.server);
}

/** null 을 주면 저장을 지운다 — 다음 기동부터 빌드타임 기본값으로 돌아간다. */
export async function saveServerUrl(url: string | null): Promise<void> {
  if (url === null) {
    await AsyncStorage.removeItem(K.server);
    return;
  }
  await AsyncStorage.setItem(K.server, url);
}

export async function saveItinerary(it: Itinerary | null) {
  if (!it) return AsyncStorage.removeItem(K.itinerary);
  return AsyncStorage.setItem(K.itinerary, JSON.stringify(it));
}

export async function loadItinerary(): Promise<Itinerary | null> {
  const raw = await AsyncStorage.getItem(K.itinerary);
  return raw ? (JSON.parse(raw) as Itinerary) : null;
}

export async function saveThreadId(id: string) {
  return AsyncStorage.setItem(K.thread, id);
}

export async function loadThreadId(): Promise<string | null> {
  return AsyncStorage.getItem(K.thread);
}

export type StoredVisit = VisitInput & { id: string; visited_at: string; place_name: string; synced: boolean };

export async function loadVisits(): Promise<StoredVisit[]> {
  const raw = await AsyncStorage.getItem(K.visits);
  return raw ? (JSON.parse(raw) as StoredVisit[]) : [];
}

/** 오프라인이면 synced=false로 큐에 남기고, 다음 기동 시 재전송한다. */
export async function addVisit(v: StoredVisit) {
  const all = await loadVisits();
  await AsyncStorage.setItem(K.visits, JSON.stringify([v, ...all]));
}

export async function markSynced(id: string) {
  const all = await loadVisits();
  await AsyncStorage.setItem(
    K.visits,
    JSON.stringify(all.map((v) => (v.id === id ? { ...v, synced: true } : v))),
  );
}

// ---------------------------------------------------------------- 지역 목록
// 키에 버전을 붙인다. 예전에 기본값으로 넣어 뒀던 지역이 기기에 남아,
// 기본값을 비운 뒤에도 계속 나타나는 문제를 끊기 위해서다.
const K_REGIONS = 'cm:regions:v3';
const K_REGIONS_LEGACY = ['cm:regions', 'cm:regions:v2'];

export type StoredRegion = { label: string; region: string };

export async function loadRegions(): Promise<StoredRegion[] | null> {
  await AsyncStorage.multiRemove(K_REGIONS_LEGACY);   // 구버전 잔재 제거
  const raw = await AsyncStorage.getItem(K_REGIONS);
  return raw ? (JSON.parse(raw) as StoredRegion[]) : null;
}

export async function saveRegions(regions: StoredRegion[]) {
  return AsyncStorage.setItem(K_REGIONS, JSON.stringify(regions));
}
