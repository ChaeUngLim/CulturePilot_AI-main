/** 일정 타임라인. 각 항목에 '왜 여기 배치됐는지'(reason)를 항상 붙인다 — UR-14. */
import { Pressable, StyleSheet, Text, View } from 'react-native';

import type { Itinerary, ItineraryItem } from '@/api/types';
import { legLabel } from '@/components/NaverMap';
import { PlaceFacts } from '@/components/PlaceFacts';
import { colors, radius, space, type } from '@/theme';

const hhmm = (iso?: string | null) =>
  iso ? new Date(iso).toTimeString().slice(0, 5) : '--:--';

export function Timeline({
  itinerary, onPressItem, highlightSeq,
}: {
  itinerary: Itinerary;
  onPressItem?: (item: ItineraryItem) => void;
  highlightSeq?: number | null;
}) {
  return (
    <View style={{ gap: space(1) }}>
      {itinerary.items.map((item, idx) => {
        const isLast = idx === itinerary.items.length - 1;
        const highlighted = highlightSeq === item.seq;
        return (
          <View key={`${item.seq}-${item.name}`}>
            {item.travel_min_from_prev > 0 && (
              <View style={s.travelRow}>
                <View style={s.railGap} />
                <Text style={type.tiny}>{`↓ ${legLabel(item)}`}</Text>
              </View>
            )}
            <Pressable
              onPress={() => onPressItem?.(item)}
              style={({ pressed }) => [s.row, highlighted && s.rowHi, pressed && { opacity: 0.8 }]}
            >
              <View style={s.rail}>
                <View style={[s.node, highlighted && { backgroundColor: colors.warn }]} />
                {!isLast && <View style={s.line} />}
              </View>
              <View style={s.body}>
                <View style={s.timeRow}>
                  <Text style={[type.h3, { color: colors.accent }]}>{hhmm(item.arrive)}</Text>
                  <Text style={type.tiny}>~ {hhmm(item.depart)} · {item.dwell_min}분</Text>
                </View>
                <Text style={[type.h3, { marginTop: space(1) }]}>{item.name}</Text>
                <PlaceFacts
                  kind={item.kind}
                  indoor={item.indoor}
                  parking={item.parking}
                  parkingNote={item.parking_note}
                  fixedTime={item.fixed_time}
                  arriveBy={item.travel_min_from_prev > 0 ? item.transport : null}
                  verifyStatus={item.verify_status}
                  extra={item.evidence_ids.length > 0
                    ? [{ label: `근거 ${item.evidence_ids.length}`, tone: 'ok' }]
                    : undefined}
                />
                {!!item.reason && (
                  <Text style={[type.small, { marginTop: space(2) }]}>{item.reason}</Text>
                )}
              </View>
            </Pressable>
          </View>
        );
      })}
      {itinerary.notes.length > 0 && (
        <View style={s.notes}>
          {itinerary.notes.map((n) => (
            <Text key={n} style={[type.small, { color: colors.ok }]}>{`· ${n}`}</Text>
          ))}
        </View>
      )}
    </View>
  );
}

const RAIL = 28;

const s = StyleSheet.create({
  row: { flexDirection: 'row', gap: space(3), borderRadius: radius.md, padding: space(2) },
  rowHi: { backgroundColor: colors.warnSoft },
  rail: { width: RAIL, alignItems: 'center' },
  railGap: { width: RAIL },
  node: {
    width: 12, height: 12, borderRadius: 6, marginTop: space(1),
    backgroundColor: colors.accent,
  },
  line: { flex: 1, width: 2, backgroundColor: colors.border, marginTop: space(1) },
  body: { flex: 1, paddingBottom: space(3) },
  timeRow: { flexDirection: 'row', alignItems: 'baseline', gap: space(2) },
  travelRow: { flexDirection: 'row', gap: space(3), paddingLeft: space(2), paddingVertical: space(1) },
  notes: { marginTop: space(2), gap: space(1), paddingLeft: space(2) },
});
