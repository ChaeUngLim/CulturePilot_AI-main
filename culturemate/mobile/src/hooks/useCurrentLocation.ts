/**
 * 현재 위치. gap_fill(일정 조기 종료) 라우트의 필수 입력이며,
 * 서버로는 conditions_override.origin 으로 주입된다.
 */
import * as Location from 'expo-location';
import { useCallback, useState } from 'react';

import type { GeoPoint } from '@/api/types';

export function useCurrentLocation() {
  const [coords, setCoords] = useState<GeoPoint | null>(null);
  const [status, setStatus] = useState<'idle' | 'asking' | 'granted' | 'denied'>('idle');

  const request = useCallback(async (): Promise<GeoPoint | null> => {
    setStatus('asking');
    try {
      const { status: perm } = await Location.requestForegroundPermissionsAsync();
      if (perm !== 'granted') {
        setStatus('denied');
        return null;
      }
      const pos = await Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.Balanced });
      const point: GeoPoint = { lat: pos.coords.latitude, lng: pos.coords.longitude, name: '현재 위치' };
      setCoords(point);
      setStatus('granted');
      return point;
    } catch {
      setStatus('denied');
      return null;
    }
  }, []);

  return { coords, status, request };
}
