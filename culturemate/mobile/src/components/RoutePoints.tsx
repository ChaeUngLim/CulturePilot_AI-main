/**
 * 출발지 · 도착지 · 출발시각.
 *
 * 셋 다 선택 사항이다. 비워 두면 지금까지처럼 순서와 소요시간만 계산하고,
 * 넣으면 "몇 시에 나가서 몇 시에 어디서 끝나는지"까지 채워진다. 계획을 실제로
 * 실행할지 말지는 대개 그 두 시각에서 갈리기 때문에, 넣을 수 있게 열어 둔다.
 *
 * 좌표가 아니라 이름만 받는다 — 서버가 주소 API로 바꾼다. 사용자에게 좌표를
 * 물어보는 화면은 만들지 않는다.
 */
import { useState } from 'react';
import { Modal, Pressable, StyleSheet, Text, TextInput, View } from 'react-native';

import { Button } from '@/components/ui';
import { colors, radius, space, type } from '@/theme';

type Field = 'origin' | 'destination' | 'start' | 'end' | null;

const HOURS = ['08:00', '09:00', '10:00', '11:00', '13:00', '14:00', '16:00', '18:00'];
const END_HOURS = ['17:00', '18:00', '19:00', '20:00', '21:00', '22:00', '23:00'];

export function RoutePoints({
  origin, destination, startTime, endTime, currentLabel, locating,
  onOrigin, onDestination, onStartTime, onEndTime, onUseCurrent, disabled,
}: {
  /** 도착 목표 시각 "21:00". 출발만 시각이 있으면 반쪽짜리다 */
  endTime?: string | null;
  onEndTime?: (v: string | null) => void;
  origin: string | null;
  /** 출발지를 직접 안 정했을 때 대신 보여줄 문구 (예: "📍 현재 위치 (서울 종로구)") */
  currentLabel?: string;
  locating?: boolean;
  onUseCurrent?: () => void;
  destination: string | null;
  /** "09:00" 형식 */
  startTime: string | null;
  onOrigin: (v: string | null) => void;
  onDestination: (v: string | null) => void;
  onStartTime: (v: string | null) => void;
  disabled?: boolean;
}) {
  const [editing, setEditing] = useState<Field>(null);
  const [text, setText] = useState('');

  const submit = () => {
    const value = text.trim() || null;
    if (editing === 'origin') onOrigin(value);
    else if (editing === 'destination') onDestination(value);
    setText('');
    setEditing(null);
  };

  return (
    <View style={s.row}>
      {/*
        현재 위치는 출발지가 아니다. 정하지 않았을 때 대신 쓰는 값일 뿐이라
        흐리게 보여주고, 직접 정하면 진하게 바뀐다. 둘을 같은 모양으로 두면
        "부산역에서 출발"이라고 말해도 여전히 현재 위치인 줄 안다.
      */}
      <Slot label="출발"
            value={origin}
            fallback={currentLabel ? `📍 ${currentLabel}` : null}
            placeholder={locating ? '위치 확인 중…' : '출발지 (선택)'}
            onPress={() => setEditing('origin')}
            onClear={origin ? () => onOrigin(null) : undefined}
            disabled={disabled} />

      <Slot label="" value={startTime} placeholder="시각 (선택)"
            onPress={() => setEditing('start')}
            onClear={() => onStartTime(null)} disabled={disabled} />

      <Text style={type.tiny}>{'→'}</Text>

      <Slot label="도착" value={destination} placeholder="도착지 (선택)"
            onPress={() => setEditing('destination')}
            onClear={() => onDestination(null)} disabled={disabled} />

      <Slot label="" value={endTime ?? null} placeholder="시각 (선택)"
            onPress={() => setEditing('end')}
            onClear={onEndTime ? () => onEndTime(null) : undefined}
            disabled={disabled} />

      <Modal visible={editing !== null} transparent animationType="fade"
             onRequestClose={() => setEditing(null)}>
        <Pressable style={s.backdrop} onPress={() => setEditing(null)} />
        <View style={s.dialog}>
          {editing === 'start' || editing === 'end' ? (
            <>
              <Text style={type.h3}>
                {editing === 'end' ? '몇 시까지 도착할까요?' : '몇 시에 나가세요?'}
              </Text>
              <Text style={[type.small, { marginTop: space(1) }]}>
                {editing === 'end'
                  ? '마지막 일정이 이 시각까지 끝나도록 짭니다.'
                  : '정하면 장소마다 도착 시각까지 계산합니다.'}
              </Text>
              <View style={s.hours}>
                {(editing === 'end' ? END_HOURS : HOURS).map((h) => {
                  const cur = editing === 'end' ? endTime : startTime;
                  return (
                    <Pressable
                      key={h}
                      onPress={() => {
                        if (editing === 'end') onEndTime?.(h);
                        else onStartTime(h);
                        setEditing(null);
                      }}
                      style={({ pressed }) => [
                        s.hour,
                        cur === h && {
                          backgroundColor: colors.accent, borderColor: colors.accent,
                        },
                        pressed && { opacity: 0.6 },
                      ]}
                    >
                      <Text style={[type.small, {
                        color: cur === h ? '#0F1115' : colors.text,
                        fontWeight: cur === h ? '700' : '500',
                      }]}>{h}</Text>
                    </Pressable>
                  );
                })}
              </View>
              <View style={s.actions}>
                <Button label="시각 없음" variant="outline" style={{ flex: 1 }}
                        onPress={() => {
                          if (editing === 'end') onEndTime?.(null);
                          else onStartTime(null);
                          setEditing(null);
                        }} />
              </View>
            </>
          ) : (
            <>
              <Text style={type.h3}>
                {editing === 'destination' ? '어디서 마무리할까요?' : '어디서 출발하세요?'}
              </Text>
              <Text style={[type.small, { marginTop: space(1) }]}>
                {'역·건물 이름이나 주소를 넣으세요.\n'
                  + '예: 판교역 · 예술의전당 · 서울 강남구 영동대로 513'}
              </Text>
              <TextInput
                value={text}
                onChangeText={setText}
                onSubmitEditing={submit}
                autoFocus
                placeholder={editing === 'destination' ? '종각역' : '판교역'}
                placeholderTextColor={colors.textFaint}
                style={s.input}
              />
              <View style={s.actions}>
                <Button
                  label={editing === 'origin' && onUseCurrent ? '현재 위치 사용' : '비우기'}
                  variant="outline" style={{ flex: 1 }}
                  onPress={() => {
                    if (editing === 'destination') onDestination(null);
                    else if (onUseCurrent) onUseCurrent();
                    else onOrigin(null);
                    setText('');
                    setEditing(null);
                  }} />
                <Button label="확인" style={{ flex: 1 }} onPress={submit} />
              </View>
            </>
          )}
        </View>
      </Modal>
    </View>
  );
}

