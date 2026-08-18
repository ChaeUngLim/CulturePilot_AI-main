/**
 * 캘린더 — 만든 일정을 날짜로 다시 연다 (UR-28).
 *
 * 기획안 4.3의 선순환(탐색 → 일정 → 동선 → **기록** → 분석)은 «다시 열기»가
 * 있어야 04에 도달한다. 이 화면이 없으면 앱을 닫는 순간 그날 일정으로 돌아갈
 * 길이 사라지고, 기록이 안 쌓여 다음 바퀴의 개인화가 첫 사용자와 같아진다.
 *
 * **규칙 — 여기서는 일정을 고치지 않는다.** 변경은 «오늘» 탭의 재계획 경로 하나로
 * 모은다. 편집 경로가 둘이 되면 `extract_edit_signals` 가 한쪽 수정만 보게 되어
 * 수정 행동 학습(UR-09)이 반쪽이 된다.
 */
import { useCallback, useMemo, useState } from 'react';
import {
  ActivityIndicator, Pressable, RefreshControl, ScrollView, Text, View,
} from 'react-native';
import { useFocusEffect, useRouter } from 'expo-router';

import { fetchPlan, fetchPlans } from '@/api/client';
import type { Itinerary, PlanSummary } from '@/api/types';
import { NaverMap } from '@/components/NaverMap';
import { Timeline } from '@/components/Timeline';
import { Card, Chip } from '@/components/ui';
import { colors, radius, space, type as type_ } from '@/theme';

const WEEKDAYS = ['일', '월', '화', '수', '목', '금', '토'];

function ymd(d: Date) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

/** 그 달의 1일이 무슨 요일인지에 맞춰 앞을 비운 6주 그리드. */
function monthGrid(year: number, month: number): (Date | null)[] {
  const first = new Date(year, month, 1);
  const days = new Date(year, month + 1, 0).getDate();
  const lead = first.getDay();
  const cells: (Date | null)[] = Array(lead).fill(null);
  for (let d = 1; d <= days; d += 1) cells.push(new Date(year, month, d));
  while (cells.length % 7 !== 0) cells.push(null);
  return cells;
}

