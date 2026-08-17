/**
 * 취향 리포트 (UR-15).
 *
 * 수치는 서버 SQL 집계값을 그대로 그린다. 클라이언트가 재계산하면
 * '리포트가 지어낸 숫자'라는 문제가 서버에서 앱으로 옮겨올 뿐이다.
 */
import { useCallback, useState } from 'react';
import {
  ActivityIndicator, Pressable, RefreshControl, ScrollView, StyleSheet, Text, View,
} from 'react-native';

import { fetchReport } from '@/api/client';
import type { TasteReport } from '@/api/types';
import { Card, Chip, Empty } from '@/components/ui';
import { FRICTION_LABEL } from '@/constants';
import { useCM } from '@/hooks/context';
import { ErrorBoundary } from '@/components/ErrorBoundary';
import { colors, radius, space, type } from '@/theme';
import { Link, useFocusEffect } from 'expo-router';

function Bar({ label, value, tone = colors.accent }: { label: string; value: number; tone?: string }) {
  return (
    <View style={{ gap: space(1) }}>
      <View style={s.barHead}>
        <Text style={type.small}>{label}</Text>
        <Text style={type.tiny}>{(value * 100).toFixed(0)}%</Text>
      </View>
      <View style={s.barBg}>
        <View style={[s.barFill, { width: `${Math.min(100, value * 100)}%`, backgroundColor: tone }]} />
      </View>
    </View>
  );
}

function Scale({ label, value, left, right }: { label: string; value: number; left: string; right: string }) {
  const pct = ((value + 1) / 2) * 100;
  return (
    <View style={{ gap: space(2) }}>
      <Text style={type.small}>{label}</Text>
      <View style={s.scaleTrack}>
        <View style={[s.scaleDot, { left: `${pct}%` }]} />
      </View>
      <View style={s.barHead}>
        <Text style={type.tiny}>{left}</Text>
        <Text style={type.tiny}>{right}</Text>
      </View>
    </View>
  );
}