function Slot({
  label, value, fallback, placeholder, onPress, onClear, disabled,
}: {
  label: string;
  value: string | null;
  /** 값이 없을 때 대신 보여줄 것. 정해진 값이 아니라는 뜻으로 흐리게 그린다. */
  fallback?: string | null;
  placeholder: string;
  onPress: () => void;
  /** 없으면 ✕ 버튼을 숨긴다 — 지울 수 없는 값도 있다(현재 위치) */
  onClear?: () => void;
  disabled?: boolean;
}) {
  return (
    <View style={s.slot}>
      {!!label && <Text style={type.tiny}>{label}</Text>}
      <Pressable
        onPress={onPress}
        disabled={disabled}
        style={({ pressed }) => [
          s.chip,
          !value && { borderStyle: 'dashed', borderColor: colors.textFaint },
          pressed && { opacity: 0.6 },
        ]}
      >
        <Text style={[type.small, {
          color: value ? colors.text : colors.textFaint,
          fontWeight: value ? '600' : '400',
        }]}>
          {value ?? fallback ?? placeholder}
        </Text>
      </Pressable>
      {!!value && !!onClear && (
        <Pressable onPress={onClear} disabled={disabled} hitSlop={8}>
          <Text style={[type.tiny, { color: colors.textFaint }]}>{'✕'}</Text>
        </Pressable>
      )}
    </View>
  );
}

const s = StyleSheet.create({
  row: { flexDirection: 'row', alignItems: 'center', gap: space(2), flexWrap: 'wrap' },
  slot: { flexDirection: 'row', alignItems: 'center', gap: space(1) },
  chip: {
    paddingHorizontal: space(3), paddingVertical: space(2),
    minHeight: 36, justifyContent: 'center',
    borderRadius: radius.xl, borderWidth: 1, borderColor: colors.border,
    backgroundColor: colors.surface,
  },
  backdrop: { position: 'absolute', top: 0, left: 0, right: 0, bottom: 0,
              backgroundColor: '#000A' },
  dialog: {
    position: 'absolute', left: space(4), right: space(4), top: '28%',
    backgroundColor: colors.surface, borderRadius: radius.lg,
    borderWidth: 1, borderColor: colors.border, padding: space(4), gap: space(2),
  },
  input: {
    marginTop: space(3), paddingHorizontal: space(3), paddingVertical: space(3),
    borderRadius: radius.md, borderWidth: 1, borderColor: colors.border,
    backgroundColor: colors.surfaceAlt, color: colors.text, fontSize: 15,
  },
  hours: { flexDirection: 'row', flexWrap: 'wrap', gap: space(2), marginTop: space(3) },
  hour: {
    paddingHorizontal: space(3), paddingVertical: space(2), minWidth: 64,
    alignItems: 'center', borderRadius: radius.xl,
    borderWidth: 1, borderColor: colors.border, backgroundColor: colors.surfaceAlt,
  },
  actions: { flexDirection: 'row', gap: space(2), marginTop: space(4) },
});
