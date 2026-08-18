import type { FrictionTag } from '@/api/types';

/** 불편 태그 — 서버 FrictionTag Literal 과 동일 집합이어야 한다. */
export const FRICTION_LABEL: Record<FrictionTag, string> = {
  parking: '주차', crowding: '혼잡', accessibility: '접근성', waiting: '대기',
  noise: '소음', cost: '비용', reservation: '예약', transit: '교통', weather: '날씨',
};

/** 장소 종류 라벨. 서버 kind Literal 과 같은 집합이다. */
export const KIND_LABEL: Record<string, string> = {
  event: '행사', venue: '문화공간', food: '식당', cafe: '카페',
  shop: '상점', park: '야외', other: '기타',
};
