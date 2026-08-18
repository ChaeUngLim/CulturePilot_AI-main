/** 노드 진행 표시. 일정 생성은 수 초가 걸리므로 무엇이 돌고 있는지 보여준다. */
import { ActivityIndicator, StyleSheet, Text, View } from 'react-native';

import { nodeLabel, type Phase, type TraceStep } from '@/hooks/useCultureMate';
import { colors, radius, space, type } from '@/theme';

export function ProgressTrace({ trace, phase }: { trace: TraceStep[]; phase: Phase }) {
  if (phase !== 'running' && trace.length === 0) return null;
  const last = trace[trace.length - 1];

  return (
    <View style={s.wrap}>
      <View style={s.head}>
        {phase === 'running' && <ActivityIndicator size="small" color={colors.accent} />}
        <Text style={[type.small, { color: colors.accent, fontWeight: '600' }]}>
          {phase === 'running' ? (last ? nodeLabel(last.node) : '요청 분석') : '완료'}
        </Text>
      </View>
      <View style={s.dots}>
        {trace.map((t, i) => (
          <View key={`${t.node}-${i}`} style={s.step}>
            <View style={[s.dot, { backgroundColor: i === trace.length - 1 && phase === 'running' ? colors.accent : colors.ok }]} />
            <Text style={type.tiny} numberOfLines={1}>{nodeLabel(t.node)}</Text>
          </View>
        ))}
      </View>
    </View>
  );
}

const s = StyleSheet.create({
  wrap: {
    backgroundColor: colors.accentSoft,
    borderRadius: radius.md,
    padding: space(3),
    gap: space(2),
  },
  head: { flexDirection: 'row', alignItems: 'center', gap: space(2) },
  dots: { flexDirection: 'row', flexWrap: 'wrap', gap: space(3) },
  step: { flexDirection: 'row', alignItems: 'center', gap: space(1) },
  dot: { width: 6, height: 6, borderRadius: 3 },
});
