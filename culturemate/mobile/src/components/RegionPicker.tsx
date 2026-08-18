/**
 * 지역 선택 · 관리 (모바일 우선).
 *
 * 칩을 눌러 켜고 끈다. 켜진 칩을 다시 누르면 빠진다 — 체크박스를 그리지 않고
 * 칩 자체가 상태를 나타낸다. 좁은 화면에서 체크박스는 터치 영역만 잡아먹고,
 * 손가락으로는 어차피 칩 전체를 누르게 된다.
 *
 * 기본은 '현재 위치'다. 지역 칩을 미리 채워 두면 사용자는 자기가 고른 것과
 * 앱이 넣어둔 것을 구분하지 못한다.
 */
import * as Haptics from 'expo-haptics';
import { useState } from 'react';
import {
  Modal, Platform, Pressable, ScrollView, StyleSheet, Text, TextInput, View,
} from 'react-native';

import { Button } from '@/components/ui';
import { colors, radius, space, type } from '@/theme';

export type Region = { label: string; region: string | null };

export const CURRENT_LOCATION: Region = { label: '현재 위치', region: null };
export const DEFAULT_REGIONS: Region[] = [];

/** 입력값을 검색에 쓸 지역 문자열로 정규화한다. */
export function normalizeRegion(input: string): Region | null {
  const raw = input.trim().replace(/\s+/g, ' ');
  if (!raw) return null;
  const hasCity =
    /^(서울|부산|대구|인천|광주|대전|울산|세종|경기|강원|충북|충남|전북|전남|경북|경남|제주)/.test(raw);
  const isGu = /(구|시|군)$/.test(raw);
  if (hasCity) return { label: raw.split(' ').pop() ?? raw, region: raw };
  return { label: raw, region: `서울 ${isGu ? raw : `${raw}구`}` };
}

const tap = () => {
  if (Platform.OS !== 'web') void Haptics.selectionAsync();
};

export function RegionPicker({
  regions, selected, hereLabel, pending, onToggle, onUseLocation, onAdd, onRemove,
  onApply, disabled,
}: {
  regions: Region[];
  /** 선택된 지역들. 비어 있으면 '현재 위치' 모드. */
  selected: string[];
  /** 현재 위치의 자치구. 알아냈으면 칩에 함께 표시한다. */
  hereLabel?: string | null;
  /** 선택이 바뀌어 아직 적용되지 않았는가 (현재는 표시에 쓰지 않음) */
  pending?: boolean;
  onToggle: (r: Region) => void;
  onUseLocation: () => void;
  onAdd: (r: Region) => void;
  onRemove: (r: Region) => void;
  /** 남겨 둔다 — 자동 반영이 막힌 환경에서 수동 트리거로 쓸 수 있다 */
  onApply?: () => void;
  disabled?: boolean;
}) {
  const [adding, setAdding] = useState(false);
  const [text, setText] = useState('');
  const [error, setError] = useState<string | null>(null);

  const submit = () => {
    const r = normalizeRegion(text);
    if (!r) return setError('지역명을 입력해 주세요');
    if (regions.some((x) => x.region === r.region)) return setError('이미 추가된 지역입니다');
    onAdd(r);
    setText('');
    setError(null);
    setAdding(false);
  };

  const usingLocation = selected.length === 0;

  return (
    <View style={{ gap: space(2) }}>
      <Text style={type.tiny}>
        {selected.length > 0
          ? `현재 위치 + ${selected.join(', ')} 에서 찾습니다 · 다시 눌러 해제`
          : regions.length === 0
            ? '질문할 지역을 좁히려면 ＋ 로 등록하세요 · 없으면 현재 위치 기준'
            : '질문 범위 (선택 안 하면 현재 위치 기준)'}
      </Text>

      <ScrollView horizontal showsHorizontalScrollIndicator={false}
                  contentContainerStyle={s.row}>
        <Chip
          label={
            usingLocation
              ? (hereLabel ? `📍 현재 위치 (${hereLabel})` : '📍 현재 위치')
              : '📍 전체 해제'
          }
          active={usingLocation}
          disabled={disabled}
          onPress={() => { tap(); onUseLocation(); }}
        />

        {regions.map((r) => {
          const on = !!r.region && selected.includes(r.region);
          return (
            <View key={r.label + (r.region ?? '')} style={[
              s.chip, s.removable,
              on && { backgroundColor: colors.accent, borderColor: colors.accent },
              disabled && { opacity: 0.6 },
            ]}>
              <Pressable onPress={() => { tap(); onToggle(r); }} disabled={disabled}
                         hitSlop={6}>
                <Text style={[type.small, {
                  color: on ? '#0F1115' : colors.text, fontWeight: on ? '700' : '500',
                }]}>
                  {r.label}
                </Text>
              </Pressable>
              <Pressable
                onPress={() => {
                  if (Platform.OS !== 'web') {
                    void Haptics.notificationAsync(Haptics.NotificationFeedbackType.Warning);
                  }
                  r.region && onRemove(r);
                }}
                disabled={disabled}
                hitSlop={10}
                accessibilityLabel={`${r.label} 삭제`}
              >
                <Text style={[s.remove, { color: on ? '#0F1115' : colors.textFaint }]}>
                  {'×'}
                </Text>
              </Pressable>
            </View>
          );
        })}

        <Pressable
          onPress={() => setAdding(true)}
          disabled={disabled}
          hitSlop={6}
          style={({ pressed }) => [s.chip, s.add, pressed && { opacity: 0.6 }]}
        >
          <Text style={[type.small, { color: colors.accent, fontWeight: '700' }]}>
            {regions.length === 0 ? '＋ 지역 추가' : '＋'}
          </Text>
        </Pressable>
      </ScrollView>



      <Modal visible={adding} transparent animationType="fade"
             onRequestClose={() => setAdding(false)}>
        <Pressable style={s.backdrop} onPress={() => setAdding(false)} />
        <View style={s.dialog}>
          <Text style={type.h3}>{'지역 추가'}</Text>
          <Text style={[type.small, { marginTop: space(1) }]}>
            {'예: 강남구 · 서울 마포구 · 부산 해운대구 · 대전'}
          </Text>
          <TextInput
            value={text}
            onChangeText={(v) => { setText(v); setError(null); }}
            onSubmitEditing={submit}
            placeholder="지역명"
            placeholderTextColor={colors.textFaint}
            style={s.input}
            autoFocus
            returnKeyType="done"
          />
          {!!error && (
            <Text style={[type.small, { color: colors.danger, marginTop: space(2) }]}>
              {error}
            </Text>
          )}
          <View style={s.actions}>
            <Button label="취소" variant="outline" style={{ flex: 1 }}
                    onPress={() => { setAdding(false); setError(null); }} />
            <Button label="추가" style={{ flex: 1 }} onPress={submit} />
          </View>
        </View>
      </Modal>
    </View>
  );
}

