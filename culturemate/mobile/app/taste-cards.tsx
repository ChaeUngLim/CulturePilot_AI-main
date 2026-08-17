/**
 * 취향 카드 등록 (UR-01 · FR-25) · 3지 반응 (UR-31 · 기획안 2.4-③).
 *
 * 이 화면이 있는 이유는 하나다 — 아카이브가 0건인 첫 사용자는 `personal_score` 가
 * 0.5 로 고정돼 개인화가 사실상 꺼진 채로 첫인상을 만든다. 방문 기록이 쌓이기 전
 * 구간을 «말로 밝힌 취향»으로 받는다.
 *
 * 왜 탭이 아니라 모달인가 — 카드 넘기기는 한 번 하고 마는 일이지 매일 들르는
 * 목적지가 아니다. 탭을 하나 늘리면 다섯 탭 전부가 한 칸씩 좁아지는 대가를
 * 두 번째 실행부터는 아무도 안 쓰는 화면에 치르게 된다. 취향 탭에서 들어온다.
 */
import { useRouter } from 'expo-router';
import { useCallback, useEffect, useState } from 'react';
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';

import { fetchPreferenceCards, savePreferenceCards } from '@/api/client';
import type { PreferenceCard, Verdict } from '@/api/types';
import { Button, Card } from '@/components/ui';
import { ErrorBoundary } from '@/components/ErrorBoundary';
import { colors, radius, space, type } from '@/theme';

/**
 * 카드 더미.
 *
 * ★ `scripts/_catalog_data.py` 가 실제로 쓰는 카테고리 이름과 **글자까지 같아야 한다.**
 * 여기서 «전시»라고 물어보고 카탈로그가 «전시관»으로 저장돼 있으면, 사용자가 좋다고
 * 누른 취향이 어떤 후보와도 매칭되지 않아 점수에 아무 영향을 주지 못한다.
 * 그러면 화면은 동작하는데 개인화만 조용히 꺼져 있는, 가장 찾기 어려운 상태가 된다.
 */
const DECK: { subject: string; glyph: string; blurb: string }[] = [
  { subject: '미술관', glyph: '◈', blurb: '기획전과 상설전, 조용히 걷는 관람' },
  { subject: '박물관', glyph: '▲', blurb: '역사와 유물, 해설이 있는 전시' },
  { subject: '공연장', glyph: '♪', blurb: '연극·클래식·뮤지컬' },
  { subject: '독립서점', glyph: '▤', blurb: '책방 큐레이션과 북토크' },
  { subject: '독립영화관', glyph: '▣', blurb: '예술영화·다양성 영화 상영관' },
  { subject: '공방', glyph: '✂', blurb: '직접 만들어 보는 원데이 클래스' },
  { subject: '복합문화공간', glyph: '◍', blurb: '전시와 카페, 편집숍이 함께' },
  { subject: '갤러리', glyph: '□', blurb: '작은 전시, 신진 작가' },
  { subject: '야외공연장', glyph: '☀', blurb: '야외 무대와 페스티벌' },
  { subject: '거리', glyph: '⌇', blurb: '걷기 좋은 골목과 거리 풍경' },
  { subject: '도서관', glyph: '▥', blurb: '머무르며 읽는 공간' },
  { subject: '문화유산', glyph: '⌂', blurb: '고궁·한옥·유적' },
];

/**
 * 3지 반응 (기획안 2.4-③).
 *
 * 서버 스키마는 verdict 4값 + experienced 를 갖는다. «가봤어요»만 experienced 를
 * 켜서 두 축을 그대로 쓴다 — 겪고 내린 판단은 말로만 밝힌 기대보다 무겁게 반영된다.
 */
const REACTIONS: {
  key: string; label: string; verdict: Verdict; experienced: boolean; tone: string;
}[] = [
  { key: 'interested', label: '기대돼요', verdict: 'interested', experienced: false, tone: colors.accent },
  { key: 'been', label: '가봤어요', verdict: 'recommend', experienced: true, tone: colors.ok },
  { key: 'no', label: '관심 없어요', verdict: 'not_interested', experienced: false, tone: colors.textFaint },
];

function reactionOf(card: PreferenceCard | undefined) {
  if (!card) return null;
  return REACTIONS.find(
    (r) => r.verdict === card.verdict && r.experienced === card.experienced) ?? null;
}

