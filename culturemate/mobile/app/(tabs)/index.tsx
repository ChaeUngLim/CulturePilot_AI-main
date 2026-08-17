/**
 * 메인 화면 — 대화 · 일정 · HITL 확인이 한 흐름으로 이어진다.
 *
 * 화면을 나누지 않은 이유: 사용자는 "일정을 만든다 → 경고를 본다 → 고른다 → 일정이 바뀐다"를
 * 하나의 사건으로 경험한다. 라우팅으로 끊으면 무엇 때문에 일정이 바뀌었는지 맥락이 사라진다.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'expo-router';
import {
  KeyboardAvoidingView, Platform, Pressable, RefreshControl, ScrollView,
  StyleSheet, Text, useWindowDimensions, View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import type {
  AdvisoryOption, Collection, Decision, GeoPoint, Itinerary, Transport,
} from '@/api/types';
import { AdvisoryCard } from '@/components/AdvisoryCard';
import { Composer } from '@/components/Composer';
import { EvidenceSheet } from '@/components/EvidenceSheet';
import { NaverMap, pointsToItinerary } from '@/components/NaverMap';
import { originLabel, type Origin } from '@/components/RoutePoints.types';
import { RoutePoints } from '@/components/RoutePoints';
import { SaveToCollection } from '@/components/SaveToCollection';
import {
  DEFAULT_TRANSPORT, transportMix, TransportPicker,
} from '@/components/TransportPicker';
import { ProgressTrace } from '@/components/ProgressTrace';
import {
  CURRENT_LOCATION, DEFAULT_REGIONS, RegionPicker, type Region,
} from '@/components/RegionPicker';
import { fetchReport, geocode, whereAmI, reroute, fetchCollections} from '@/api/client';
import type { TasteReport } from '@/api/types';
import { loadRegions, saveRegions } from '@/store/storage';
import { Timeline } from '@/components/Timeline';
import { ErrorBoundary } from '@/components/ErrorBoundary';
import { Button, Card, Chip, Empty } from '@/components/ui';
import { FRICTION_LABEL } from '@/constants';
import { isMock } from '@/config';
import { useCM } from '@/hooks/context';
import { useCurrentLocation } from '@/hooks/useCurrentLocation';
import { colors, radius, space, type } from '@/theme';

function TodayScreen() {
  const cm = useCM();
  const { request: requestLocation, status: locStatus } = useCurrentLocation();
  const [picked, setPicked] = useState<Record<string, AdvisoryOption>>({});
  const [evidenceIds, setEvidenceIds] = useState<string[] | null>(null);
  // 선택된 지역 목록. 비어 있으면 현재 위치 모드.
  const [chosen, setChosen] = useState<string[]>([]);
  // 마지막으로 실제 요청에 쓴 선택 — 바뀐 게 없으면 버튼을 숨긴다
  const [applied, setApplied] = useState<string[]>([]);
  const [regions, setRegions] = useState<Region[]>(DEFAULT_REGIONS);
  const bootstrapped = useRef(false);

  const [taste, setTaste] = useState<TasteReport | null>(null);
  // 즐겨찾기 대상 목록. 기존 테마에 담으려면 이름을 알아야 한다.
  const [collections, setCollections] = useState<Collection[]>([]);
  const [savedToast, setSavedToast] = useState<string | null>(null);
  const [here, setHere] = useState<GeoPoint | null>(null);
  // 대화 로그는 기본으로 접는다. 화면의 주인공은 일정이다.
  const [showLog, setShowLog] = useState(false);
  // 현재 위치의 자치구. '현재 위치'만 보여주면 어디인지 알 수 없어 신뢰가 안 간다.
  const [hereLabel, setHereLabel] = useState<string | null>(null);
  // 출발지. 현재 위치가 늘 맞진 않다 — 집에서 내일 일정을 짜는 경우가 더 흔하다.
  const [origin, setOrigin] = useState<Origin>({ kind: 'current', label: null });
  // 도착지는 선택. 지정하면 일정 마지막을 그 근처로 맞춘다.
  const [destination, setDestination] = useState<{ name: string; point: GeoPoint } | null>(null);
  // 출발 시각(선택). 정하면 서버가 그 시각부터 일정을 짠다.
  const [startTime, setStartTime] = useState<string | null>(null);
  // 도착 목표 시각(선택). 마지막 일정이 이 시각까지 끝나도록 짠다.
  const [endTime, setEndTime] = useState<string | null>(null);
  const [locating, setLocating] = useState(false);
  // 이동수단. 항상 하나가 선택돼 있다 — 지도의 숫자가 무슨 기준인지 분명해야 한다.
  const [transport, setTransport] = useState<Transport>(DEFAULT_TRANSPORT);
  // 수단을 바꾸면 기존 일정을 그 기준으로 다시 잰다(탐색은 다시 하지 않는다)
  const [rerouting, setRerouting] = useState(false);
  const [rerouted, setRerouted] = useState<Itinerary | null>(null);
  // 재계산이 실패하면 화면은 그대로인데 사용자는 눌렀다고 생각한다. 실패는 밝힌다.
  const [rerouteError, setRerouteError] = useState<string | null>(null);
  // 손으로 고른 게 아니라 발화에서 잡힌 것인지 — 라벨 문구가 달라진다
  const [transportAuto, setTransportAuto] = useState(false);

  const locate = useCallback(async () => {
    setLocating(true);
    try {
      const point = await requestLocation();
      if (!point) return null;
      setHere(point);
      const label = await whereAmI(point.lat, point.lng);
      setHereLabel(label);
      setOrigin({ kind: 'current', label });
      return point;
    } finally {
      setLocating(false);
    }
  }, [requestLocation]);

  /** 직접 입력한 출발지를 좌표로 바꿔 고정한다. */
  const setCustomOrigin = useCallback(async (place: string) => {
    setLocating(true);
    try {
      const found = await geocode(place);
      if (!found) {
        // 못 찾으면 조용히 현재 위치로 돌아가지 않는다 — 사용자가 이유를 알아야 한다
        setOrigin({ kind: 'custom', label: `${place} · 못 찾음` });
        return null;
      }
      const point = { lat: found.lat, lng: found.lng, name: found.label };
      setHere(point);
      setHereLabel(null);
      setOrigin({ kind: 'custom', label: found.label });
      return point;
    } finally {
      setLocating(false);
    }
  }, []);

  const setCustomDestination = useCallback(async (place: string | null) => {
    if (!place) { setDestination(null); return null; }
    const found = await geocode(place);
    const next = found
      ? { name: found.label, point: { lat: found.lat, lng: found.lng, name: found.label } }
      : { name: `${place} · 못 찾음`, point: { lat: 0, lng: 0, name: place } };
    setDestination(next);
    return next;
  }, []);

  /**
   * 질문에서 읽어낸 조건으로 화면의 컨트롤을 전부 맞춘다.
   *
   * "양재역 9시 출발 … 종로역 귀가"라고 말했으면 출발·시각·도착 칩이 그렇게
   * 켜져 있어야 한다. 계산에 쓴 조건과 화면에 켜진 조건이 다르면 두 가지가
   * 무너진다 — 어느 쪽을 믿을지 알 수 없고, 다음 질문이 엉뚱한 조건으로 나간다.
   */
  const resolved = cm.resolved;
  useEffect(() => {
    if (!resolved) return;

    if (resolved.transport && resolved.transport !== 'unknown'
        && resolved.transport !== transport) {
      setTransport(resolved.transport as Transport);
      setTransportAuto(true);
    }
    if (resolved.start_time && resolved.start_time !== startTime) {
      setStartTime(resolved.start_time);
    }
    if (resolved.end_time && resolved.end_time !== endTime) {
      setEndTime(resolved.end_time);
    }
    // 발화에 도착지가 없으면 이전 질문의 값을 지운다 — 남아 있으면 다음 질문에
    // 그 조건이 몰래 실려 나간다.
    if (!resolved.destination_name && destination) setDestination(null);
    if (resolved.origin_name && resolved.origin_name !== origin.label) {
      // 서버가 이미 좌표로 바꿔 일정을 짰다. 화면은 이름만 맞춰 두면 된다.
      setOrigin({ kind: 'custom', label: resolved.origin_name });
    }
    if (resolved.destination_name && resolved.destination_name !== destination?.name) {
      void setCustomDestination(resolved.destination_name);
    }
    // 지역 칩도 질문을 따라간다. 지점("강남역 근처")을 말했으면 구 단위 칩은 건드리지 않는다.
    if (!resolved.landmark && resolved.regions?.length) {
      setChosen((prev) => (prev.join() === resolved.regions!.join()
        ? prev : resolved.regions!));
      setApplied(resolved.regions);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resolved]);

  /**
   * 조건이 바뀌면 지도와 목록을 그 기준으로 다시 그린다.
   *
   * 이동수단·출발지·도착지 중 무엇이 바뀌든 같은 경로를 탄다. 장소는 그대로 두고
   * 구간만 다시 재므로 탐색·검증을 반복하지 않는다(2~3초). 화면에 일정이 없으면
   * 다시 잴 것도 없으므로 다음 질문에 그 조건이 실려 나간다.
   */
  const recompute = useCallback(async (over: {
    transport?: Transport;
    origin?: GeoPoint | null;
    destination?: { name: string; point: GeoPoint } | null;
  } = {}) => {
    if (!cm.itinerary || cm.restored) return;
    const dest = over.destination !== undefined ? over.destination : destination;
    setRerouting(true);
    setRerouteError(null);
    try {
      const updated = await reroute({
        transport: over.transport ?? transport,
        threadId: cm.threadId,
        origin: over.origin !== undefined ? over.origin : (here ?? null),
        originName: origin.kind === 'custom' ? origin.label : null,
        destination: dest && dest.point.lat !== 0 ? dest.point : null,
        destinationName: dest?.name ?? null,
        startTime: startTime,
      });
      setRerouted(updated);
    } catch {
      // 원래 일정을 그대로 두되, 바뀌지 않았다는 사실을 말한다
      setRerouted(null);
      setRerouteError('경로를 다시 계산하지 못했어요. 잠시 후 다시 시도해 주세요.');
    } finally {
      setRerouting(false);
    }
  }, [cm.itinerary, cm.restored, cm.threadId, destination, here, origin, startTime,
      transport]);

  const changeTransport = useCallback(async (next: Transport) => {
    setTransport(next);
    setTransportAuto(false);
    await recompute({ transport: next });
  }, [recompute]);

  /**
   * 화면에 그릴 일정.
   *
   * 지난 세션에서 저장된 일정(cm.restored)은 여기에 넣지 않는다. 묻지도 않았는데
   * 결과가 떠 있으면 방금 만든 일정으로 오해하고, 그 오해 위에서 판단하게 된다.
   * 대신 아래에 '지난 일정 보기' 버튼을 두어 원할 때만 꺼내 본다.
   */
  const shownItinerary = cm.restored ? null : (rerouted ?? cm.itinerary);

  // 사용자가 추가한 지역은 기기에 남는다. 매번 다시 입력하게 하지 않는다.
  useEffect(() => {
    void loadRegions().then((saved) => { if (saved) setRegions(saved); });
    // 취향 요약 — 무엇을 근거로 추천했는지 보여주기 위한 것이라 실패해도 무시한다
    void fetchReport('').then(setTaste).catch(() => {});
    void fetchCollections().then(setCollections).catch(() => {});
  }, []);

  const persist = useCallback((next: Region[]) => {
    setRegions(next);
    void saveRegions(next.filter((r): r is { label: string; region: string } =>
      r.region !== null));
  }, []);

  // 칩은 '다음 질문의 범위'다. 누르는 순간 추천이 돌면 사용자는 조건을 다 고르기도 전에
  // 매번 결과를 기다리게 된다. 선택은 상태로만 남기고, 실행은 질문할 때 한다.
  const addRegion = useCallback((r: Region) => {
    persist([...regions, r]);
    if (r.region) setChosen((prev) => [...prev, r.region!]);
  }, [persist, regions]);

  const removeRegion = useCallback((r: Region) => {
    persist(regions.filter((x) => x.region !== r.region));
    setChosen((prev) => prev.filter((x) => x !== r.region));
  }, [persist, regions]);

  const toggleRegion = useCallback((r: Region) => {
    if (!r.region) return;
    setChosen((prev) =>
      prev.includes(r.region!) ? prev.filter((x) => x !== r.region) : [...prev, r.region!]);
  }, []);
  const scrollRef = useRef<ScrollView>(null);
  const insets = useSafeAreaInsets();
  const { height: winH } = useWindowDimensions();
  // 화면 높이에 비례. 폰에서는 너무 크지 않게, 데스크톱에서는 답답하지 않게.
  const mapHeight = Math.round(Math.min(Math.max(winH * 0.34, 220), 420));

  const awaiting = cm.phase === 'awaiting_confirm';
  const busy = cm.phase === 'running';

  const allPicked = useMemo(
    () => cm.advisories.length > 0 && cm.advisories.every((a) => picked[a.id]),
    [cm.advisories, picked],
  );

  const onSend = useCallback(
    async (text: string, needsLocation?: boolean) => {
      setPicked({});
      const override: Record<string, unknown> = {};

      // 선택한 지역은 질문의 검색 범위로 실린다. 발화에 지역이 있으면
      // 서버가 그쪽을 우선하므로("신촌역 근처"), 칩은 말하지 않은 경우의 기본값이 된다.
      if (chosen.length > 0) {
        override.regions = chosen;
        override.region = chosen[0];
      }
      // 출발지는 지역 선택과 무관하게 함께 보낸다.
      // 지역을 고르는 건 범위를 '넓히는' 행위이지 출발지를 버리는 게 아니다.
      // 직접 지정한 출발지는 GPS 로 덮어쓰지 않는다.
      const point = here ?? (origin.kind === 'current' ? await locate() : null);
      if (point) override.origin = point;
      // 도착지가 있으면 마지막 일정이 그 근처에서 끝나도록 제약을 건다
      if (destination && destination.point.lat !== 0) {
        override.destination = destination.point;
        override.destination_name = destination.name;
      }
      override.transport = transport;   // 화면에 켜진 수단이 곧 계산 기준이다
      if (startTime) override.start_time = startTime;
      if (endTime) override.end_time = endTime;
      setRerouted(null);                // 새 질문이 오면 재계산본은 버린다
      // 확인 대기 중이어도 새 질문은 받는다. 여기서 막으면 확인 카드를 채우지
      // 못한 사용자는 아무것도 물을 수 없는 상태에 갇힌다.
      // 새 질문은 곧 '이 일정은 됐고 다시 짜 달라'는 뜻이므로 대기를 버린다.
      if (busy) return;                  // 진행 중 중복 요청만 차단
      if (awaiting) cm.dismissConfirm();
      setApplied(chosen);
      cm.send(text, Object.keys(override).length > 0 ? override : null);
      setTimeout(() => scrollRef.current?.scrollToEnd({ animated: true }), 120);
    },
    [awaiting, busy, chosen, cm, destination, endTime, here, locate, origin.kind,
     startTime, transport],
  );

  const submitDecisions = useCallback(() => {
    const decisions: Decision[] = cm.advisories.map((a) => ({
      advisory_id: a.id,
      option_id: picked[a.id].id,
      note: picked[a.id].action === 'keep' ? a.place_id ?? null : null,
    }));
    setPicked({});
    cm.confirm(decisions);
  }, [cm, picked]);

  /**
   * 선택된 지역들로 추천을 요청한다.
   * 지역이 하나도 없으면 현재 위치를 잡아서 쓴다 — 이게 기본 동작이다.
   */
  const recommend = useCallback(
    async (targets: string[]) => {
      const override: Record<string, unknown> = {};
      let where: string;

      if (targets.length > 0) {
        override.regions = targets;
        override.region = targets[0];
        where = targets.join(', ');
      } else {
        const point = await requestLocation();
        if (point) {
          override.origin = point;
          where = '지금 내 주변';
        } else {
          // 위치를 못 얻으면 지역을 정해 주는 편이 빈 결과보다 낫다
          override.region = REGION_FALLBACK.region;
          override.regions = [REGION_FALLBACK.region];
          where = REGION_FALLBACK.region!;
        }
      }
      cm.send(
        `${where}에서 오늘 가볼 만한 문화생활을 내 취향과 과거 방문 기록을 반영해서 추천해줘`,
        override,
      );
    },
    [cm, requestLocation],
  );

  // 처음에는 현재 위치 지도만 보여준다.
  // 앱을 열자마자 LLM 추론과 외부 API를 부르면 10~20초 동안 아무것도 못 하고 기다려야 한다.
  // 지도를 먼저 띄우고, 추천은 사용자가 새로고침을 눌렀을 때 시작한다.
  useEffect(() => {
    if (bootstrapped.current) return;
    bootstrapped.current = true;
    void locate();
  }, [locate]);

  /** 새로고침 — 현재 위치 기준으로 취향을 반영해 다시 추천한다. */
  const refreshHere = useCallback(() => {
    setChosen([]);
    setApplied([]);
    setPicked({});
    void locate();          // 새로고침 = 현재 위치로 복귀
    void recommend([]);
  }, [locate, recommend]);

  // 선택한 범위가 마지막 요청과 다른가 — 아직 반영 안 됐음을 알린다
  const scopeChanged = useMemo(
    () => chosen.length !== applied.length || chosen.some((x) => !applied.includes(x)),
    [applied, chosen],
  );

  // 초기 화면: 아직 아무 요청도 안 했고 일정도 없는 상태
  const showIntro = cm.phase === 'idle' && !shownItinerary && cm.messages.length === 0;

  return (
    <KeyboardAvoidingView
      style={{ flex: 1, backgroundColor: colors.bg }}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      keyboardVerticalOffset={insets.top + 44}
    >
      <ScrollView
        ref={scrollRef}
        contentContainerStyle={s.content}
        keyboardShouldPersistTaps="handled"
        refreshControl={
          <RefreshControl
            refreshing={busy}
            onRefresh={refreshHere}
            tintColor={colors.accent}
          />
        }
      >
        {isMock() && (
          <Link href="/connect" asChild>
            <Pressable style={({ pressed }) => [s.banner, pressed && { opacity: 0.7 }]}>
              <Chip label="목 모드" tone="accent" />
              <Text style={[type.tiny, { flex: 1 }]}>
                백엔드 없이 동작 중입니다. 눌러서 서버에 연결하세요.
              </Text>
              <Text style={[type.tiny, { color: colors.accent }]}>연결 ›</Text>
            </Pressable>
          </Link>
        )}

        {taste?.stats && (shownItinerary || cm.messages.length > 0) && (
          <TasteStrip stats={taste.stats} />
        )}

        {showIntro && (
          <View style={{ gap: space(3) }}>
            <View style={s.planHead}>
              <View style={{ flex: 1 }}>
                <View style={s.titleRow}>
                  <Text style={type.h2}>{'오늘의 일정'}</Text>
                  <Pressable
                    onPress={refreshHere}
                    disabled={busy}
                    hitSlop={10}
                    style={({ pressed }) => [s.refresh, (pressed || busy) && { opacity: 0.5 }]}
                  >
                    <Text style={{ color: colors.accent, fontSize: 16 }}>{'⟳'}</Text>
                  </Pressable>
                </View>
                <Text style={type.tiny}>
                  {here ? originLabel(origin) : '위치를 확인하는 중…'}
                </Text>
              </View>
            </View>

            {here && <NaverMap itinerary={pointsToItinerary([
              { name: originLabel(origin), lat: here.lat, lng: here.lng },
            ])} height={mapHeight} />}

            {cm.restored && cm.itinerary && cm.itinerary.items.length > 0 && (
              <Pressable
                onPress={cm.showRestored}
                style={({ pressed }) => [s.restored, pressed && { opacity: 0.7 }]}
              >
                <Text style={[type.small, { color: colors.accent }]}>
                  {`↺ 지난 일정 다시 보기 (${cm.itinerary.items.length}곳)`}
                </Text>
                <Text style={type.tiny}>
                  {cm.itinerary.date
                    ? new Date(cm.itinerary.date).toLocaleDateString('ko-KR')
                    : '저장된 일정'}
                </Text>
              </Pressable>
            )}

            <Card>
              <Text style={type.h3}>{'새로고침을 누르면 추천 일정을 만들어요'}</Text>
              <Text style={[type.small, { marginTop: space(2) }]}>
                {'위 ⟳ 버튼을 누르면 현재 위치와 내 취향·과거 방문 기록을 반영해 하루 일정을 짜 드립니다. '
                  + '지역을 고르거나 아래에 직접 물어보셔도 됩니다.'}
              </Text>
            </Card>
          </View>
        )}

        <ProgressTrace trace={cm.trace} phase={cm.phase} />

        {cm.error && (
          <Card style={{ borderColor: colors.danger }}>
            <Text style={[type.h3, { color: colors.danger }]}>연결 문제</Text>
            <Text style={[type.small, { marginTop: space(2) }]}>{cm.error}</Text>
            <Button label="다시 시작" variant="outline" onPress={cm.reset} style={{ marginTop: space(3) }} />
          </Card>
        )}

        {locStatus === 'denied' && (
          <Card style={{ borderColor: colors.warn }}>
            <Text style={[type.small, { color: colors.warn }]}>
              {'위치 권한이 없습니다. 위에서 지역을 고르거나 "강남역 근처" 처럼 직접 말씀해 주세요.'}
            </Text>
          </Card>
        )}

        {awaiting && (
          <View style={{ gap: space(3) }}>
            <View>
              <Text style={type.h2}>확인이 필요해요</Text>
              <Text style={[type.small, { marginTop: space(1) }]}>
                일정을 자동으로 바꾸지 않았습니다. 각 항목을 확인하고 직접 골라 주세요.
              </Text>
            </View>
            {cm.advisories.map((a) => (
              <AdvisoryCard
                key={a.id}
                advisory={a}
                selectedOptionId={picked[a.id]?.id}
                onSelect={(o) => setPicked((p) => ({ ...p, [a.id]: o }))}
                onShowEvidence={setEvidenceIds}
              />
            ))}
            <Button
              label={allPicked ? '선택 반영하고 일정 확정' : `${Object.keys(picked).length}/${cm.advisories.length} 선택됨`}
              onPress={submitDecisions}
              disabled={!allPicked}
            />
          </View>
        )}

        {shownItinerary && shownItinerary.items.length > 0 && (
          <View style={{ gap: space(3) }}>
            <View style={s.planHead}>
              <View style={{ flex: 1 }}>
                <View style={s.titleRow}>
                  <Text style={type.h2}>{awaiting ? '현재 일정 (확인 전)' : '오늘의 일정'}</Text>
                  <Pressable
                    onPress={refreshHere}
                    disabled={busy || awaiting}
                    hitSlop={10}
                    style={({ pressed }) => [
                      s.refresh,
                      (pressed || busy || awaiting) && { opacity: 0.5 },
                    ]}
                  >
                    <Text style={{ color: colors.accent, fontSize: 16 }}>{'⟳'}</Text>
                  </Pressable>
                </View>
                {/* 계산에 실제로 쓰인 값을 보여준다. 화면 상태(칩)를 그대로 읽으면
                    좌표를 못 찾아 반영되지 않은 이름까지 적혀서 거짓말이 된다. */}
                <Text style={type.tiny} numberOfLines={1}>
                  {[
                    shownItinerary.origin_name
                      ? `🚩 ${shownItinerary.origin_name} 출발`
                      : `📍 ${originLabel(origin)} 기준`,
                    chosen.length > 0 && !shownItinerary.origin_name
                      ? chosen.join(', ') : null,
                  ].filter(Boolean).join(' + ')
                    + (shownItinerary.destination_name
                      ? ` → 🏁 ${shownItinerary.destination_name}` : '')}
                </Text>
              </View>
              <Text style={type.tiny}>
                {shownItinerary.transport_mode === 'best'
                  ? (transportMix(shownItinerary.transport_mix)
                     || `이동 ${shownItinerary.total_travel_min}분`)
                  : `이동 ${shownItinerary.total_travel_min}분`}
              </Text>
            </View>
            <NaverMap itinerary={shownItinerary} height={mapHeight} />

            {(!!shownItinerary.origin_name || !!shownItinerary.destination_name) && (
              <Text style={type.tiny} numberOfLines={1}>
                {[shownItinerary.origin_name && `🚩 ${shownItinerary.origin_name}`,
                  shownItinerary.destination_name && `🏁 ${shownItinerary.destination_name}`]
                  .filter(Boolean).join('  →  ')}
              </Text>
            )}

            <View style={s.summary}>
              <Chip label={`${shownItinerary.items.length}곳`} tone="accent" />
              <Chip label={`이동 ${shownItinerary.total_travel_min}분`} />
              <Chip label={`체류 ${shownItinerary.total_dwell_min}분`} />
              {!!shownItinerary.total_fare && (
                <Chip label={`요금 ${shownItinerary.total_fare.toLocaleString()}원`} />
              )}
              {shownItinerary.items[0]?.arrive && (
                <Chip label={`${new Date(shownItinerary.items[0].arrive!)
                  .toTimeString().slice(0, 5)} 시작`} tone="ok" />
              )}
            </View>

            <Card>
            <SaveToCollection
              items={shownItinerary.items}
              collections={collections}
              onSaved={(title, added) => {
                setSavedToast(`⭐ '${title}'에 ${added}곳 담았어요`);
                void fetchCollections().then(setCollections).catch(() => {});
                setTimeout(() => setSavedToast(null), 3000);
              }}
            />
            {!!savedToast && (
              <Text style={[type.small, { color: colors.ok }]}>{savedToast}</Text>
            )}
            {!!rerouteError && (
              <Text style={[type.small, { color: colors.danger }]}>{rerouteError}</Text>
            )}

              <Timeline
                itinerary={shownItinerary}
                highlightSeq={awaiting ? cm.advisories[0]?.target_seq ?? null : null}
                onPressItem={(item) =>
                  item.evidence_ids.length ? setEvidenceIds(item.evidence_ids) : undefined
                }
              />
            </Card>
          </View>
        )}

        {!showIntro && !shownItinerary && cm.phase === 'done' && (
          <Empty title="일정이 만들어지지 않았어요" body="날짜나 지역을 조금 넓혀서 다시 물어봐 주세요." />
        )}

        {cm.messages.length > 0 && (
          <View style={{ gap: space(2) }}>
            <Pressable onPress={() => setShowLog((v) => !v)} hitSlop={8}>
              <Text style={[type.tiny, { color: colors.accent }]}>
                {showLog ? '대화 접기 ▲' : `대화 보기 (${cm.messages.length}) ▼`}
              </Text>
            </Pressable>
            {showLog && cm.messages.map((m) => (
              <View key={m.id} style={[s.msg, m.role === 'user' ? s.msgUser : s.msgBot]}>
                <Text style={[type.body, m.role === 'user' && { color: '#0F1115' }]}>
                  {m.text}
                </Text>
              </View>
            ))}
          </View>
        )}
      </ScrollView>

      <View style={[s.inputArea, { paddingBottom: Math.max(insets.bottom - 8, 0) }]}>
        <View style={s.scope}>
          <RoutePoints
            origin={origin.kind === 'custom' ? origin.label : null}
            destination={destination?.name ?? null}
            startTime={startTime}
            endTime={endTime}
            currentLabel={originLabel(origin)}
            locating={locating}
            onUseCurrent={() => void locate()}
            onOrigin={(place) => void (async () => {
              const point = place ? await setCustomOrigin(place) : await locate();
              await recompute({ origin: point ?? null });
            })()}
            onDestination={(place) => void (async () => {
              const next = await setCustomDestination(place);
              await recompute({ destination: next });
            })()}
            onStartTime={(v) => { setStartTime(v); void recompute(); }}
            onEndTime={(v) => { setEndTime(v); void recompute(); }}
            disabled={busy || awaiting}
          />
          <TransportPicker
            value={transport}
            autoDetected={transportAuto}
            busy={rerouting}
            mix={shownItinerary?.transport_mix}
            onChange={(next) => void changeTransport(next)}
            disabled={busy || awaiting}
          />
          <RegionPicker
            regions={regions}
            selected={chosen}
            hereLabel={hereLabel}
            pending={scopeChanged}
            onToggle={toggleRegion}
            onUseLocation={() => setChosen([])}
            onAdd={addRegion}
            onRemove={removeRegion}
            disabled={busy || awaiting}
          />
        </View>
        <Composer onSend={onSend} disabled={busy} />
      </View>

      <EvidenceSheet
        ids={evidenceIds}
        onClose={() => setEvidenceIds(null)}
        resolve={cm.evidenceById}
      />
    </KeyboardAvoidingView>
  );
}

