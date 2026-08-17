/** 입력창 + 빠른 요청 칩. 위치가 필요한 요청은 GPS를 붙여 보낸다. */
import { useRef, useState } from 'react';
import {
  Platform, Pressable, ScrollView, StyleSheet, Text, TextInput, View,
  type NativeSyntheticEvent, type TextInputKeyPressEventData,
} from 'react-native';

import { colors, radius, space, type } from '@/theme';

export const QUICK_PROMPTS: { label: string; text: string; needsLocation?: boolean }[] = [
  { label: '하루 일정', text: '이번 주말 성수동에서 하루 문화생활 일정 짜줘' },
  { label: '조기 종료', text: '전시 일찍 끝났어. 2시간 남았는데 근처 갈 만한 곳 있어?', needsLocation: true },
  { label: '비 올 때', text: '오후에 비 온대. 실내 위주로 바꿔줘' },
  { label: '재방문', text: '작년에 갔던 그 미술관 다시 가려는데 달라진 거 있어?' },
  { label: '과거 기록', text: '내가 지난 6개월 동안 어디 다녀왔지?' },
];

export function Composer({
  onSend, disabled,
}: {
  onSend: (text: string, needsLocation?: boolean) => void;
  disabled?: boolean;
}) {
  const [text, setText] = useState('');
  const lastSent = useRef(0);

  const submit = () => {
    if (!text.trim() || disabled) return;
    // 이벤트가 중복으로 오는 경우가 있어 짧은 구간의 재전송을 막는다
    const now = Date.now();
    if (now - lastSent.current < 400) return;
    lastSent.current = now;
    onSend(text.trim());
    setText('');
  };

  /** 웹: Enter 전송, Shift+Enter 줄바꿈. 채팅 입력의 관습을 따른다. */
  const onKeyPress = (e: NativeSyntheticEvent<TextInputKeyPressEventData>) => {
    if (Platform.OS !== 'web') return;
    const native = e.nativeEvent as unknown as {
      key: string; shiftKey?: boolean; preventDefault?: () => void;
    };
    if (native.key === 'Enter' && !native.shiftKey) {
      native.preventDefault?.();
      submit();
    }
  };

  return (
    <View style={s.wrap}>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={s.chips}>
        {QUICK_PROMPTS.map((q) => (
          <Pressable
            key={q.label}
            onPress={() => onSend(q.text, q.needsLocation)}
            disabled={disabled}
            style={({ pressed }) => [s.chip, (pressed || disabled) && { opacity: 0.6 }]}
          >
            <Text style={[type.small, { color: colors.text }]}>
              {q.needsLocation ? '📍 ' : ''}{q.label}
            </Text>
          </Pressable>
        ))}
      </ScrollView>

      {Platform.OS === 'web' && (
        <Text style={[type.tiny, { paddingHorizontal: space(4) }]}>
          {'Enter 로 전송 · Shift+Enter 줄바꿈'}
        </Text>
      )}

      <View style={s.inputRow}>
        <TextInput
          value={text}
          onChangeText={setText}
          // 웹에서 Enter 는 onKeyPress 와 onSubmitEditing 을 모두 발동시킨다.
          // 둘 다 연결하면 같은 메시지가 두 번 전송된다 — 플랫폼별로 하나만 쓴다.
          onSubmitEditing={Platform.OS === 'web' ? undefined : submit}
          onKeyPress={onKeyPress}
          editable={!disabled}
          placeholder="어떤 문화생활을 하고 싶으세요?"
          placeholderTextColor={colors.textFaint}
          style={s.input}
          returnKeyType="send"
          blurOnSubmit={false}
          // 웹에서는 Enter 전송 / Shift+Enter 줄바꿈.
          // 네이티브 multiline 은 Enter 가 줄바꿈으로 먹혀 onSubmitEditing 이 안 온다.
          multiline={Platform.OS !== 'web'}
        />
        <Pressable
          onPress={submit}
          disabled={disabled || !text.trim()}
          style={({ pressed }) => [
            s.send,
            (disabled || !text.trim()) && { opacity: 0.35 },
            pressed && { opacity: 0.7 },
          ]}
        >
          <Text style={{ fontSize: 18, color: '#0F1115' }}>↑</Text>
        </Pressable>
      </View>
    </View>
  );
}

const s = StyleSheet.create({
  wrap: {
    borderTopWidth: 1, borderColor: colors.border, backgroundColor: colors.bg,
    paddingTop: space(2), gap: space(2),
  },
  chips: { gap: space(2), paddingHorizontal: space(4) },
  chip: {
    paddingHorizontal: space(4), paddingVertical: space(2),
    minHeight: 38, justifyContent: 'center',
    borderRadius: radius.xl, borderWidth: 1, borderColor: colors.border,
    backgroundColor: colors.surface,
  },
  inputRow: {
    flexDirection: 'row', alignItems: 'flex-end', gap: space(2),
    paddingHorizontal: space(4), paddingBottom: space(2),
  },
  input: {
    flex: 1, minHeight: 48, maxHeight: 120,
    backgroundColor: colors.surface, borderRadius: radius.lg,
    borderWidth: 1, borderColor: colors.border,
    paddingHorizontal: space(4), paddingVertical: space(3),
    color: colors.text,
    fontSize: 16,        // 16px 미만이면 모바일 브라우저가 입력 시 화면을 확대한다
  },
  send: {
    width: 48, height: 48, borderRadius: 24, backgroundColor: colors.accent,
    alignItems: 'center', justifyContent: 'center',
  },
});
