import { useEffect, useState } from 'react';
import { Stack } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { View } from 'react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import { onApiUrlChange, setApiUrl } from '@/config';
import { CultureMateProvider } from '@/hooks/context';
import { loadServerUrl } from '@/store/storage';
import { colors } from '@/theme';

export default function RootLayout() {
  // 저장된 서버 주소를 먼저 적용한다. 이걸 기다리지 않으면 첫 화면이 빌드타임
  // 기본값(대개 목 모드)으로 한 번 뜬 뒤 바뀌어, 사용자는 앱이 스스로 모드를
  // 갈아탄 것처럼 본다.
  const [ready, setReady] = useState(false);

  // 주소가 바뀌면 트리를 통째로 다시 세운다. 대화 스레드·확정 일정은 «어느
  // 서버의» 것인지에 매여 있어서, 서버만 갈아 끼우고 화면 상태를 두면 이전
  // 서버에서 만든 일정이 그대로 남는다.
  const [generation, setGeneration] = useState(0);

  useEffect(() => {
    void (async () => {
      const stored = await loadServerUrl();
      if (stored !== null) setApiUrl(stored);
      setReady(true);
    })();
    return onApiUrlChange(() => setGeneration((g) => g + 1));
  }, []);

  if (!ready) return <View style={{ flex: 1, backgroundColor: colors.bg }} />;

  return (
    <SafeAreaProvider>
      <CultureMateProvider key={generation}>
        <StatusBar style="light" />
        <Stack
          screenOptions={{
            headerStyle: { backgroundColor: colors.bg },
            headerTintColor: colors.text,
            headerTitleStyle: { fontWeight: '700' },
            contentStyle: { backgroundColor: colors.bg },
          }}
        >
          <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
          <Stack.Screen name="visit" options={{ title: '관람 기록 추가', presentation: 'modal' }} />
          <Stack.Screen name="taste-cards" options={{ title: '취향 카드', presentation: 'modal' }} />
          <Stack.Screen name="connect" options={{ title: '서버 연결', presentation: 'modal' }} />
        </Stack>
      </CultureMateProvider>
    </SafeAreaProvider>
  );
}