/**
 * 취향 근거 표시.
 * 개인화는 결과만 보면 알 수 없다. 무엇을 근거로 골랐는지 드러나야
 * 사용자가 추천을 신뢰하거나, 틀렸을 때 고칠 수 있다.
 */
function TasteStrip({ stats }: { stats: TasteReport['stats'] }) {
  const cats = Object.entries(stats.preferred_categories ?? {})
    .filter(([, v]) => v > 0)   // 싫다고 한 카테고리가 «내 취향 반영»에 뜨면 안 된다(UR-01)
    .sort((a, b) => b[1] - a[1]).slice(0, 3).map(([k]) => k);
  const frictions = Object.entries(stats.friction_sensitivity ?? {})
    .sort((a, b) => b[1] - a[1]).slice(0, 2).map(([k]) => k);
  if (cats.length === 0 && frictions.length === 0) return null;

  const indoor = (stats.indoor_bias ?? 0) >= 0.3 ? '실내 선호'
    : (stats.indoor_bias ?? 0) <= -0.3 ? '야외 선호' : null;

  return (
    <View style={s.taste}>
      <Text style={[type.tiny, { color: colors.accent, fontWeight: '700' }]}>
        {'내 취향 반영'}
      </Text>
      <View style={s.tasteChips}>
        {cats.map((c) => <Chip key={`cat-${c}`} label={c} tone="accent" />)}
        {indoor && <Chip key="indoor" label={indoor} tone="accent" />}
        {frictions.map((f) => (
          <Chip key={`fr-${f}`} label={`${FRICTION_LABEL[f as never] ?? f} 주의`}
                tone="warn" />
        ))}
      </View>
    </View>
  );
}

