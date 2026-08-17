/**
 * 대화·일정 상태 머신.
 *
 * 서버 그래프의 상태를 클라이언트에서 미러링하지 않는다. 서버가 진실이고,
 * 여기서는 '지금 화면이 무엇을 보여줘야 하는가'만 관리한다.
 *   idle → running → (awaiting_confirm) → running → done
 */
import { useCallback, useEffect, useRef, useState } from 'react';

import {
  fetchRoutes, fetchVerification, getEvidence, sendChat, sendResume,
} from '@/api/client';
import type {
  Advisory, Decision, Evidence, InterruptPayload, Itinerary, Resolved, StreamEvent,
} from '@/api/types';
import { apiUrl, isMock } from '@/config';
import { loadItinerary, loadThreadId, saveItinerary, saveThreadId, syncMode } from '@/store/storage';

export type Phase = 'idle' | 'running' | 'awaiting_confirm' | 'done' | 'error';

export type Message = { id: string; role: 'user' | 'assistant'; text: string };

export type TraceStep = { node: string; keys: string[]; at: number };

const NODE_LABEL: Record<string, string> = {
  classify: '요청 분석',
  archive: '개인 아카이브 조회',
  discovery: '문화 콘텐츠 탐색·검증',
  current_plan: '기존 일정 확인',
  merge_context: '정보 통합',
  itinerary: '일정·동선 생성',
  validation: '일정 검증',
  hitl: '사용자 확인',
  finalize: '선택 반영',
  persist: '아카이브 저장',
  compose: '응답 작성',
  report: '취향 리포트 생성',
};

export const nodeLabel = (n: string) => NODE_LABEL[n] ?? n;

const newThreadId = () => `t-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`;

// 메시지 id. Date.now() 만 쓰면 같은 밀리초에 두 개가 생겨 React key 가 충돌한다.
let msgSeq = 0;
const newMessageId = (role: string) => `${role}-${Date.now().toString(36)}-${msgSeq++}`;

