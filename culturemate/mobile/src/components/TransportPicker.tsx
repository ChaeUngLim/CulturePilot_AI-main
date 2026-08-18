/**
 * 이동수단 선택.
 *
 * 두 가지가 동시에 성립해야 한다.
 *   · 손으로 고를 수 있다 — 체크된 수단으로 거리·시간을 다시 계산한다.
 *   · 말로 해도 바뀐다 — "도보로 짜줘"라고 입력하면 서버가 해석한 수단으로
 *     이 컨트롤이 따라 움직인다. 화면과 실제 계산이 어긋나면 사용자는
 *     둘 중 뭘 믿어야 할지 알 수 없다.
 */
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';

import type { Transport } from '@/api/types';
import { colors, radius, space, type } from '@/theme';

/**
 * 기본은 최단루트. 구간마다 가장 빠른 수단을 조합한다.
 * 항상 하나는 선택돼 있어야 지도와 목록의 기준이 분명해진다.
 */
export const DEFAULT_TRANSPORT: Transport = 'best';

export const TRANSPORT_OPTIONS: { value: Transport; icon: string; label: string }[] = [
  { value: 'best',    icon: '✨', label: '최단루트' },
  { value: 'car',     icon: '🚗', label: '자가용' },
  { value: 'walk',    icon: '🚶', label: '도보' },
  { value: 'subway',  icon: '🚈', label: '지하철' },
  { value: 'bus',     icon: '🚌', label: '버스' },
];

export const TRANSPORT_LABEL: Record<string, string> = Object.fromEntries(
  TRANSPORT_OPTIONS.map((o) => [o.value, o.label]),
);

/** 수단별 소요시간을 한 줄로. 최단루트가 실제로 무엇을 섞었는지 보여준다. */
export function transportMix(mix?: Record<string, number> | null): string {
  const entries = Object.entries(mix ?? {}).filter(([, v]) => v > 0);
  if (entries.length === 0) return '';
  return entries
    .sort((a, b) => b[1] - a[1])
    .map(([k, v]) => `${TRANSPORT_LABEL[k] ?? k} ${v}분`)
    .join(' · ');
}

export function TransportPicker({
  value, onChange, autoDetected, busy, mix, disabled,
}: {
  /** 계산 결과의 수단 구성 — 최단루트일 때 무엇이 섞였는지 밝힌다 */
  mix?: Record<string, number> | null;
  value: Transport;
  onChange: (next: Transport) => void;
  /** 재계산 중 — 지도와 목록이 아직 이전 수단 기준이라는 뜻 */
  busy?: boolean;
  /** 방금 발화에서 자동으로 잡힌 수단 — 사용자가 고른 게 아니라는 걸 알려준다 */
  autoDetected?: boolean;
  disabled?: boolean;
}) {
  return (
    <View style={{ gap: space(1) }}>
      <ScrollView horizontal showsHorizontalScrollIndicator={false}
                  contentContainerStyle={s.row}>
        <Text style={[type.tiny, s.lead]}>{'이동'}</Text>
        {TRANSPORT_OPTIONS.map((o) => {
          const on = value === o.value;
          return (
            <Pressable
              key={o.value}
              // 항상 하나는 켜져 있다. 같은 걸 다시 눌러도 해제되지 않는다 —
              // 기준이 없는 상태가 되면 지도의 숫자가 무엇인지 알 수 없다.
              onPress={() => !on && onChange(o.value)}
              disabled={disabled}
              hitSlop={4}
              style={({ pressed }) => [
                s.chip,
                on && { backgroundColor: colors.accent, borderColor: colors.accent },
                pressed && { opacity: 0.6 },
              ]}
            >
              <Text style={[type.small, {
                color: on ? '#0F1115' : colors.text,
                fontWeight: on ? '700' : '500',
              }]}>
                {`${o.icon} ${o.label}`}
              </Text>
            </Pressable>
          );
        })}
      </ScrollView>
      <Text style={[type.tiny, autoDetected && { color: colors.accent }]}>
        {busy
          ? `${TRANSPORT_LABEL[value]} 기준으로 다시 계산하는 중…`
          : autoDetected
            ? `말씀하신 내용에서 '${TRANSPORT_LABEL[value]}'로 인식했어요`
            : value === 'best'
              ? transportMix(mix)
                ? `구간마다 가장 빠른 수단 — ${transportMix(mix)}`
                : '구간마다 가장 빠른 수단을 조합합니다 · 주차 사정까지 반영해요'
              : `${TRANSPORT_LABEL[value]}만으로 계산합니다`}
      </Text>
    </View>
  );
}

const s = StyleSheet.create({
  row: { flexDirection: 'row', alignItems: 'center', gap: space(2), paddingRight: space(4) },
  lead: { marginRight: space(1) },
  chip: {
    paddingHorizontal: space(3), paddingVertical: space(2),
    minHeight: 36, justifyContent: 'center',
    borderRadius: radius.xl, borderWidth: 1, borderColor: colors.border,
    backgroundColor: colors.surface,
  },
});
