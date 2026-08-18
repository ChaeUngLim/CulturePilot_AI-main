/**
 * 장소 하나에 대한 '가기 전에 알아야 할 사실' 표시.
 *
 * 일정 타임라인·큐레이션·(앞으로 생길) 상세 화면이 같은 규칙으로 보여줘야 한다.
 * 화면마다 따로 만들면 한쪽만 주차를 빼먹거나 실내 판정 기준이 갈리는데,
 * 사용자 입장에서는 같은 장소가 화면마다 다르게 보이는 셈이라 신뢰를 잃는다.
 */
import { StyleSheet, Text, View } from 'react-native';

import type { Parking, VerifyStatus } from '@/api/types';
import { Chip } from '@/components/ui';
import { KIND_LABEL } from '@/constants';
import { space, type } from '@/theme';

type Tone = 'default' | 'ok' | 'warn' | 'danger' | 'accent';

/** 주차는 차량 이동에서 가장 자주 문제가 된다 — 장소마다 드러나야 한다. */
export const PARKING_CHIP: Record<Parking, { label: string; tone: Tone } | null> = {
  free:   { label: '무료주차', tone: 'ok' },
  paid:   { label: '유료주차', tone: 'default' },
  nearby: { label: '인근주차', tone: 'warn' },
  none:   { label: '주차불가', tone: 'danger' },
  unknown: null,          // 모르는 걸 '없음'처럼 보이게 하지 않는다
};

export const TRANSPORT_CHIP: Record<string, string> = {
  walk: '🚶 도보', car: '🚗 자가용', subway: '🚈 지하철',
  bus: '🚌 버스', bike: '🚲 자전거',
};

/**
 * 공식정보 대조 결과 칩.
 *
 * `unknown` 은 칩을 띄우지 않는다 — 2단계 검증이 끝나기 전 상태라, 그때마다
 * '확인 안 됨'이 떴다가 사라지면 깜빡이는 것처럼 보인다.
 * `needs_check` 는 경고로 남긴다. 정보가 부족한 것이지 틀린 게 아니므로 버리지 않고,
 * 소규모 공방·독립서점이 대개 여기 해당한다.
 */
const VERIFY_CHIP: Record<string, { label: string; tone?: Tone }> = {
  verified: { label: '✓ 공식정보 확인', tone: 'accent' },
  needs_check: { label: '확인 필요', tone: 'warn' },
  excluded: { label: '정보 불일치', tone: 'danger' },
};

export type PlaceFactsProps = {
  kind?: string | null;
  /** 카테고리 원문("전시"·"공연"). kind 라벨이 없을 때 대신 쓴다 */
  category?: string | null;
  indoor?: boolean | null;
  parking?: Parking | null;
  parkingNote?: string | null;
  /** 사용자가 시각을 지정한 항목 */
  fixedTime?: boolean;
  /** 이 장소까지 오는 이동수단. 구간마다 다를 수 있어 장소에 붙인다. */
  arriveBy?: string | null;
  /** 공식정보 대조 결과. 2단계 검증이 끝나면 '확인 필요'가 '검증됨'으로 바뀐다. */
  verifyStatus?: VerifyStatus | null;
  /** 화면별로 덧붙일 칩 (근거 개수, 불편 기록 등) */
  extra?: { label: string; tone?: Tone }[];
};

export function PlaceFacts({
  kind, category, indoor, parking, parkingNote, fixedTime, arriveBy,
  verifyStatus, extra,
}: PlaceFactsProps) {
  const parkChip = parking ? PARKING_CHIP[parking] : null;
  const kindLabel = kind ? KIND_LABEL[kind] ?? kind : category || null;
  const verifyChip = verifyStatus ? VERIFY_CHIP[verifyStatus] : null;

  return (
    <>
      <View style={s.chips}>
        {fixedTime && <Chip label="🔒 지정 시각" tone="accent" />}
        {!!arriveBy && TRANSPORT_CHIP[arriveBy] && (
          <Chip label={TRANSPORT_CHIP[arriveBy]} />
        )}
        {!!kindLabel && <Chip label={kindLabel} />}
        {indoor === true && <Chip label="🏠 실내" tone="accent" />}
        {indoor === false && <Chip label="🌤 야외" tone="warn" />}
        {parkChip && <Chip label={parkChip.label} tone={parkChip.tone} />}
        {verifyChip && <Chip label={verifyChip.label} tone={verifyChip.tone} />}
        {extra?.map((e) => <Chip key={e.label} label={e.label} tone={e.tone} />)}
      </View>
      {!!parkingNote && (
        <Text style={[type.tiny, { marginTop: space(1) }]}>{`🅿 ${parkingNote}`}</Text>
      )}
    </>
  );
}

const s = StyleSheet.create({
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: space(1), marginTop: space(2) },
});
