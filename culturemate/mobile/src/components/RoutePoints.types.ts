/**
 * 출발지의 두 가지 상태.
 *
 * 현재 위치와 직접 정한 장소는 다른 개념이다. GPS 는 '정하지 않았을 때 대신
 * 쓰는 값'이고, 직접 정한 곳은 '사용자가 뜻한 출발점'이다. 한 타입에 뭉뚱그리면
 * 화면이 둘을 같은 모양으로 그리게 되고, 그러면 "부산역에서 출발"이라고 말해도
 * 여전히 현재 위치인 줄 알게 된다.
 */
export type Origin =
  | { kind: 'current'; label: string | null }      // GPS
  | { kind: 'custom'; label: string };             // 직접 입력한 장소명

export function originLabel(o: Origin): string {
  if (o.kind === 'current') return o.label ? `현재 위치 (${o.label})` : '현재 위치';
  return o.label;
}