function ReportScreen() {
  const cm = useCM();
  const [report, setReport] = useState<TasteReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setReport(await fetchReport(cm.threadId));
    } catch (e) {
      // 서버가 죽었는지, 응답 형태가 다른지 화면에서 바로 알 수 있어야 한다
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [cm.threadId]);

  useFocusEffect(
    useCallback(() => {
      if (!report) void load();
    }, [load, report]),
  );

  if (loading && !report) {
    return (
      <View style={s.center}>
        <ActivityIndicator color={colors.accent} />
        <Text style={[type.small, { marginTop: space(3) }]}>누적 기록을 집계하고 있어요…</Text>
      </View>
    );
  }

  if (error) {
    return (
      <ScrollView style={{ flex: 1, backgroundColor: colors.bg }}
                  contentContainerStyle={s.content}>
        <Card style={{ borderColor: colors.danger }}>
          <Text style={[type.h3, { color: colors.danger }]}>리포트를 불러오지 못했습니다</Text>
          <Text style={[type.small, { marginTop: space(2) }]}>{error}</Text>
          <Text style={[type.tiny, { marginTop: space(3) }]}>
            서버가 켜져 있는지, 주소가 맞는지 확인해 주세요.
          </Text>
          <Link href="/connect" asChild>
            <Pressable hitSlop={8} style={({ pressed }) => [{ marginTop: space(2), opacity: pressed ? 0.6 : 1 }]}>
              <Text style={[type.small, { color: colors.accent }]}>서버 연결 확인하기 ›</Text>
            </Pressable>
          </Link>
        </Card>
      </ScrollView>
    );
  }

  // 콜드 스타트 (UR-01). 기록이 0건인 사용자에게 «쌓일 때까지 기다리라»고만 하면
  // 개인화가 꺼진 채로 첫인상이 끝난다. 지금 당장 취향을 말할 수단을 같이 준다.
  if (!report) {
    return (
      <View style={{ flex: 1, backgroundColor: colors.bg }}>
        <Empty title="리포트를 만들 기록이 부족해요" body="방문 기록이 쌓이면 취향 분석을 보여드립니다." />
        <View style={{ paddingHorizontal: space(4), paddingBottom: space(8), gap: space(2) }}>
          <Text style={[type.small, { textAlign: 'center' }]}>
            기다리지 않고 지금 알려주셔도 돼요.
          </Text>
          <Link href="/taste-cards" asChild>
            <Pressable
              style={({ pressed }) => [s.cardsCta, pressed && { opacity: 0.75 }]}>
              <Text style={[type.body, { fontWeight: '600', color: '#0F1115' }]}>
                카드로 취향 고르기
              </Text>
            </Pressable>
          </Link>
        </View>
      </View>
    );
  }

  const st = report.stats ?? ({} as TasteReport['stats']);
  // 음수는 «선호 문화 콘텐츠»가 아니다 — 취향 카드의 «관심 없어요»가 음수로 내려온다(UR-01).
  const cats = Object.entries(st.preferred_categories ?? {})
    .filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]);
  const frictions = Object.entries(st.friction_sensitivity ?? {}).sort((a, b) => b[1] - a[1]);

  return (
    <ScrollView
      style={{ backgroundColor: colors.bg }}
      contentContainerStyle={s.content}
      refreshControl={<RefreshControl refreshing={loading} onRefresh={load} tintColor={colors.accent} />}
    >
      {cats.length > 0 && (
        <Card>
          <Text style={type.h3}>선호 문화 콘텐츠</Text>
          <View style={{ gap: space(3), marginTop: space(3) }}>
            {cats.map(([k, v]) => <Bar key={k} label={k} value={v} />)}
          </View>
        </Card>
      )}

      {(st.avg_travel_min != null || st.avg_dwell_min != null) && (
        <Card>
          <Text style={type.h3}>이동과 체류</Text>
          <View style={s.statRow}>
            <View style={s.stat}>
              <Text style={[type.h2, { color: colors.accent }]}>
                {st.avg_travel_min?.toFixed(0) ?? '-'}<Text style={type.tiny}> 분</Text>
              </Text>
              <Text style={type.tiny}>평균 이동</Text>
            </View>
            <View style={s.stat}>
              <Text style={[type.h2, { color: colors.accent }]}>
                {st.avg_dwell_min?.toFixed(0) ?? '-'}<Text style={type.tiny}> 분</Text>
              </Text>
              <Text style={type.tiny}>평균 체류</Text>
            </View>
          </View>
        </Card>
      )}

      <Card>
        <Text style={type.h3}>성향</Text>
        <View style={{ gap: space(5), marginTop: space(4) }}>
          <Scale label="실내 / 야외" value={st.indoor_bias ?? 0} left="야외 선호" right="실내 선호" />
          <Scale label="재방문 / 신규 탐색" value={st.novelty_bias ?? 0} left="재방문 선호" right="새로운 곳 선호" />
        </View>
      </Card>

      {frictions.length > 0 && (
        <Card>
          <Text style={type.h3}>주요 불편 요소</Text>
          <Text style={[type.small, { marginTop: space(1) }]}>
            비중이 높은 항목일수록 일정에서 먼저 경고해 드립니다.
          </Text>
          <View style={{ gap: space(3), marginTop: space(3) }}>
            {frictions.map(([k, v]) => (
              <Bar key={k} label={FRICTION_LABEL[k as never] ?? k} value={v} tone={colors.danger} />
            ))}
          </View>
        </Card>
      )}

      {/* 기록이 있어도 카드는 다시 열 수 있어야 한다 — 취향은 바뀌고, 잘못 누른
          카드를 되돌릴 길이 없으면 틀린 취향이 계속 추천에 실린다 (UR-01). */}
      <Link href="/taste-cards" asChild>
        <Pressable hitSlop={8} style={({ pressed }) => [{ opacity: pressed ? 0.6 : 1 }]}>
          <Text style={[type.small, { color: colors.accent, textAlign: 'center' }]}>
            카드로 취향 고치기 ›
          </Text>
        </Pressable>
      </Link>

      {!!report.narrative && (
        <Card>
          <View style={{ flexDirection: 'row', justifyContent: 'space-between' }}>
            <Text style={type.h3}>요약</Text>
            <Chip label="AI 생성" tone="accent" />
          </View>
          <Text style={[type.body, { marginTop: space(3) }]}>{report.narrative}</Text>
        </Card>
      )}
    </ScrollView>
  );
}

const s = StyleSheet.create({
  content: { padding: space(4), gap: space(3), paddingBottom: space(10) },
  center: { flex: 1, backgroundColor: colors.bg, alignItems: 'center', justifyContent: 'center' },
  barHead: { flexDirection: 'row', justifyContent: 'space-between' },
  barBg: { height: 8, borderRadius: 4, backgroundColor: colors.surfaceAlt, overflow: 'hidden' },
  barFill: { height: 8, borderRadius: 4 },
  scaleTrack: {
    height: 6, borderRadius: 3, backgroundColor: colors.surfaceAlt, position: 'relative',
    marginHorizontal: space(2),
  },
  scaleDot: {
    position: 'absolute', top: -5, width: 16, height: 16, borderRadius: 8,
    backgroundColor: colors.accent, marginLeft: -8,
  },
  cardsCta: {
    backgroundColor: colors.accent, borderRadius: radius.md,
    paddingVertical: space(3), alignItems: 'center',
  },
  statRow: { flexDirection: 'row', gap: space(4), marginTop: space(3) },
  stat: {
    flex: 1, backgroundColor: colors.surfaceAlt, borderRadius: radius.md,
    padding: space(4), alignItems: 'center', gap: space(1),
  },
});


export default function Screen() {
  return (
    <ErrorBoundary screen="취향">
      <ReportScreen />
    </ErrorBoundary>
  );
}
