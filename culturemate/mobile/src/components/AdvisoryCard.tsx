/**
 * HITL 확인 카드.
 *
 * 설계 규칙 두 개를 UI에서도 강제한다.
 *   1) 첫 선택지는 항상 '그대로' — 변경이 기본값이면 사실상 자동 변경 승인이 된다.
 *   2) 모든 선택지에 예상 효과를 표시한다. 근거 없이 고르게 하지 않는다.
 */
import * as Haptics from 'expo-haptics';
import { Platform, Pressable, StyleSheet, Text, View } from 'react-native';

import type { Advisory, AdvisoryOption } from '@/api/types';
import { Chip } from '@/components/ui';
import { colors, radius, severityColor, severitySoft, space, type } from '@/theme';

const KIND_LABEL: Record<Advisory['kind'], string> = {
  friction: '과거 불편 기록',
  revisit_diff: '재방문 변경사항',
  conflict: '일정 충돌',
  weather: '날씨',
  budget: '예산',
};

export function AdvisoryCard({
  advisory, selectedOptionId, onSelect, onShowEvidence,
}: {
  advisory: Advisory;
  selectedOptionId?: string;
  onSelect: (option: AdvisoryOption) => void;
  onShowEvidence: (ids: string[]) => void;
}) {
  const accent = severityColor(advisory.severity);

  const pick = (o: AdvisoryOption) => {
    if (Platform.OS !== 'web') void Haptics.selectionAsync();
    onSelect(o);
  };

  return (
    <View style={[s.card, { borderColor: accent }]}>
      <View style={[s.head, { backgroundColor: severitySoft(advisory.severity) }]}>
        <Chip label={KIND_LABEL[advisory.kind]} tone={advisory.severity >= 3 ? 'danger' : 'warn'} />
        <Text style={[type.h3, { marginTop: space(2) }]}>{advisory.title}</Text>
      </View>

      <View style={s.body}>
        <Text style={type.body}>{advisory.message}</Text>

        {advisory.evidence_ids.length > 0 && (
          <Pressable onPress={() => onShowEvidence(advisory.evidence_ids)} style={s.evidenceBtn}>
            <Text style={[type.small, { color: colors.accent, fontWeight: '600' }]}>
              판단 근거 {advisory.evidence_ids.length}건 보기 →
            </Text>
          </Pressable>
        )}

        <View style={{ gap: space(2), marginTop: space(3) }}>
          {advisory.options.map((o) => {
            const selected = selectedOptionId === o.id;
            return (
              <Pressable
                key={o.id}
                onPress={() => pick(o)}
                style={({ pressed }) => [
                  s.option,
                  selected && { borderColor: accent, backgroundColor: severitySoft(advisory.severity) },
                  pressed && { opacity: 0.8 },
                ]}
              >
                <View style={[s.radio, selected && { borderColor: accent }]}>
                  {selected && <View style={[s.radioDot, { backgroundColor: accent }]} />}
                </View>
                <View style={{ flex: 1 }}>
                  <Text style={[type.body, { fontWeight: selected ? '700' : '500' }]}>{o.label}</Text>
                  {!!o.predicted_effect && (
                    <Text style={[type.small, { marginTop: space(1) }]}>{o.predicted_effect}</Text>
                  )}
                </View>
              </Pressable>
            );
          })}
        </View>
      </View>
    </View>
  );
}

const s = StyleSheet.create({
  card: {
    borderRadius: radius.lg,
    borderWidth: 1,
    overflow: 'hidden',
    backgroundColor: colors.surface,
  },
  head: { padding: space(4), paddingBottom: space(3) },
  body: { padding: space(4) },
  evidenceBtn: { marginTop: space(3) },
  option: {
    flexDirection: 'row',
    gap: space(3),
    alignItems: 'flex-start',
    padding: space(4),
    minHeight: 56,          // 모바일에서 한 손으로 정확히 누를 수 있는 크기
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surfaceAlt,
  },
  radio: {
    width: 22, height: 22, borderRadius: 11, borderWidth: 2,
    borderColor: colors.textFaint, alignItems: 'center', justifyContent: 'center',
    marginTop: space(0.5),
  },
  radioDot: { width: 10, height: 10, borderRadius: 5 },
});