export default function CalendarScreen() {
  const router = useRouter();
  const today = useMemo(() => new Date(), []);
  const [cursor, setCursor] = useState(() => new Date(today.getFullYear(), today.getMonth(), 1));
  const [plans, setPlans] = useState<PlanSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [picked, setPicked] = useState<PlanSummary | null>(null);
  const [detail, setDetail] = useState<Itinerary | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setPlans(await fetchPlans());
    } finally {
      setLoading(false);
    }
  }, []);

  // 다른 탭에서 일정을 만들고 돌아오면 바로 보여야 한다.
  useFocusEffect(useCallback(() => { void load(); }, [load]));

  /** 날짜 → 그날의 일정들. 하루에 여러 번 만들 수 있으므로 배열이다. */
  const byDate = useMemo(() => {
    const map = new Map<string, PlanSummary[]>();
    for (const p of plans) {
      if (!p.plan_date) continue;
      const list = map.get(p.plan_date) ?? [];
      list.push(p);
      map.set(p.plan_date, list);
    }
    return map;
  }, [plans]);

  const open = useCallback(async (p: PlanSummary) => {
    setPicked(p);
    setDetail(null);
    setDetailLoading(true);
    try {
      setDetail(await fetchPlan(p.id));
    } finally {
      setDetailLoading(false);
    }
  }, []);

  const cells = monthGrid(cursor.getFullYear(), cursor.getMonth());
  const todayKey = ymd(today);

  return (
    <ScrollView
      style={{ flex: 1, backgroundColor: colors.bg }}
      contentContainerStyle={{ padding: space(2), gap: space(2) }}
      refreshControl={<RefreshControl refreshing={loading} onRefresh={load} tintColor={colors.accent} />}
    >
      {/* 월 이동 */}
      <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
        <Pressable
          onPress={() => setCursor(new Date(cursor.getFullYear(), cursor.getMonth() - 1, 1))}
          hitSlop={12}
        >
          <Text style={[type_.h3, { color: colors.textDim }]}>‹</Text>
        </Pressable>
        <Text style={type_.h2}>
          {cursor.getFullYear()}년 {cursor.getMonth() + 1}월
        </Text>
        <Pressable
          onPress={() => setCursor(new Date(cursor.getFullYear(), cursor.getMonth() + 1, 1))}
          hitSlop={12}
        >
          <Text style={[type_.h3, { color: colors.textDim }]}>›</Text>
        </Pressable>
      </View>

      <Card>
        <View style={{ flexDirection: 'row' }}>
          {WEEKDAYS.map((w) => (
            <Text key={w} style={[type_.tiny, { flex: 1, textAlign: 'center', color: colors.textFaint }]}>
              {w}
            </Text>
          ))}
        </View>

        <View style={{ flexDirection: 'row', flexWrap: 'wrap', marginTop: space(1) }}>
          {cells.map((d, i) => {
            const key = d ? ymd(d) : `blank-${i}`;
            const found = d ? byDate.get(ymd(d)) : undefined;
            const isToday = d ? ymd(d) === todayKey : false;
            return (
              <Pressable
                key={key}
                disabled={!found}
                onPress={() => found && open(found[0])}
                style={{
                  width: `${100 / 7}%`, aspectRatio: 1,
                  alignItems: 'center', justifyContent: 'center',
                  borderRadius: radius.sm,
                  backgroundColor: picked && d && picked.plan_date === ymd(d)
                    ? colors.accentSoft : 'transparent',
                }}
              >
                {d && (
                  <>
                    <Text style={[
                      type_.body,
                      { color: found ? colors.text : colors.textFaint },
                      isToday && { color: colors.accent, fontWeight: '700' },
                    ]}>
                      {d.getDate()}
                    </Text>
                    {/* 일정이 있는 날만 점. 개수는 굳이 세지 않는다 —
                        캘린더에서 알아야 할 건 '있었나' 뿐이다. */}
                    {found && (
                      <View style={{
                        width: 5, height: 5, borderRadius: 3, marginTop: 2,
                        backgroundColor: colors.accent,
                      }} />
                    )}
                  </>
                )}
              </Pressable>
            );
          })}
        </View>
      </Card>

      {plans.length === 0 && !loading && (
        <Card>
          <Text style={[type_.h3, { color: colors.textDim }]}>아직 만든 일정이 없습니다</Text>
          <Text style={[type_.tiny, { marginTop: space(1) }]}>
            «오늘» 탭에서 일정을 만들면 그날 날짜에 표시되고, 여기서 다시 열 수 있습니다.
          </Text>
        </Card>
      )}

      {/* 고른 날의 일정 */}
      {picked && (
        <Card>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: space(1) }}>
            <Text style={type_.h3}>{picked.plan_date}</Text>
            {picked.stop_count != null && <Chip label={`${picked.stop_count}곳`} />}
            {picked.destination_name && <Chip label={`🏁 ${picked.destination_name}`} />}
          </View>

          {detailLoading && (
            <ActivityIndicator color={colors.accent} style={{ marginTop: space(2) }} />
          )}

          {!detailLoading && detail && (
            <View style={{ gap: space(2), marginTop: space(2) }}>
              <NaverMap itinerary={detail} height={200} />
              <Timeline itinerary={detail} />
              {/* 지난 일정인데 기록이 없으면 여기가 아카이브로 들어가는 입구다.
                  기획안 4.3이 «04 기록이 비면 05 분석이 빈 화면»이라 한 그 지점. */}
              {picked.plan_date && picked.plan_date < todayKey && (
                <Pressable
                  onPress={() => router.push('/visit')}
                  style={{
                    padding: space(2), borderRadius: radius.md,
                    backgroundColor: colors.accentSoft, alignItems: 'center',
                  }}
                >
                  <Text style={[type_.body, { color: colors.accent, fontWeight: '600' }]}>
                    ✎ 이 날의 기록 남기기
                  </Text>
                </Pressable>
              )}
            </View>
          )}

          {!detailLoading && !detail && (
            <Text style={[type_.tiny, { marginTop: space(2) }]}>
              일정을 불러오지 못했습니다. 당겨서 새로고침해 주세요.
            </Text>
          )}
        </Card>
      )}
    </ScrollView>
  );
}
