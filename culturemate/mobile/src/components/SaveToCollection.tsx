/**
 * 일정을 내 컬렉션에 담기 (즐겨찾기).
 *
 * 이 서비스의 전제는 "아카이브는 기록장이 아니라 다음 판단의 근거"다. 그런데
 * 아직 다녀오지 않은 곳은 방문 기록에 남길 수 없다. 이 화면이 그 사이를 메운다 —
 * "가고 싶다고 판단한 것"도 다음 판단의 재료가 되도록.
 *
 * 저장 단위는 '장소'다. 일정 통째로 두면 나중에 다른 일정의 장소와 섞이지 않아
 * '내 지도'가 만들어지지 않는다.
 */
import { useState } from 'react';
import {
  ActivityIndicator, Modal, Pressable, ScrollView, StyleSheet, Text, TextInput, View,
} from 'react-native';

import { saveCollection } from '@/api/client';
import type { Collection, ItineraryItem } from '@/api/types';
import { Button } from '@/components/ui';
import { colors, radius, space, type } from '@/theme';

/** 새 테마를 만들 때 고를 수 있는 아이콘. 자동 테마와 겹치지 않는 것들로 */
const EMOJIS = ['⭐', '❤️', '🎨', '🎭', '📚', '🍽️', '☕', '🌿', '🌙', '🎁', '🚩', '🗺️'];

export function SaveToCollection({
  items, collections, onSaved,
}: {
  items: ItineraryItem[];
  /** 이미 있는 테마들. 자동 테마에는 담을 수 없다 — 규칙이 만든 것이라 손댈 수 없다. */
  collections: Collection[];
  onSaved?: (title: string, added: number) => void;
}) {
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState('');
  const [emoji, setEmoji] = useState(EMOJIS[0]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const placeIds = items.map((i) => i.place_id).filter(Boolean) as string[];
  const mine = collections.filter((c) => c.mine);

  const save = async (name: string, icon: string) => {
    if (!name.trim() || placeIds.length === 0) return;
    setBusy(true);
    setError(null);
    try {
      const res = await saveCollection({
        title: name.trim(),
        placeIds,
        emoji: icon,
        note: '일정에서 담음',
      });
      onSaved?.(res.title, res.added);
      setOpen(false);
      setTitle('');
    } catch {
      // 저장이 안 됐는데 됐다고 하면 사용자는 다시 담지 않는다
      setError('저장하지 못했어요. 잠시 후 다시 시도해 주세요.');
    } finally {
      setBusy(false);
    }
  };

  if (placeIds.length === 0) return null;

  return (
    <>
      <Pressable
        onPress={() => setOpen(true)}
        style={({ pressed }) => [s.trigger, pressed && { opacity: 0.7 }]}
      >
        <Text style={[type.small, { color: colors.accent, fontWeight: '700' }]}>
          {`⭐ 이 일정 저장 (${placeIds.length}곳)`}
        </Text>
      </Pressable>

      <Modal visible={open} transparent animationType="fade"
             onRequestClose={() => setOpen(false)}>
        <Pressable style={s.backdrop} onPress={() => setOpen(false)} />
        <View style={s.sheet}>
          <Text style={type.h3}>{'어디에 담을까요?'}</Text>
          <Text style={[type.small, { marginTop: space(1) }]}>
            {`${placeIds.length}곳이 큐레이션 탭의 테마로 저장됩니다.`}
          </Text>

          {mine.length > 0 && (
            <>
              <Text style={[type.tiny, { marginTop: space(4) }]}>{'기존 테마'}</Text>
              <ScrollView style={{ maxHeight: 180 }}>
                <View style={s.list}>
                  {mine.map((c) => (
                    <Pressable
                      key={c.key}
                      onPress={() => void save(c.title, c.emoji)}
                      disabled={busy}
                      style={({ pressed }) => [s.row, pressed && { opacity: 0.6 }]}
                    >
                      <Text style={type.body}>{`${c.emoji} ${c.title}`}</Text>
                      <Text style={type.tiny}>{`${c.count}곳`}</Text>
                    </Pressable>
                  ))}
                </View>
              </ScrollView>
            </>
          )}

          <Text style={[type.tiny, { marginTop: space(4) }]}>{'새 테마 만들기'}</Text>
          <View style={s.emojis}>
            {EMOJIS.map((e) => (
              <Pressable
                key={e}
                onPress={() => setEmoji(e)}
                style={({ pressed }) => [
                  s.emoji,
                  emoji === e && { borderColor: colors.accent, backgroundColor: colors.accentSoft },
                  pressed && { opacity: 0.6 },
                ]}
              >
                <Text style={{ fontSize: 18 }}>{e}</Text>
              </Pressable>
            ))}
          </View>
          <TextInput
            value={title}
            onChangeText={setTitle}
            onSubmitEditing={() => void save(title, emoji)}
            placeholder="예: 비 오는 날 갈 곳 / 데이트 코스"
            placeholderTextColor={colors.textFaint}
            style={s.input}
            maxLength={20}
          />

          {!!error && (
            <Text style={[type.small, { color: colors.danger, marginTop: space(2) }]}>
              {error}
            </Text>
          )}

          <View style={s.actions}>
            <Button label="취소" variant="outline" style={{ flex: 1 }}
                    onPress={() => setOpen(false)} disabled={busy} />
            <Button
              label={busy ? '저장 중…' : '저장'}
              style={{ flex: 1 }}
              onPress={() => void save(title, emoji)}
              disabled={busy || !title.trim()}
            />
          </View>
          {busy && <ActivityIndicator color={colors.accent} style={{ marginTop: space(2) }} />}
        </View>
      </Modal>
    </>
  );
}

const s = StyleSheet.create({
  trigger: {
    alignSelf: 'flex-start', paddingHorizontal: space(4), paddingVertical: space(3),
    minHeight: 44, justifyContent: 'center',
    borderRadius: radius.xl, borderWidth: 1, borderColor: colors.accent,
    backgroundColor: colors.accentSoft,
  },
  backdrop: { position: 'absolute', top: 0, left: 0, right: 0, bottom: 0,
              backgroundColor: '#000A' },
  sheet: {
    position: 'absolute', left: space(4), right: space(4), top: '12%',
    backgroundColor: colors.surface, borderRadius: radius.lg,
    borderWidth: 1, borderColor: colors.border, padding: space(4),
  },
  list: { gap: space(1), marginTop: space(2) },
  row: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingHorizontal: space(3), paddingVertical: space(3), minHeight: 48,
    borderRadius: radius.md, borderWidth: 1, borderColor: colors.border,
    backgroundColor: colors.surfaceAlt,
  },
  emojis: { flexDirection: 'row', flexWrap: 'wrap', gap: space(2), marginTop: space(2) },
  emoji: {
    width: 40, height: 40, alignItems: 'center', justifyContent: 'center',
    borderRadius: radius.md, borderWidth: 1, borderColor: colors.border,
    backgroundColor: colors.surfaceAlt,
  },
  input: {
    marginTop: space(3), paddingHorizontal: space(3), paddingVertical: space(3),
    borderRadius: radius.md, borderWidth: 1, borderColor: colors.border,
    backgroundColor: colors.surfaceAlt, color: colors.text, fontSize: 15,
  },
  actions: { flexDirection: 'row', gap: space(2), marginTop: space(4) },
});
