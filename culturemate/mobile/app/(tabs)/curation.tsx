/**
 * 큐레이션 지도 — 내 아카이브에서 뽑은 테마 묶음.
 *
 * 남이 만든 '서울 데이트 코스'는 검색으로도 나온다. 여기서 만드는 건
 * '내가 주차 걱정 없이 다녀온 곳'처럼 이 앱만 만들 수 있는 묶음이다.
 * 그래서 장소마다 왜 들어왔는지(reason)를 반드시 함께 보여준다.
 */
import { useFocusEffect } from 'expo-router';
import { useCallback, useEffect, useState } from 'react';
import {
  ActivityIndicator, Linking, Pressable, RefreshControl, ScrollView,
  StyleSheet, Text, View,
} from 'react-native';

import { deleteCollection, fetchCollections, removeFromCollection, reroute } from '@/api/client';
import type { Collection, CuratedPlace, Itinerary, Transport } from '@/api/types';
import { ErrorBoundary } from '@/components/ErrorBoundary';
import { legLabel, NaverMap, pointsToItinerary } from '@/components/NaverMap';
import { PlaceFacts } from '@/components/PlaceFacts';
import { RoutePoints } from '@/components/RoutePoints';
import { DEFAULT_TRANSPORT, TransportPicker } from '@/components/TransportPicker';
import { Card, Chip, Empty } from '@/components/ui';
import { FRICTION_LABEL } from '@/constants';
import { colors, radius, space, type } from '@/theme';