/** 위치 권한이 거부됐을 때 기본으로 보여줄 지역 */
const REGION_FALLBACK: Region = { label: '종로구', region: '서울 종로구' };

const s = StyleSheet.create({
  content: {
    padding: space(4), gap: space(4), paddingBottom: space(8),
    // 넓은 화면에서 한 줄이 지나치게 길어지면 읽기 어렵다
    width: '100%', maxWidth: 900, alignSelf: 'center',
  },
  banner: {
    flexDirection: 'row', alignItems: 'center', gap: space(2),
    backgroundColor: colors.surfaceAlt, padding: space(3), borderRadius: 10,
  },
  msg: { padding: space(3), borderRadius: 14, maxWidth: '92%' },
  msgUser: { alignSelf: 'flex-end', backgroundColor: colors.accent },
  msgBot: { alignSelf: 'flex-start', backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.border },
  planHead: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start' },
  titleRow: { flexDirection: 'row', alignItems: 'center', gap: space(2) },
  inputArea: { borderTopWidth: 1, borderColor: colors.border, backgroundColor: colors.bg },
  scope: { paddingHorizontal: space(4), paddingTop: space(3),
           width: '100%', maxWidth: 900, alignSelf: 'center' },
  taste: {
    gap: space(2), padding: space(3), borderRadius: 10,
    backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.border,
  },
  tasteChips: { flexDirection: 'row', flexWrap: 'wrap', gap: space(1) },
  summary: { flexDirection: 'row', flexWrap: 'wrap', gap: space(2) },
  restored: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    gap: space(2), paddingHorizontal: space(4), paddingVertical: space(3),
    borderRadius: radius.lg, borderWidth: 1, borderColor: colors.border,
    borderStyle: 'dashed', backgroundColor: colors.surface,
  },
  refresh: {
    width: 28, height: 28, borderRadius: 14, alignItems: 'center',
    justifyContent: 'center', backgroundColor: colors.surfaceAlt,
    borderWidth: 1, borderColor: colors.border,
  },
});


export default function Screen() {
  return (
    <ErrorBoundary screen="오늘">
      <TodayScreen />
    </ErrorBoundary>
  );
}
