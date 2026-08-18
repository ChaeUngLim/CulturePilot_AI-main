/**
 * 아카이브 — 방문 기록 목록.
 *
 * 이 화면이 개인화 순환의 입력이다. 여기 쌓인 기록이 다음 일정의 경고와 대안이 된다.
 * 그래서 별점뿐 아니라 '무엇이 불편했는지'를 태그로 반드시 받는다.
 */
import { useFocusEffect, useRouter } from 'expo-router';
import { useCallback, useState } from 'react';
import { FlatList, Pressable, StyleSheet, Text, View } from 'react-native';

import { ErrorBoundary } from '@/components/ErrorBoundary';
import { Button, Card, Chip, Empty } from '@/components/ui';
import { FRICTION_LABEL } from '@/constants';
import { useCM } from '@/hooks/context';
import { loadVisits, type StoredVisit } from '@/store/storage';
import { colors, space, type } from '@/theme';

function ArchiveScreen() {
  const [visits, setVisits] = useState<StoredVisit[]>([]);
  const router = useRouter();
  const cm = useCM();

  useFocusEffect(
    useCallback(() => {
      void loadVisits().then(setVisits);
    }, []),
  );

  const planned = cm.itinerary?.items ?? [];

  return (
    <FlatList
      style={{ backgroundColor: colors.bg }}
      contentContainerStyle={s.content}
      data={visits}
      keyExtractor={(v) => v.id}
      ListHeaderComponent={
        <View style={{ gap: space(3) }}>
          {planned.length > 0 && (
            <Card>
              <Text style={type.h3}>오늘 일정에서 기록하기</Text>
              <Text style={[type.small, { marginTop: space(1) }]}>
                다녀온 곳의 경험을 남기면 다음 추천에 반영됩니다.
              </Text>
              <View style={s.planRow}>
                {planned.map((i) => (
                  <Pressable
                    key={i.seq}
                    onPress={() =>
                      router.push({
                        pathname: '/visit',
                        params: { place_id: i.place_id ?? '', place_name: i.name },
                      })
                    }
                    style={({ pressed }) => [s.planChip, pressed && { opacity: 0.7 }]}
                  >
                    <Text style={[type.small, { color: colors.accent }]}>+ {i.name}</Text>
                  </Pressable>
                ))}
              </View>
            </Card>
          )}

          <Button
            label="관람 기록 직접 추가"
            variant="outline"
            onPress={() => router.push('/visit')}
          />

          {visits.length > 0 && (
            <Text style={[type.h3, { marginTop: space(2) }]}>기록 {visits.length}건</Text>
          )}
        </View>
      }
      ListEmptyComponent={
        <Empty
          title="아직 기록이 없어요"
          body="방문한 곳의 만족도와 불편했던 점을 남기면, 다음 일정에서 미리 알려드릴 수 있어요."
        />
      }
      renderItem={({ item }) => (
        <Card style={{ marginTop: space(3) }}>
          <View style={s.head}>
            <Text style={type.h3}>{item.place_name}</Text>
            {!item.synced && <Chip label="동기화 대기" tone="warn" />}
          </View>
          <Text style={[type.tiny, { marginTop: space(1) }]}>
            {`${new Date(item.visited_at).toLocaleDateString('ko-KR')}`
              + `${item.companions ? ` · ${item.companions}` : ''}`
              + `${item.transport ? ` · ${item.transport}` : ''}`}
          </Text>
          {item.rating != null && (
            <Text style={[type.body, { marginTop: space(2), color: colors.warn }]}>
              {'★'.repeat(Math.round(item.rating)) }
              <Text style={{ color: colors.textFaint }}>
                {'★'.repeat(Math.max(0, 5 - Math.round(item.rating)))}
              </Text>
            </Text>
          )}
          {!!item.review && (
            <Text style={[type.small, { marginTop: space(2) }]}>{item.review}</Text>
          )}
          {item.friction.length > 0 && (
            <View style={s.chips}>
              {item.friction.map((f) => (
                <Chip key={f} label={FRICTION_LABEL[f] ?? f} tone="danger" />
              ))}
            </View>
          )}
        </Card>
      )}
    />
  );
}

const s = StyleSheet.create({
  content: { padding: space(4), paddingBottom: space(10) },
  head: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: space(1), marginTop: space(3) },
  planRow: { flexDirection: 'row', flexWrap: 'wrap', gap: space(2), marginTop: space(3) },
  planChip: {
    paddingHorizontal: space(3), paddingVertical: space(2),
    borderRadius: 20, borderWidth: 1, borderColor: colors.accent,
    backgroundColor: colors.accentSoft,
  },
});


export default function Screen() {
  return (
    <ErrorBoundary screen="아카이브">
      <ArchiveScreen />
    </ErrorBoundary>
  );
}