function CurationScreen() {
  const [collections, setCollections] = useState<Collection[]>([]);
  const [openKey, setOpenKey] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // 오늘의 일정과 같은 규칙 — 항상 하나의 수단이 기준이 된다
  const [transport, setTransport] = useState<Transport>(DEFAULT_TRANSPORT);
  const [routed, setRouted] = useState<Itinerary | null>(null);
  const [routing, setRouting] = useState(false);
  // 출발지·도착지·출발시각. 셋 다 선택이고, 넣으면 실제 시각까지 채워진다.
  const [origin, setOrigin] = useState<string | null>(null);
  const [destination, setDestination] = useState<string | null>(null);
  const [startTime, setStartTime] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const next = await fetchCollections();
      setCollections(next);
      setOpenKey((prev) => prev ?? next[0]?.key ?? null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useFocusEffect(
    useCallback(() => {
      if (collections.length === 0) void load();
    }, [collections.length, load]),
  );

  const open = collections.find((c) => c.key === openKey) ?? collections[0];

  const removeTheme = useCallback(async (id: string) => {
    await deleteCollection(id).catch(() => {});
    setOpenKey(null);
    setCollections([]);          // 다음 포커스에서 다시 불러온다
    void load();
  }, [load]);

  const removePlace = useCallback(async (id: string, placeId: string) => {
    await removeFromCollection(id, placeId).catch(() => {});
    setCollections([]);
    void load();
  }, [load]);

  /**
   * 고른 수단으로 이 테마의 동선을 계산한다.
   *
   * 큐레이션은 원래 '묶음'이지 '코스'가 아니다. 하지만 이동수단을 고른다는 건
   * "이 테마를 이 수단으로 돌면 얼마나 걸리나"가 궁금해졌다는 뜻이므로,
   * 그때부터는 가까운 순서로 이어 붙여 코스로 보여준다.
   */
  useEffect(() => {
    const places = open?.places ?? [];
    if (places.length < 2) { setRouted(null); return; }
    let cancelled = false;
    setRouting(true);
    reroute({
      transport,
      originName: origin,
      destinationName: destination,
      startTime,
      places: places.map((p) => ({
        place_id: p.place_id, name: p.name, lat: p.lat, lng: p.lng,
        indoor: p.indoor, parking: p.parking ?? 'unknown',
        parking_note: p.parking_note,
      })),
    })
      .then((it) => { if (!cancelled) setRouted(it); })
      .catch(() => { if (!cancelled) setRouted(null); })
      .finally(() => { if (!cancelled) setRouting(false); });
    return () => { cancelled = true; };
  }, [open?.key, open?.places, transport, origin, destination, startTime]);

  if (loading && collections.length === 0) {
    return (
      <View style={s.center}>
        <ActivityIndicator color={colors.accent} />
        <Text style={[type.small, { marginTop: space(3) }]}>
          {'기록에서 테마를 뽑는 중…'}
        </Text>
      </View>
    );
  }

  if (error) {
    return (
      <ScrollView style={s.screen} contentContainerStyle={s.content}>
        <Card style={{ borderColor: colors.danger }}>
          <Text style={[type.h3, { color: colors.danger }]}>
            {'큐레이션을 불러오지 못했습니다'}
          </Text>
          <Text style={[type.small, { marginTop: space(2) }]}>{error}</Text>
        </Card>
      </ScrollView>
    );
  }

  if (collections.length === 0) {
    return (
      <View style={s.screen}>
        <Empty
          title="아직 만들 수 있는 테마가 없어요"
          body={'오늘의 일정에서 ⭐ 버튼으로 마음에 든 일정을 담아 보세요. '
            + "방문 기록이 쌓이면 '주차 걱정 없던 곳' 같은 테마도 자동으로 생깁니다."}
        />
      </View>
    );
  }

  return (
    <ScrollView
      style={s.screen}
      contentContainerStyle={s.content}
      refreshControl={
        <RefreshControl refreshing={loading} onRefresh={load} tintColor={colors.accent} />
      }
    >
      <Text style={type.tiny}>
        {`내 방문 기록 ${collections.reduce((n, c) => n + c.count, 0)}건에서 뽑은 ${collections.length}개 테마`}
      </Text>

      <ScrollView horizontal showsHorizontalScrollIndicator={false}
                  contentContainerStyle={s.tabs}>
        {collections.map((c) => {
          const on = c.key === open.key;
          return (
            <Pressable
              key={c.key}
              onPress={() => setOpenKey(c.key)}
              style={({ pressed }) => [
                s.tab,
                on && { backgroundColor: colors.accent, borderColor: colors.accent },
                pressed && { opacity: 0.6 },
              ]}
            >
              <Text style={[type.small, {
                color: on ? '#0F1115' : colors.text, fontWeight: on ? '700' : '500',
              }]}>
                {`${c.emoji} ${c.title} ${c.count}`}
              </Text>
            </Pressable>
          );
        })}
      </ScrollView>

      <View style={{ gap: space(1) }}>
        <View style={s.titleRow}>
          <Text style={[type.h2, { flex: 1 }]}>{`${open.emoji} ${open.title}`}</Text>
          {/* 내가 담은 테마만 지울 수 있다. 자동 테마는 규칙이 만든 것이라 손댈 수 없다. */}
          {open.mine && !!open.collection_id && (
            <Pressable
              onPress={() => void removeTheme(open.collection_id!)}
              hitSlop={8}
              style={({ pressed }) => pressed && { opacity: 0.6 }}
            >
              <Text style={[type.tiny, { color: colors.danger }]}>{'테마 삭제'}</Text>
            </Pressable>
          )}
        </View>
        <Text style={type.small}>{open.subtitle}</Text>
        {open.mine && (
          <Text style={type.tiny}>
            {'내가 담은 곳 · 길게 눌러 빼기'}
          </Text>
        )}
      </View>

      <TransportPicker value={transport} busy={routing}
                       mix={routed?.transport_mix} onChange={setTransport} />

      {/* 출발지·도착지·출발시각은 전부 선택. 넣으면 시각까지 계산된다. */}
      <RoutePoints
        origin={origin} destination={destination} startTime={startTime}
        onOrigin={setOrigin} onDestination={setDestination} onStartTime={setStartTime}
      />

      {/* 수단을 고르면 코스가 되고, 계산 전에는 묶음 그대로 보여준다 */}
      <NaverMap
        itinerary={routed ?? pointsToItinerary(open.places)}
        height={280}
        route={!!routed}
      />

      {!!routed?.notes?.length && (
        <View style={{ gap: space(1) }}>
          {routed.notes.map((n) => (
            <Text key={n} style={[type.small, { color: colors.ok }]}>{`▸ ${n}`}</Text>
          ))}
        </View>
      )}

      {routed && routed.items.length > 0 && (
        <View style={s.summary}>
          <Chip label={`${routed.items.length}곳`} tone="accent" />
          <Chip label={`이동 ${routed.total_travel_min}분`} />
          {!!routed.total_fare && (
            <Chip label={`요금 ${routed.total_fare.toLocaleString()}원`} />
          )}
        </View>
      )}

      <View style={{ gap: space(3) }}>
        {(routed ? _ordered(open.places, routed) : open.places).map((p, idx) => (
          <PlaceRow key={p.place_id} place={p} index={idx + 1}
                    leg={routed?.items.find((i) => i.place_id === p.place_id) ?? null}
                    onRemove={open.mine && open.collection_id
                      ? () => void removePlace(open.collection_id!, p.place_id)
                      : undefined} />
        ))}
      </View>
    </ScrollView>
  );
}

/** 계산된 코스 순서대로 장소를 다시 늘어놓는다. 지도의 번호와 목록이 어긋나면 안 된다. */
function _ordered(places: CuratedPlace[], routed: Itinerary): CuratedPlace[] {
  const byId = new Map(places.map((p) => [p.place_id, p]));
  const out = routed.items.map((i) => byId.get(i.place_id ?? '')).filter(Boolean);
  return out.length === places.length ? (out as CuratedPlace[]) : places;
}

function PlaceRow({
  place, index, leg, onRemove,
}: {
  place: CuratedPlace;
  index: number;
  /** 이 장소까지 오는 구간 — 수단을 고른 뒤에만 있다 */
  leg?: Itinerary['items'][number] | null;
  /** 내가 담은 테마에서만 — 길게 눌러 뺀다 */
  onRemove?: () => void;
}) {
  const open = () => place.url && Linking.openURL(place.url);
  return (
    <Pressable onPress={open} onLongPress={onRemove} delayLongPress={500}
               disabled={!place.url && !onRemove}
               style={({ pressed }) => [s.place, pressed && { opacity: 0.75 }]}>
      <View style={s.badge}>
        <Text style={[type.small, { color: '#0F1115', fontWeight: '800' }]}>{index}</Text>
      </View>
      <View style={{ flex: 1 }}>
        <View style={s.placeHead}>
          <Text style={[type.h3, { flex: 1 }]} numberOfLines={1}>{place.name}</Text>
          {place.rating != null && (
            <Text style={[type.small, { color: colors.warn }]}>
              {`★ ${place.rating.toFixed(1)}`}
            </Text>
          )}
        </View>
        {!!place.address && (
          <Text style={type.tiny} numberOfLines={1}>{place.address}</Text>
        )}
        {!!leg && leg.travel_min_from_prev > 0 && (
          <Text style={[type.tiny, { marginTop: space(1), color: colors.accent }]}>
            {`↓ ${legLabel(leg)}`}
          </Text>
        )}
        <PlaceFacts
          category={place.category}
          indoor={place.indoor}
          parking={place.parking}
          parkingNote={place.parking_note}
          arriveBy={leg && leg.travel_min_from_prev > 0 ? leg.transport : null}
          extra={place.friction.map((f) => ({
            label: `${FRICTION_LABEL[f as never] ?? f} 불편`, tone: 'danger' as const,
          }))}
        />
        <Text style={[type.small, { marginTop: space(2) }]}>{place.reason}</Text>
      </View>
    </Pressable>
  );
}

const s = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.bg },
  center: { flex: 1, backgroundColor: colors.bg, alignItems: 'center', justifyContent: 'center' },
  content: { padding: space(4), gap: space(4), paddingBottom: space(10) },
  tabs: { gap: space(2), paddingRight: space(4) },
  tab: {
    paddingHorizontal: space(4), paddingVertical: space(2),
    minHeight: 40, justifyContent: 'center',
    borderRadius: radius.xl, borderWidth: 1, borderColor: colors.border,
    backgroundColor: colors.surface,
  },
  place: {
    flexDirection: 'row', gap: space(3), padding: space(4),
    backgroundColor: colors.surface, borderRadius: radius.lg,
    borderWidth: 1, borderColor: colors.border, minHeight: 72,
  },
  badge: {
    width: 26, height: 26, borderRadius: 13, backgroundColor: colors.accent,
    alignItems: 'center', justifyContent: 'center', marginTop: space(0.5),
  },
  placeHead: { flexDirection: 'row', alignItems: 'center', gap: space(2) },
  titleRow: { flexDirection: 'row', alignItems: 'center', gap: space(2) },
  summary: { flexDirection: 'row', flexWrap: 'wrap', gap: space(1) },
});

export default function Screen() {
  return (
    <ErrorBoundary screen="큐레이션">
      <CurationScreen />
    </ErrorBoundary>
  );
}
