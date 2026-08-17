import { Link, Tabs } from 'expo-router';
import { Pressable, Text, type ColorValue } from 'react-native';

import { isMock } from '@/config';
import { colors, space, type } from '@/theme';

function Icon({ glyph, color }: { glyph: string; color: ColorValue }) {
  return <Text style={{ fontSize: 20, color }}>{glyph}</Text>;
}

/**
 * 헤더의 연결 상태 버튼.
 *
 * 목 모드일 때만 눈에 띄게 둔다 — 붙어 있을 때는 서버가 화제가 될 이유가 없고,
 * 안 붙어 있을 때는 그게 유일한 화제다.
 */
function ConnectionButton() {
  const mock = isMock();
  return (
    <Link href="/connect" asChild>
      <Pressable
        hitSlop={8}
        style={({ pressed }) => [
          { paddingHorizontal: space(3), opacity: pressed ? 0.6 : 1 },
        ]}
      >
        <Text style={[type.small, { color: mock ? colors.warn : colors.textFaint }]}>
          {mock ? '● 목 모드' : '● 연결됨'}
        </Text>
      </Pressable>
    </Link>
  );
}

export default function TabsLayout() {
  return (
    <Tabs
      screenOptions={{
        headerStyle: { backgroundColor: colors.bg },
        headerTintColor: colors.text,
        headerTitleStyle: { fontWeight: '700' },
        tabBarStyle: { backgroundColor: colors.surface, borderTopColor: colors.border },
        tabBarActiveTintColor: colors.accent,
        tabBarInactiveTintColor: colors.textFaint,
        sceneStyle: { backgroundColor: colors.bg },
      }}
    >
      <Tabs.Screen
        name="index"
        options={{
          title: '오늘',
          headerTitle: 'CultureMate',
          headerRight: () => <ConnectionButton />,
          tabBarIcon: ({ color }) => <Icon glyph="◎" color={color} />,
        }}
      />
      {/* 캘린더는 «오늘» 바로 옆이다. 지난 일정을 되짚는 화면이라 아카이브와
          가까워 보이지만, 사용자가 찾는 건 «내가 만든 일정»이지 «내 기록»이 아니다. */}
      <Tabs.Screen
        name="calendar"
        options={{
          title: '캘린더',
          headerTitle: '내 일정',
          tabBarIcon: ({ color }) => <Icon glyph="▦" color={color} />,
        }}
      />
      <Tabs.Screen
        name="archive"
        options={{
          title: '아카이브',
          tabBarIcon: ({ color }) => <Icon glyph="▤" color={color} />,
        }}
      />
      <Tabs.Screen
        name="curation"
        options={{
          title: '큐레이션',
          headerTitle: '큐레이션 지도',
          tabBarIcon: ({ color }) => <Icon glyph="◍" color={color} />,
        }}
      />
      <Tabs.Screen
        name="report"
        options={{
          title: '취향',
          tabBarIcon: ({ color }) => <Icon glyph="◈" color={color} />,
        }}
      />
    </Tabs>
  );
}