function Chip({
  label, active, disabled, onPress, onLongPress,
}: {
  label: string;
  active: boolean;
  disabled?: boolean;
  onPress: () => void;
  onLongPress?: () => void;
}) {
  return (
    <Pressable
      onPress={onPress}
      onLongPress={onLongPress}
      delayLongPress={450}
      disabled={disabled}
      hitSlop={6}
      style={({ pressed }) => [
        s.chip,
        active && { backgroundColor: colors.accent, borderColor: colors.accent },
        (pressed || disabled) && { opacity: 0.6 },
      ]}
    >
      <Text style={[type.small, {
        color: active ? '#0F1115' : colors.text,
        fontWeight: active ? '700' : '500',
      }]}>
        {label}
      </Text>
    </Pressable>
  );
}

const s = StyleSheet.create({
  row: { gap: space(2), paddingRight: space(4), alignItems: 'center' },
  chip: {
    paddingHorizontal: space(4), paddingVertical: space(2),
    minHeight: 40, justifyContent: 'center',   // 모바일 터치 영역 확보
    borderRadius: radius.xl, borderWidth: 1, borderColor: colors.border,
    backgroundColor: colors.surface,
  },
  add: { borderStyle: 'dashed', borderColor: colors.accent, minWidth: 48,
         alignItems: 'center' },
  removable: { flexDirection: 'row', alignItems: 'center', gap: space(2),
               paddingRight: space(3) },
  remove: { fontSize: 17, lineHeight: 19, fontWeight: '600' },
  backdrop: { position: 'absolute', top: 0, left: 0, right: 0, bottom: 0,
              backgroundColor: '#000A' },
  dialog: {
    position: 'absolute', top: '24%', left: space(5), right: space(5),
    backgroundColor: colors.surface, borderRadius: radius.lg,
    borderWidth: 1, borderColor: colors.border, padding: space(5),
  },
  input: {
    marginTop: space(3), backgroundColor: colors.surfaceAlt, borderRadius: radius.md,
    borderWidth: 1, borderColor: colors.border,
    paddingHorizontal: space(4), paddingVertical: space(3),
    color: colors.text, fontSize: 16,          // 모바일에서 16px 미만은 자동 확대된다
    minHeight: 48,
  },
  actions: { flexDirection: 'row', gap: space(2), marginTop: space(4) },
});