function TasteCardsScreen() {
  const router = useRouter();
  const [picked, setPicked] = useState<Record<string, PreferenceCard>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [failed, setFailed] = useState(false);

  // 이미 평가한 카드를 먼저 읽는다. 안 그러면 두 번째로 들어온 사용자에게
  // 지난번에 답한 것을 처음부터 다시 물어보게 된다.
  useEffect(() => {
    void (async () => {
      const saved = await fetchPreferenceCards();
      setPicked(Object.fromEntries(saved.map((c) => [c.subject, c])));
      setLoading(false);
    })();
  }, []);

  const choose = useCallback((subject: string, r: (typeof REACTIONS)[number]) => {
    setFailed(false);
    setPicked((prev) => {
      const cur = prev[subject];
      // 같은 반응을 다시 누르면 취소다. 잘못 누른 카드를 되돌릴 방법이 없으면
      // 사용자는 틀린 취향을 그대로 두고 나간다.
      if (cur && cur.verdict === r.verdict && cur.experienced === r.experienced) {
        const { [subject]: _drop, ...rest } = prev;
        return rest;
      }
      return { ...prev, [subject]: { subject, verdict: r.verdict, experienced: r.experienced } };
    });
  }, []);

  const count = Object.keys(picked).length;

  const save = async () => {
    if (count === 0) return;
    setSaving(true);
    const res = await savePreferenceCards(Object.values(picked));
    setSaving(false);
    if (!res) {
      // 저장이 안 됐는데 화면을 닫으면 사용자는 다시 카드를 넘기지 않는다.
      setFailed(true);
      return;
    }
    router.back();
  };

  if (loading) {
    return (
      <View style={s.center}>
        <ActivityIndicator color={colors.accent} />
        <Text style={[type.small, { marginTop: space(3) }]}>취향 카드를 불러오는 중…</Text>
      </View>
    );
  }

  return (
    <View style={{ flex: 1, backgroundColor: colors.bg }}>
      <ScrollView contentContainerStyle={s.content}>
        <Card>
          <Text style={type.h3}>어떤 문화생활을 좋아하세요?</Text>
          <Text style={[type.small, { marginTop: space(2) }]}>
            방문 기록이 쌓이기 전에도 추천을 맞춰 드리기 위해 물어봅니다.
            고른 것만 반영되고, 언제든 다시 바꿀 수 있어요.
          </Text>
        </Card>

        {DECK.map(({ subject, glyph, blurb }) => {
          const active = reactionOf(picked[subject]);
          return (
            <Card key={subject}>
              <View style={s.head}>
                <Text style={s.glyph}>{glyph}</Text>
                <View style={{ flex: 1 }}>
                  <Text style={type.h3}>{subject}</Text>
                  <Text style={[type.tiny, { marginTop: space(1) }]}>{blurb}</Text>
                </View>
              </View>
              <View style={s.reactions}>
                {REACTIONS.map((r) => {
                  const on = active?.key === r.key;
                  return (
                    <Pressable
                      key={r.key}
                      onPress={() => choose(subject, r)}
                      style={({ pressed }) => [
                        s.reaction,
                        on && { borderColor: r.tone, backgroundColor: colors.surfaceAlt },
                        pressed && { opacity: 0.7 },
                      ]}
                    >
                      <Text style={[type.small, { color: on ? r.tone : colors.textDim,
                                                  fontWeight: on ? '700' : '400' }]}>
                        {r.label}
                      </Text>
                    </Pressable>
                  );
                })}
              </View>
            </Card>
          );
        })}
      </ScrollView>

      <View style={s.footer}>
        {failed && (
          <Text style={[type.small, { color: colors.danger, marginBottom: space(2) }]}>
            저장하지 못했습니다. 서버 연결을 확인하고 다시 눌러 주세요.
          </Text>
        )}
        <Button
          label={count === 0 ? '카드를 골라 주세요' : `${count}개 반영하기`}
          onPress={save}
          disabled={count === 0}
          loading={saving}
        />
      </View>
    </View>
  );
}

const s = StyleSheet.create({
  content: { padding: space(4), gap: space(3), paddingBottom: space(6) },
  center: { flex: 1, backgroundColor: colors.bg, alignItems: 'center', justifyContent: 'center' },
  head: { flexDirection: 'row', alignItems: 'center', gap: space(3) },
  glyph: {
    fontSize: 22, color: colors.accent, width: 40, height: 40, borderRadius: radius.md,
    backgroundColor: colors.surfaceAlt, textAlign: 'center', lineHeight: 40,
  },
  reactions: { flexDirection: 'row', gap: space(2), marginTop: space(3) },
  reaction: {
    flex: 1, paddingVertical: space(2), borderRadius: radius.md, borderWidth: 1,
    borderColor: colors.border, alignItems: 'center',
  },
  footer: {
    padding: space(4), borderTopWidth: 1, borderTopColor: colors.border,
    backgroundColor: colors.surface,
  },
});

export default function Screen() {
  return (
    <ErrorBoundary screen="취향 카드">
      <TasteCardsScreen />
    </ErrorBoundary>
  );
}
