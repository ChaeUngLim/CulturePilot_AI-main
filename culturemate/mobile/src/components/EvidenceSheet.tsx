/** 판단 근거 원문 시트 (UR-14). 목록에는 id만 오고 여기서 지연 로드한다. */
import { useEffect, useState } from 'react';
import { ActivityIndicator, Linking, Modal, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';

import type { Evidence } from '@/api/types';
import { Chip } from '@/components/ui';
import { colors, radius, space, type } from '@/theme';

const KIND: Record<Evidence['kind'], { label: string; tone: 'ok' | 'accent' | 'warn' | 'default' }> = {
  archive: { label: '내 기록', tone: 'ok' },
  official: { label: '공식 출처', tone: 'accent' },
  web: { label: '웹 검색', tone: 'default' },
  weather: { label: '날씨', tone: 'warn' },
  maps: { label: '지도', tone: 'default' },
  profile: { label: '취향 프로필', tone: 'ok' },
  rule: { label: '검증 규칙', tone: 'default' },
};

export function EvidenceSheet({
  ids, onClose, resolve,
}: {
  ids: string[] | null;
  onClose: () => void;
  resolve: (id: string) => Promise<Evidence | null>;
}) {
  const [items, setItems] = useState<Evidence[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!ids) return;
    setLoading(true);
    void Promise.all(ids.map(resolve))
      .then((r) => setItems(r.filter(Boolean) as Evidence[]))
      .finally(() => setLoading(false));
  }, [ids, resolve]);

  return (
    <Modal visible={!!ids} animationType="slide" transparent onRequestClose={onClose}>
      <Pressable style={s.backdrop} onPress={onClose} />
      <View style={s.sheet}>
        <View style={s.handle} />
        <Text style={[type.h2, { paddingHorizontal: space(5) }]}>판단 근거</Text>
        <Text style={[type.small, { paddingHorizontal: space(5), marginTop: space(1) }]}>
          이 제안에 사용된 기록과 출처입니다.
        </Text>

        {loading ? (
          <ActivityIndicator style={{ marginTop: space(8) }} color={colors.accent} />
        ) : (
          <ScrollView contentContainerStyle={s.list}>
            {items.map((e) => (
              <View key={e.id} style={s.item}>
                <View style={s.itemHead}>
                  <Chip label={KIND[e.kind]?.label ?? e.kind} tone={KIND[e.kind]?.tone ?? 'default'} />
                  <Text style={type.tiny}>신뢰도 {(e.confidence * 100).toFixed(0)}%</Text>
                </View>
                <Text style={[type.h3, { marginTop: space(2) }]}>{e.title}</Text>
                <Text style={[type.small, { marginTop: space(2) }]}>{e.text}</Text>
                {!!e.observed_at && (
                  <Text style={[type.tiny, { marginTop: space(2) }]}>
                    확인 시각 {new Date(e.observed_at).toLocaleString('ko-KR')}
                  </Text>
                )}
                {!!e.url && (
                  <Pressable onPress={() => void Linking.openURL(e.url!)}>
                    <Text style={[type.small, { color: colors.accent, marginTop: space(2) }]}>
                      원문 열기 ↗
                    </Text>
                  </Pressable>
                )}
              </View>
            ))}
            {items.length === 0 && (
              <Text style={[type.small, { textAlign: 'center', padding: space(6) }]}>
                표시할 근거가 없습니다.
              </Text>
            )}
          </ScrollView>
        )}

        <Pressable onPress={onClose} style={s.close}>
          <Text style={[type.body, { fontWeight: '600' }]}>닫기</Text>
        </Pressable>
      </View>
    </Modal>
  );
}

const s = StyleSheet.create({
  backdrop: { position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, backgroundColor: '#000A' },
  sheet: {
    position: 'absolute', bottom: 0, left: 0, right: 0, maxHeight: '82%',
    backgroundColor: colors.bg,
    borderTopLeftRadius: radius.xl, borderTopRightRadius: radius.xl,
    paddingTop: space(3), paddingBottom: space(6),
    borderTopWidth: 1, borderColor: colors.border,
  },
  handle: {
    width: 40, height: 4, borderRadius: 2, backgroundColor: colors.border,
    alignSelf: 'center', marginBottom: space(3),
  },
  list: { padding: space(5), gap: space(3) },
  item: {
    backgroundColor: colors.surface, borderRadius: radius.md,
    borderWidth: 1, borderColor: colors.border, padding: space(4),
  },
  itemHead: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  close: {
    marginHorizontal: space(5), marginTop: space(2), paddingVertical: space(3),
    borderRadius: radius.md, backgroundColor: colors.surfaceAlt, alignItems: 'center',
  },
});
