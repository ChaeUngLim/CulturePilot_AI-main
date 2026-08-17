/**
 * HITL 계약 회귀 테스트 (백엔드·시뮬레이터 불필요).
 *
 * 검증 대상은 UI가 아니라 '계약'이다.
 *   - 노드 진행 → 인터럽트 → 재개 → 재계획 순서가 유지되는가
 *   - 첫 선택지가 항상 '유지'인가 (변경이 기본값이면 자동 변경 승인이 된다)
 *   - 모든 선택지에 예상 효과가 붙는가
 *   - 일정의 시간 정합성(이동시간 확보)이 지켜지는가
 *   - '유지'만 골랐을 때 불필요한 재계획이 일어나지 않는가
 */
import { mockResume, mockStream } from '../.verify/mock.js';

let failed = 0;
const check = (name, cond, extra = '') => {
  const mark = cond ? '  ✓' : '  ✗';
  if (!cond) failed++;
  console.log(`${mark} ${name}${extra ? `  ${extra}` : ''}`);
};

const run = async () => {
  console.log('\n[1] 일정 생성 → HITL 인터럽트');
  const ev = [];
  await mockStream('t1', '이번 주말 성수동에서 하루 문화생활 일정 짜줘', (e) => ev.push(e));
  const nodes = ev.filter((e) => e.type === 'update').map((e) => e.node);
  const it = ev.find((e) => e.type === 'interrupt');

  check('노드가 설계 순서대로 진행', 
    nodes.join(',') === 'classify,archive,discovery,merge_context,itinerary,validation', nodes.join(' → '));
  check('확인이 필요한 상태로 정지', !!it);

  const p = it.payload;
  check('일정이 함께 전달됨', p.itinerary?.items?.length > 0, `${p.itinerary.items.length}곳`);
  check('확인 카드 존재', p.advisories.length > 0, `${p.advisories.length}장`);
  check('첫 선택지는 항상 "유지"', p.advisories.every((a) => a.options[0].action === 'keep'));
  check('모든 선택지에 예상 효과', p.advisories.every((a) => a.options.every((o) => !!o.predicted_effect)));
  check('카드가 근거를 참조', p.advisories.every((a) => a.evidence_ids.length > 0));
  check('근거 원문 동봉', p.evidence.length > 0, `${p.evidence.length}건`);

  const items = p.itinerary.items;
  const timeOk = items.every((cur, i) =>
    i === 0 || (new Date(cur.arrive) - new Date(items[i - 1].depart)) / 60000 + 1e-6 >= cur.travel_min_from_prev);
  check('시간 정합성(이동시간 확보)', timeOk);
  check('운영 종료 전 일정 종료', new Date(items.at(-1).depart).getHours() < 21);

  console.log('\n[2] "교체" 선택 → 재계획');
  const ev2 = [];
  const replace = p.advisories[0].options.find((o) => o.action === 'replace');
  await mockResume('t1', [
    { advisory_id: p.advisories[0].id, option_id: replace.id },
    { advisory_id: p.advisories[1].id, option_id: p.advisories[1].options[0].id },
  ], (e) => ev2.push(e));
  const done = ev2.find((e) => e.type === 'done');
  check('일정 재생성 노드 재실행', ev2.some((e) => e.type === 'update' && e.node === 'itinerary'));
  check('아카이브 저장까지 진행', ev2.some((e) => e.type === 'update' && e.node === 'persist'));
  check('교체 대상이 일정에서 제거됨', !done.itinerary.items.some((i) => i.place_id === 'p-daelim'));
  check('응답 스트리밍', ev2.filter((e) => e.type === 'token').length > 0);

  console.log('\n[3] "유지"만 선택 → 재계획 없음');
  const ev3 = [];
  await mockStream('t2', '일정 짜줘', () => {});
  await mockResume('t2', [], (e) => ev3.push(e));
  check('불필요한 재계획 없음', !ev3.some((e) => e.type === 'update' && e.node === 'itinerary'));

  console.log('\n[4] 아카이브 질의 → 인터럽트 없이 종료');
  const ev4 = [];
  await mockStream('t3', '내가 지난 6개월 동안 어디 다녀왔지?', (e) => ev4.push(e));
  check('일정 생성 경로를 타지 않음', !ev4.some((e) => e.type === 'interrupt'));
  check('정상 종료', ev4.some((e) => e.type === 'done'));

  console.log(failed === 0 ? '\n모든 계약 검증 통과\n' : `\n실패 ${failed}건\n`);
  process.exit(failed === 0 ? 0 : 1);
};

run();
