/** 관람 기록 입력 — POST /visits → 아카이브 임베딩 → 프로필 갱신. */
import * as ImagePicker from 'expo-image-picker';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { useState } from 'react';
import { Image, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';

import { postVisit } from '@/api/client';
import type { FrictionTag } from '@/api/types';
import { FRICTION_LABEL } from '@/constants';
import { Button, Card } from '@/components/ui';
import { USER_ID } from '@/config';
import { addVisit } from '@/store/storage';
import { colors, radius, space, type } from '@/theme';

const FRICTIONS = Object.keys(FRICTION_LABEL) as FrictionTag[];
const COMPANIONS = ['solo', 'couple', 'friends', 'family', 'kids'] as const;
const COMPANION_LABEL: Record<string, string> = {
  solo: '혼자', couple: '연인', friends: '친구', family: '가족', kids: '아이 동반',
};
// 이동수단 목록은 일정 화면과 같아야 한다. 여기만 다르면 '대중교통'으로 기록한
// 방문이 취향 통계에서 지하철·버스 어느 쪽과도 이어지지 않는다.
const TRANSPORTS = ['walk', 'subway', 'bus', 'car', 'bike'] as const;
const TRANSPORT_LABEL: Record<string, string> = {
  walk: '도보', subway: '지하철', bus: '버스', car: '차량', bike: '자전거',
};

export default function VisitScreen() {
  const router = useRouter();
  const params = useLocalSearchParams<{ place_id?: string; place_name?: string }>();

  const [placeName, setPlaceName] = useState(params.place_name ?? '');
  const [rating, setRating] = useState(0);
  const [review, setReview] = useState('');
  const [friction, setFriction] = useState<FrictionTag[]>([]);
  const [companions, setCompanions] = useState<string | null>(null);
  const [transport, setTransport] = useState<string | null>(null);
  const [photos, setPhotos] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);

  const toggle = <T,>(list: T[], v: T) =>
    list.includes(v) ? list.filter((x) => x !== v) : [...list, v];

  const pickPhoto = async () => {
    const perm = await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (!perm.granted) return;
    const res = await ImagePicker.launchImageLibraryAsync({ quality: 0.6, allowsMultipleSelection: true });
    if (!res.canceled) setPhotos((p) => [...p, ...res.assets.map((a) => a.uri)].slice(0, 6));
  };

  const save = async () => {
    if (!placeName.trim()) return;
    setSaving(true);
    const payload = {
      user_id: USER_ID,
      place_id: params.place_id || `manual-${placeName.trim()}`,
      rating: rating || null,
      review: review.trim() || null,
      friction,
      companions,
      transport,
      photos,
    };
    const ok = await postVisit(payload);
    // 실패해도 로컬에 남긴다. 다음 기동 때 재전송한다.
    await addVisit({
      ...payload,
      id: `v-${Date.now()}`,
      visited_at: new Date().toISOString(),
      place_name: placeName.trim(),
      synced: ok,
    });
    setSaving(false);
    router.back();
  };

  return (
    <ScrollView style={{ backgroundColor: colors.bg }} contentContainerStyle={s.content}>
      <Card>
        <Text style={type.h3}>어디를 다녀오셨나요?</Text>
        <TextInput
          value={placeName}
          onChangeText={setPlaceName}
          placeholder="장소 이름"
          placeholderTextColor={colors.textFaint}
          style={s.input}
        />
      </Card>

      <Card>
        <Text style={type.h3}>만족도</Text>
        <View style={s.stars}>
          {[1, 2, 3, 4, 5].map((n) => (
            <Pressable key={n} onPress={() => setRating(n === rating ? 0 : n)}>
              <Text style={[s.star, { color: n <= rating ? colors.warn : colors.textFaint }]}>★</Text>
            </Pressable>
          ))}
        </View>
      </Card>

      <Card>
        <Text style={type.h3}>불편했던 점</Text>
        <Text style={[type.small, { marginTop: space(1) }]}>
          여기 남긴 항목은 같은 장소가 다음 일정에 다시 포함될 때 먼저 알려드립니다.
        </Text>
        <View style={s.chips}>
          {FRICTIONS.map((f) => {
            const on = friction.includes(f);
            return (
              <Pressable key={f} onPress={() => setFriction((p) => toggle(p, f))}
                style={[s.tag, on && { backgroundColor: colors.dangerSoft, borderColor: colors.danger }]}>
                <Text style={[type.small, { color: on ? colors.danger : colors.textDim }]}>
                  {FRICTION_LABEL[f]}
                </Text>
              </Pressable>
            );
          })}
        </View>
      </Card>

      <Card>
        <Text style={type.h3}>상황</Text>
        <Text style={[type.small, { marginTop: space(1) }]}>
          비슷한 상황의 과거 경험을 찾을 때 쓰입니다.
        </Text>
        <View style={s.chips}>
          {COMPANIONS.map((c) => (
            <Pressable key={c} onPress={() => setCompanions(companions === c ? null : c)}
              style={[s.tag, companions === c && { backgroundColor: colors.accentSoft, borderColor: colors.accent }]}>
              <Text style={[type.small, { color: companions === c ? colors.accent : colors.textDim }]}>
                {COMPANION_LABEL[c]}
              </Text>
            </Pressable>
          ))}
        </View>
        <View style={s.chips}>
          {TRANSPORTS.map((t) => (
            <Pressable key={t} onPress={() => setTransport(transport === t ? null : t)}
              style={[s.tag, transport === t && { backgroundColor: colors.accentSoft, borderColor: colors.accent }]}>
              <Text style={[type.small, { color: transport === t ? colors.accent : colors.textDim }]}>
                {TRANSPORT_LABEL[t]}
              </Text>
            </Pressable>
          ))}
        </View>
      </Card>

      <Card>
        <Text style={type.h3}>감상</Text>
        <TextInput
          value={review}
          onChangeText={setReview}
          placeholder="기억해 두고 싶은 것을 자유롭게 적어 주세요"
          placeholderTextColor={colors.textFaint}
          style={[s.input, { minHeight: 90, textAlignVertical: 'top' }]}
          multiline
        />
        <View style={s.photoRow}>
          {photos.map((uri) => (
            <Image key={uri} source={{ uri }} style={s.photo} />
          ))}
          <Pressable onPress={pickPhoto} style={s.photoAdd}>
            <Text style={{ color: colors.textDim, fontSize: 22 }}>+</Text>
          </Pressable>
        </View>
      </Card>

      <Button label="기록 저장" onPress={save} loading={saving} disabled={!placeName.trim()} />
    </ScrollView>
  );
}

const s = StyleSheet.create({
  content: { padding: space(4), gap: space(3), paddingBottom: space(10) },
  input: {
    marginTop: space(3), backgroundColor: colors.surfaceAlt, borderRadius: radius.md,
    borderWidth: 1, borderColor: colors.border,
    paddingHorizontal: space(3), paddingVertical: space(3), color: colors.text, fontSize: 15,
  },
  stars: { flexDirection: 'row', gap: space(2), marginTop: space(3) },
  star: { fontSize: 32 },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: space(2), marginTop: space(3) },
  tag: {
    paddingHorizontal: space(3), paddingVertical: space(2), borderRadius: 18,
    borderWidth: 1, borderColor: colors.border, backgroundColor: colors.surfaceAlt,
  },
  photoRow: { flexDirection: 'row', flexWrap: 'wrap', gap: space(2), marginTop: space(3) },
  photo: { width: 64, height: 64, borderRadius: radius.sm },
  photoAdd: {
    width: 64, height: 64, borderRadius: radius.sm, borderWidth: 1, borderColor: colors.border,
    borderStyle: 'dashed', alignItems: 'center', justifyContent: 'center',
  },
});
