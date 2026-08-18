/** 런타임 설정. EXPO_PUBLIC_* 만 앱 번들에 주입된다. */

/** 빌드 시 주입된 서버 주소. 연결 화면의 «기본값으로 되돌리기» 가 돌아갈 자리다. */
export const ENV_API_URL = (process.env.EXPO_PUBLIC_API_URL ?? '').replace(/\/$/, '');

export const USER_ID = process.env.EXPO_PUBLIC_USER_ID ?? 'demo-user';
export const NAVER_MAP_KEY = process.env.EXPO_PUBLIC_NAVER_MAP_KEY ?? '';
export const NAVER_MAP_KEY_PARAM = process.env.EXPO_PUBLIC_NAVER_MAP_KEY_PARAM ?? 'ncpKeyId';

/**
 * 지금 유효한 서버 주소. 빌드타임 값으로 시작하고, 연결 화면에서 저장한 주소가
 * 있으면 기동 때 그것으로 덮어쓴다.
 *
 * 상수가 아니라 함수인 이유 — .env 를 고치고 다시 빌드해야만 서버가 바뀌면
 * 실기기·터널처럼 주소가 매번 달라지는 환경에서는 앱을 붙일 방법이 없다.
 * 호출 시점에 읽어야 하므로 `import { API_URL }` 같은 상수 캡처는 쓰지 않는다.
 */
let current = ENV_API_URL;

export function apiUrl(): string {
  return current;
}

/** 주소가 비어 있으면 목 모드. 백엔드 없이도 전체 플로우가 돈다. */
export function isMock(): boolean {
  return current.length === 0;
}

/** 사람이 친 주소를 실제로 붙일 수 있는 형태로 만든다. `localhost:8000` → `http://localhost:8000` */
export function normalizeUrl(raw: string): string {
  const t = raw.trim().replace(/\/+$/, '');
  if (!t) return '';
  return /^[a-z]+:\/\//i.test(t) ? t : `http://${t}`;
}

type Listener = (url: string) => void;
const listeners = new Set<Listener>();

/**
 * 주소가 바뀌면 알린다.
 *
 * 대화 스레드·확정 일정은 «어느 서버의» 것인지에 매여 있다. 서버만 갈아 끼우고
 * 화면 상태를 그대로 두면 목 모드에서 만든 일정이 실서버 화면에 남는 것과 같은
 * 일이 벌어진다 — 그래서 루트에서 이 신호를 받아 상태를 통째로 다시 세운다.
 */
export function onApiUrlChange(fn: Listener): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

/** 새 주소를 적용한다. 빈 문자열이면 목 모드. 정규화된 최종 주소를 돌려준다. */
export function setApiUrl(raw: string): string {
  const next = normalizeUrl(raw);
  if (next === current) return current;
  current = next;
  listeners.forEach((fn) => fn(current));
  return current;
}