// 화면에 내부 용어가 새어 나가면 사용자는 자기가 무엇을 해야 하는지가 아니라
// 시스템이 무엇을 원하는지를 읽게 된다. 서버 문구도 한 번 걸러서 쓴다.
const JARGON = /advisory|option_id|payload|schema|decisions\[/i;

function confirmText(p: InterruptPayload): string {
  const clean = p.instruction && !JARGON.test(p.instruction) ? p.instruction : '';
  if (clean) return clean;
  const stops = p.itinerary?.items.length ?? 0;
  const n = p.advisories?.length ?? 0;
  const head = stops ? `${stops}곳으로 일정을 짰어요. ` : '';
  return n === 1
    ? `${head}확인할 것이 하나 있습니다 — 아래에서 골라 주세요.`
    : `${head}확인할 것이 ${n}개 있습니다 — 아래에서 하나씩 골라 주세요.`;
}

export function useCultureMate() {
  const [threadId, setThreadId] = useState<string>(() => newThreadId());
  const [phase, setPhase] = useState<Phase>('idle');
  const [messages, setMessages] = useState<Message[]>([]);
  const [trace, setTrace] = useState<TraceStep[]>([]);
  const [itinerary, setItinerary] = useState<Itinerary | null>(null);
  const [advisories, setAdvisories] = useState<Advisory[]>([]);
  const [evidence, setEvidence] = useState<Evidence[]>([]);
  // 서버가 발화에서 해석한 조건. 화면의 선택 컨트롤이 이걸 따라간다.
  const [resolved, setResolved] = useState<Resolved | null>(null);
  // 저장소에서 되살린 일정인지. 이번에 물어봐서 나온 결과와 구분해야 한다 —
  // 묻지도 않았는데 지난 일정이 결과처럼 떠 있으면 방금 만든 것으로 오해한다.
  const [restored, setRestored] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const cancelRef = useRef<(() => void) | null>(null);
  const streamingRef = useRef<string>('');
  // 실측 응답이 늦게 도착했을 때 '지금 화면에 있는 일정'이 맞는지 가릴 표식.
  // 사용자가 그 사이 새로 물어봤다면 옛 일정의 경로로 덮어써서는 안 된다.
  const routesForRef = useRef<string | null>(null);

  /**
   * 일정을 띄운 뒤 뒤따라 채우는 것들. 실패는 각각 삼킨다.
   *
   *   1) 구간 실측  — 지도의 직선이 실제 노선으로 바뀐다
   *   2) 공식정보 검증 — '확인 필요'가 '검증됨'으로 바뀐다
   *
   * 순서대로 부른다. 검증이 경로보다 오래 걸리는데, 지도가 먼저 정확해지는 편이
   * 사용자에게 더 크게 보인다.
   */
  const fillRoutes = useCallback(async (it: Itinerary) => {
    const tid = threadId;
    routesForRef.current = it.id;

    const measured = await fetchRoutes(tid, it.transport_mode ?? undefined);
    if (routesForRef.current !== it.id) return;      // 그 사이 새로 물어봤다
    if (measured) {
      setItinerary(measured);
      void saveItinerary(measured);
    }

    const verified = await fetchVerification(tid);
    if (!verified || routesForRef.current !== it.id) return;
    // 검증은 경로 좌표를 건드리지 않으므로, 방금 받은 실측 결과 위에 상태만 얹는다.
    const base = measured ?? it;
    const status = new Map(verified.items.map((v) => [v.seq, v.verify_status]));
    setItinerary({
      ...base,
      items: base.items.map((x) => ({ ...x, verify_status: status.get(x.seq) ?? x.verify_status })),
    });
  }, [threadId]);

  // 앱 재기동 시 마지막 확정 일정을 복원한다.
  // 다만 화면에 결과로 내세우지는 않는다 — 아카이브 탭의 '오늘 일정에서 기록하기'와
  // '지난 일정 보기'에만 쓰이고, 메인 화면은 빈 상태에서 시작한다.
  useEffect(() => {
    void (async () => {
      await syncMode(apiUrl());   // 서버가 바뀌었으면 이전 캐시를 버린다
      const [cached, tid] = await Promise.all([loadItinerary(), loadThreadId()]);
      if (cached) { setItinerary(cached); setRestored(true); }
      if (tid) setThreadId(tid);
    })();
  }, []);

  const handle = useCallback((e: StreamEvent) => {
    switch (e.type) {
      case 'update':
        setTrace((t) => [...t, { node: e.node, keys: e.keys, at: Date.now() }]);
        break;
      case 'token': {
        streamingRef.current += e.text;
        const text = streamingRef.current;
        setMessages((m) => {
          const last = m[m.length - 1];
          if (last?.role === 'assistant' && last.id === 'streaming') {
            return [...m.slice(0, -1), { ...last, text }];
          }
          return [...m, { id: 'streaming', role: 'assistant', text }];
        });
        break;
      }
      case 'interrupt': {
        const p = e.payload as InterruptPayload;
        setAdvisories(p.advisories ?? []);
        setEvidence(p.evidence ?? []);
        // 확인 카드도 '해석된 조건'을 싣고 온다. 이걸 반영하지 않으면
        // "판교역에서 7시 출발"이라고 말해도 칩이 이전 값 그대로 남는다.
        if (p.resolved) setResolved(p.resolved);
        if (p.itinerary) {
          setItinerary(p.itinerary);
          setRestored(false);
          // 확인 화면에도 지도가 함께 뜬다. 여기서 안 채우면 사용자는 직선으로
          // 그려진 동선을 보고 '그대로 진행'을 판단하게 된다.
          if (!isMock()) void fillRoutes(p.itinerary);
        }
        // 확인 대기도 '답'이다. 대화에 아무것도 안 남기면 사용자는 질문이
        // 씹혔다고 여긴다 — 실제로는 확인을 기다리는 중인데.
        streamingRef.current = '';
        setMessages((m) => {
          const rest = m.filter((x) => x.id !== 'streaming');
          return [...rest, { id: newMessageId('a'), role: 'assistant',
                             text: confirmText(p) }];
        });
        setPhase('awaiting_confirm');
        break;
      }
      case 'done': {
        const text = streamingRef.current || e.answer;
        streamingRef.current = '';
        setMessages((m) => {
          const rest = m.filter((x) => x.id !== 'streaming');
          return text ? [...rest, { id: newMessageId('a'), role: 'assistant', text }] : rest;
        });
        if (e.itinerary) {
          setItinerary(e.itinerary);
          setRestored(false);
          void saveItinerary(e.itinerary);
          // 일정을 먼저 띄우고, 실제 경로 좌표는 뒤이어 채운다.
          // 이걸 기다렸다가 함께 그리면 첫 화면이 그만큼 늦어지는데,
          // 사용자가 먼저 보고 싶은 건 '어디를 가는가'지 '선이 정확한가'가 아니다.
          if (!isMock()) void fillRoutes(e.itinerary);
        }
        if (e.evidence?.length) setEvidence(e.evidence);
        if (e.resolved) setResolved(e.resolved);
        setAdvisories([]);
        setPhase('done');
        break;
      }
      case 'error':
        setError(e.message);
        setPhase('error');
        break;
    }
  }, [fillRoutes]);

  const send = useCallback(
    (text: string, conditionsOverride?: Record<string, unknown> | null) => {
      if (!text.trim()) return;
      cancelRef.current?.();
      streamingRef.current = '';
      // 이전 요청의 실측 응답이 늦게 와서 새 일정을 덮어쓰지 않게 표식을 버린다
      routesForRef.current = null;
      setError(null);
      setTrace([]);
      setAdvisories([]);
      setMessages((m) => [...m, { id: newMessageId('u'), role: 'user', text }]);
      setPhase('running');
      void saveThreadId(threadId);
      cancelRef.current = sendChat({ threadId, message: text, conditionsOverride, onEvent: handle });
    },
    [handle, threadId],
  );

  const confirm = useCallback(
    (decisions: Decision[]) => {
      cancelRef.current?.();
      streamingRef.current = '';
      setPhase('running');
      cancelRef.current = sendResume(threadId, decisions, handle);
    },
    [handle, threadId],
  );

  const reset = useCallback(() => {
    cancelRef.current?.();
    const tid = newThreadId();
    setThreadId(tid);
    void saveThreadId(tid);
    setMessages([]);
    setTrace([]);
    setAdvisories([]);
    setEvidence([]);
    setError(null);
    setPhase('idle');
  }, []);

  const evidenceById = useCallback(
    async (id: string): Promise<Evidence | null> => {
      const local = evidence.find((e) => e.id === id);
      if (local) return local;
      return getEvidence(threadId, id);
    },
    [evidence, threadId],
  );

  useEffect(() => () => cancelRef.current?.(), []);

  return {
    threadId, phase, messages, trace, itinerary, advisories, evidence, error,
    resolved, restored, showRestored: () => setRestored(false),
    // 확인 카드를 접고 대화를 이어 간다. 새 질문이 곧 그 답이기 때문이다.
    dismissConfirm: () => { setAdvisories([]); setPhase('idle'); },
    send, confirm, reset, evidenceById,
  };
}
