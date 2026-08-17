/**
 * 화면 단위 오류 경계.
 *
 * RN은 렌더 중 예외가 나면 화면 전체가 빨간 에러로 덮인다. 원인을 알 수 없는 상태로
 * 앱이 멈추는 대신, 어느 화면에서 무엇이 터졌는지 보여주고 나머지는 계속 쓰게 한다.
 */
import { Component, type ReactNode } from 'react';
import { ScrollView, StyleSheet, Text, View } from 'react-native';

import { Button, Card } from '@/components/ui';
import { colors, space, type } from '@/theme';

type Props = { children: ReactNode; screen: string };
type State = { error: Error | null };

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: { componentStack?: string | null }) {
    console.error(`[${this.props.screen}]`, error, info.componentStack);
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;

    return (
      <ScrollView style={{ flex: 1, backgroundColor: colors.bg }}
                  contentContainerStyle={s.wrap}>
        <Card style={{ borderColor: colors.danger }}>
          <Text style={[type.h3, { color: colors.danger }]}>
            {this.props.screen} 화면에서 문제가 발생했습니다
          </Text>
          <Text style={[type.small, { marginTop: space(2) }]}>{error.message}</Text>
          {!!error.stack && (
            <View style={s.stack}>
              <Text style={type.tiny} numberOfLines={12}>{error.stack}</Text>
            </View>
          )}
          <Button label="다시 시도" variant="outline" style={{ marginTop: space(4) }}
                  onPress={() => this.setState({ error: null })} />
        </Card>
      </ScrollView>
    );
  }
}

const s = StyleSheet.create({
  wrap: { padding: space(4), paddingTop: space(8) },
  stack: {
    marginTop: space(3), padding: space(3),
    backgroundColor: colors.surfaceAlt, borderRadius: 8,
  },
});
