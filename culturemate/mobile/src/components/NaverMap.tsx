/**
 * 네이버 지도 (WebView + JS SDK).
 *
 * 서버는 지도 공급자를 모른다 — `map_path` 좌표와 `travel_min_from_prev`만 내려주고
 * 렌더링은 전적으로 여기 책임이다. 지도를 갈아끼워도 서버 계약은 그대로다.
 *
 * 키 파라미터명이 콘솔 세대에 따라 ncpKeyId / ncpClientId 로 갈리므로,
 * 첫 로드가 실패하면 반대쪽 이름으로 한 번 더 시도한다.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Platform, StyleSheet, Text, View } from 'react-native';

import type { Itinerary, Parking } from '@/api/types';
import { NAVER_MAP_KEY, NAVER_MAP_KEY_PARAM } from '@/config';
import { colors, radius, space, type } from '@/theme';

/** 주차 표기. 기호 하나로 구분되어야 지도에서 읽힌다. */
const PARKING_MARK: Record<string, string> = {
  free: 'P', paid: 'P₩', nearby: 'P↗', none: 'P✕', unknown: '',
};
const PARKING_LABEL: Record<string, string> = {
  free: '무료주차', paid: '유료주차', nearby: '인근주차', none: '주차불가',
  unknown: '주차정보 없음',
};
const TRANSPORT_ICON: Record<string, string> = {
  car: '🚗', walk: '🚶', subway: '🚈', bus: '🚌', bike: '🚲',
};
const TRANSPORT_NAME: Record<string, string> = {
  car: '자가용', walk: '도보', subway: '지하철', bus: '버스', bike: '자전거',
};

/** 이 장소까지 '어떻게 가는지'. 구간마다 수단이 다를 수 있어 장소에 붙인다. */
function arrivalIcon(item: { transport?: string | null; travel_min_from_prev: number }) {
  if (!item.travel_min_from_prev) return '';       // 출발지 자신
  return TRANSPORT_ICON[item.transport ?? 'subway'] ?? '';
}

type Leg = {
  travel_min_from_prev: number;
  travel_km_from_prev?: number | null;
  travel_transfers?: number | null;
  travel_fare?: number | null;
  travel_source?: string;
  transport?: string | null;
};

/**
 * 구간 라벨. '3분'만 있으면 가까워서 3분인지 빨라서 3분인지 알 수 없다.
 * 추정값에는 ~ 를 붙인다 — 실측과 추정을 같은 얼굴로 보여주면 안 된다.
 */
export function legLabel(item: Leg, { compact = false } = {}): string {
  const icon = TRANSPORT_ICON[item.transport ?? 'subway'] ?? '🚈';
  const approx = item.travel_source === 'estimate' ? '~' : '';
  const parts = [`${icon} ${approx}${item.travel_min_from_prev}분`];
  if (item.travel_km_from_prev) parts.push(`${item.travel_km_from_prev}km`);
  if (!compact && item.travel_transfers) parts.push(`환승 ${item.travel_transfers}`);
  if (!compact && item.travel_fare) parts.push(`${item.travel_fare.toLocaleString()}원`);
  return parts.join(' · ');
}

/**
 * 출발지·도착지 핀. 방문할 장소가 아니라 하루의 양 끝이라 번호를 붙이지 않고
 * 색도 다르게 한다. 이게 없으면 "판교역에서 출발"이라고 말해도 지도에는
 * 강남 어딘가만 찍혀서, 어디서 출발하는 일정인지 알 수 없다.
 */
function endpoints(itinerary: Itinerary) {
  const out: { lat: number; lng: number; label: string; kind: 'start' | 'end' }[] = [];
  if (itinerary.origin) {
    // 이름이 없으면 사용자가 정한 출발지가 아니라 현재 위치로 대신한 것이다.
    // 둘을 같은 라벨로 두면 "부산역에서 출발"이라고 말했는지 아닌지 알 수 없다.
    const named = itinerary.origin_name;
    out.push({
      lat: itinerary.origin.lat, lng: itinerary.origin.lng,
      label: named ? `🚩 출발 · ${named}` : '📍 현재 위치에서 출발',
      kind: 'start',
    });
  }
  if (itinerary.destination) {
    out.push({
      lat: itinerary.destination.lat, lng: itinerary.destination.lng,
      label: `🏁 도착 · ${itinerary.destination_name ?? itinerary.destination.name ?? ''}`.trim(),
      kind: 'end',
    });
  }
  return out;
}

/**
 * 구간 선 스타일. 수단이 선 모양으로 구분돼야 지도만 보고도 읽힌다.
 *
 * 실측 좌표가 없는 구간은 `_estimate` 로 옅게 그린다. 굵은 실선으로 그리면
 * 직선인데도 실제 경로처럼 보여서, 지도가 없는 정보를 있는 것처럼 말하게 된다.
 */
const LEG_STYLE = {
  walk: { color: '#22c55e', weight: 4, opacity: 0.9, style: 'shortdash' },
  subway: { color: '#3b82f6', weight: 6, opacity: 0.9, style: 'solid' },
  bus: { color: '#f59e0b', weight: 5, opacity: 0.9, style: 'solid' },
  car: { color: '#a78bfa', weight: 5, opacity: 0.9, style: 'solid' },
  bike: { color: '#14b8a6', weight: 4, opacity: 0.9, style: 'shortdash' },
  _default: { color: '#a78bfa', weight: 4, opacity: 0.85, style: 'solid' },
  _estimate: { color: '#94a3b8', weight: 3, opacity: 0.5, style: 'shortdot' },
} as const;

function buildHtml(itinerary: Itinerary, keyParam: string, route: boolean) {
  const ends = endpoints(itinerary);
  const start = ends.find((e) => e.kind === 'start') ?? null;
  const end = ends.find((e) => e.kind === 'end') ?? null;
  const points = itinerary.items
    .filter((i) => i.geo)
    .map((i, idx) => ({
      lat: i.geo!.lat, lng: i.geo!.lng, name: i.name, seq: idx + 1,
      leg: i.travel_min_from_prev > 0 ? legLabel(i, { compact: true }) : '',
      // 직전 장소에서 여기까지의 실제 경로. [[lng, lat], …] 로 온다.
      // 비어 있으면(추정 구간) 두 점을 직선으로 잇는다 — 없는 것보다 낫다.
      path: (i.travel_path ?? []).map(([lng, lat]) => ({ lat, lng })),
      mode: i.transport ?? '',
      // 이 장소까지 어떤 수단으로 오는지 — 구간마다 다를 수 있다
      go: arrivalIcon(i),
      goName: TRANSPORT_NAME[i.transport ?? ''] ?? '',
      time: i.arrive ? new Date(i.arrive).toTimeString().slice(0, 5) : '',
      // 주차는 차량 이동에서 가장 자주 문제가 된다 — 지도에서 바로 보여야 한다
      parking: PARKING_MARK[i.parking ?? 'unknown'],
      indoor: i.indoor === true ? '🏠' : i.indoor === false ? '🌤' : '',
      fixed: i.fixed_time ? '🔒' : '',
    }));
  const center = start ?? points[0] ?? { lat: 37.5665, lng: 126.978 };
  const src = `https://oapi.map.naver.com/openapi/v3/maps.js?${keyParam}=${encodeURIComponent(NAVER_MAP_KEY)}`;

  return `<!DOCTYPE html>
<html><head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no"/>
<style>
  html,body,#map{margin:0;padding:0;width:100%;height:100%;background:${colors.bg}}
  .pin{background:${colors.accent};color:#0F1115;font:600 12px/1 -apple-system,system-ui,sans-serif;
       padding:6px 8px;border-radius:14px;white-space:nowrap;box-shadow:0 2px 8px #0006}
  .pin b{font-size:13px}
  .pin .pk{margin-left:5px;padding:1px 4px;border-radius:6px;
           background:#0F1115;color:#fff;font-size:10px;font-weight:700}
  .pin .go{margin-right:4px;padding:1px 3px;border-radius:6px;
           background:#0F1115;font-size:12px;line-height:1}
  .leg{background:${colors.bg}ee;color:${colors.textDim};border:1px solid ${colors.border};
       font:600 10px/1 -apple-system,system-ui,sans-serif;padding:3px 6px;
       border-radius:9px;white-space:nowrap}
  .end{background:${colors.bg};color:${colors.text};border:2px solid ${colors.accent};
       font:700 12px/1 -apple-system,system-ui,sans-serif;padding:6px 9px;
       border-radius:14px;white-space:nowrap;box-shadow:0 2px 8px #0008}
</style>
</head><body>
<div id="map"></div>
<script>
  var POINTS = ${JSON.stringify(points)};
  var START = ${JSON.stringify(start)};
  var END = ${JSON.stringify(end)};
  function report(kind, msg){
    if (window.ReactNativeWebView) {
      window.ReactNativeWebView.postMessage(JSON.stringify({kind: kind, message: msg}));
    }
  }
  // 네이버가 제공하는 인증 실패 훅. 스크립트는 정상 로드되지만 키/도메인이 틀리면
  // 지도만 조용히 안 뜨므로, 이 훅이 없으면 원인을 알 수 없다.
  window.navermap_authFailure = function(){ report('auth', 'auth-failed'); };
  function draw(){
    try {
      var map = new naver.maps.Map('map', {
        center: new naver.maps.LatLng(${center.lat}, ${center.lng}),
        zoom: 13, scaleControl:false, mapDataControl:false, logoControlOptions:{position: naver.maps.Position.BOTTOM_LEFT}
      });
      var path = [];
      // 경로선은 출발지에서 시작한다. 첫 장소부터 그으면 "집에서 얼마나 가야 하나"가
      // 지도에서 사라진다.
      if (START) { path.push(new naver.maps.LatLng(START.lat, START.lng)); }
      POINTS.forEach(function(p){
        var pos = new naver.maps.LatLng(p.lat, p.lng);
        path.push(pos);
        new naver.maps.Marker({
          position: pos, map: map,
          icon: {
            content: '<div class="pin" title="' + (p.goName ? p.goName + '로 이동' : '') + '">'
              + (p.go ? '<span class="go">' + p.go + '</span>' : '')
              + p.fixed + '<b>' + p.seq + '</b> ' + p.name
              + (p.time ? ' · ' + p.time : '') + p.indoor
              + (p.parking ? '<span class="pk">' + p.parking + '</span>' : '')
              + '</div>',
            anchor: new naver.maps.Point(12, 12),
          }
        });
      });
      [START, END].forEach(function(e){
        if (!e) return;
        new naver.maps.Marker({
          position: new naver.maps.LatLng(e.lat, e.lng), map: map, zIndex: 100,
          icon: { content: '<div class="end">' + e.label + '</div>',
                  anchor: new naver.maps.Point(12, 12) }
        });
      });
      if (END) { path.push(new naver.maps.LatLng(END.lat, END.lng)); }

      if (path.length > 1) {
        // 큐레이션은 '묶음'이지 '경로'가 아니다. 선을 그으면 순서대로 가야 하는
        // 코스처럼 보여서, 실제로는 없는 동선을 사용자가 읽게 된다.
        if (${route}) {
          // 구간마다 따로 긋는다. 한 줄로 이으면 수단이 섞인 하루를 한 가지
          // 스타일로만 그리게 되고, 실제 경로 좌표도 쓸 자리가 없다.
          //   도보  = 짧은 점선 (보도라 선형이 촘촘하다)
          //   지하철 = 굵은 실선
          //   버스  = 실선
          //   추정  = 옅은 점선 — '이건 실측이 아니다'를 선으로도 말한다
          var STYLE = ${JSON.stringify(LEG_STYLE)};
          for (var k = 0; k < POINTS.length; k++) {
            var from = k === 0 ? START : POINTS[k-1];
            if (!from) continue;
            var pt = POINTS[k];
            var seg = [];
            if (pt.path && pt.path.length > 1) {
              for (var j = 0; j < pt.path.length; j++) {
                seg.push(new naver.maps.LatLng(pt.path[j].lat, pt.path[j].lng));
              }
            } else {
              seg.push(new naver.maps.LatLng(from.lat, from.lng));
              seg.push(new naver.maps.LatLng(pt.lat, pt.lng));
            }
            var st = STYLE[pt.mode] || STYLE._default;
            if (!pt.path || pt.path.length < 2) { st = STYLE._estimate; }
            new naver.maps.Polyline({
              map: map, path: seg, strokeColor: st.color,
              strokeWeight: st.weight, strokeOpacity: st.opacity,
              strokeStyle: st.style, strokeLineCap: 'round',
            });
          }
          // 마지막 장소 → 도착지. 이 구간은 항목이 없어 위 반복에서 빠진다.
          if (END && POINTS.length) {
            var last = POINTS[POINTS.length - 1];
            new naver.maps.Polyline({
              map: map, strokeColor: STYLE._estimate.color,
              strokeWeight: STYLE._estimate.weight, strokeOpacity: STYLE._estimate.opacity,
              strokeStyle: STYLE._estimate.style,
              path: [new naver.maps.LatLng(last.lat, last.lng),
                     new naver.maps.LatLng(END.lat, END.lng)],
            });
          }
          // 구간마다 중간 지점에 소요시간·거리를 얹는다
          for (var k = 0; k < POINTS.length; k++) {
            if (!POINTS[k].leg) continue;
            // 첫 구간은 '출발지 → 첫 장소'다. 출발지가 없으면 그릴 게 없다.
            var from = k === 0 ? START : POINTS[k-1];
            if (!from) continue;
            var mid = new naver.maps.LatLng(
              (from.lat + POINTS[k].lat) / 2,
              (from.lng + POINTS[k].lng) / 2);
            new naver.maps.Marker({
              position: mid, map: map, zIndex: 50,
              icon: { content: '<div class="leg">' + POINTS[k].leg + '</div>',
                      anchor: new naver.maps.Point(20, 8) }
            });
          }
        }
        var bounds = new naver.maps.LatLngBounds(path[0], path[0]);
        path.forEach(function(p){ bounds.extend(p); });
        map.fitBounds(bounds, {top:60,right:40,bottom:60,left:40});
      }
      report('ready', 'ok');
    } catch (err) { report('error', String(err)); }
  }
  var s = document.createElement('script');
  s.src = ${JSON.stringify(src)};
  s.onload = function(){ (window.naver && naver.maps) ? draw() : report('error','sdk-missing'); };
  s.onerror = function(){ report('error','script-load-failed'); };
  document.head.appendChild(s);
</script>
</body></html>`;
}

const OTHER_PARAM = (p: string) => (p === 'ncpKeyId' ? 'ncpClientId' : 'ncpKeyId');

/**
 * react-native-webview 는 웹을 지원하지 않는다("does not support this platform").
 * 웹에서는 같은 HTML을 iframe(srcDoc)으로 띄우고, 네이티브에서만 WebView를 쓴다.
 * 지도 HTML은 그대로 재사용하므로 두 플랫폼의 결과가 같다.
 */
function WebMap({
  itinerary, keyParam, route, onFail,
}: {
  itinerary: Itinerary;
  keyParam: string;
  route: boolean;
  onFail: (kind: 'auth' | 'load') => void;
}) {
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    let cancelled = false;

    const draw = () => {
      const naver = (window as any).naver;
      if (cancelled || !naver?.maps) return;
      try {
        drawMap(naver, el, itinerary, route);
      } catch {
        onFail('load');
      }
    };

    // 네이버는 전역 콜백으로 인증 실패를 알린다. srcDoc iframe 에서는 Referer 가
    // null 이라 무조건 실패하므로, 웹에서는 부모 문서에 직접 그린다.
    (window as any).navermap_authFailure = () => onFail('auth');

    const src = `https://oapi.map.naver.com/openapi/v3/maps.js?${keyParam}=${encodeURIComponent(NAVER_MAP_KEY)}`;
    const existing = document.querySelector<HTMLScriptElement>(`script[src="${src}"]`);
    if ((window as any).naver?.maps) {
      draw();
    } else if (existing) {
      existing.addEventListener('load', draw);
    } else {
      const script = document.createElement('script');
      script.src = src;
      script.onload = draw;
      script.onerror = () => onFail('load');
      document.head.appendChild(script);
    }
    return () => { cancelled = true; };
  }, [itinerary, keyParam, onFail]);

  return <div ref={ref} style={{ width: '100%', height: '100%' }} />;
}

/** 마커·경로 그리기. 웹과 WebView가 같은 로직을 쓰도록 분리했다. */
function drawMap(naver: any, el: HTMLElement, itinerary: Itinerary, route: boolean) {
  const points = itinerary.items.filter((i) => i.geo);
  if (points.length === 0) return;
  const first = points[0].geo!;
  const map = new naver.maps.Map(el, {
    center: new naver.maps.LatLng(first.lat, first.lng),
    zoom: 13,
    scaleControl: false,
    mapDataControl: false,
  });
  const ends = endpoints(itinerary);
  const start = ends.find((e) => e.kind === 'start') ?? null;
  const end = ends.find((e) => e.kind === 'end') ?? null;

  const path = points.map((p) => new naver.maps.LatLng(p.geo!.lat, p.geo!.lng));
  // 경로선은 출발지에서 시작해 도착지에서 끝난다
  if (start) path.unshift(new naver.maps.LatLng(start.lat, start.lng));
  const shift = start ? 1 : 0;      // 경로선 앞에 출발지가 끼어 있으면 인덱스가 밀린다
  points.forEach((p, idx) => {
    const time = p.arrive ? new Date(p.arrive).toTimeString().slice(0, 5) : '';
    const pk = PARKING_MARK[p.parking ?? 'unknown'];
    const indoor = p.indoor === true ? '🏠' : p.indoor === false ? '🌤' : '';
    const go = arrivalIcon(p);
    const goName = TRANSPORT_NAME[p.transport ?? ''] ?? '';
    new naver.maps.Marker({
      position: path[idx + shift],
      map,
      title: `${p.name}${goName ? ` · ${goName}로 이동` : ''}`
             + ` · ${PARKING_LABEL[p.parking ?? 'unknown']}`,
      icon: {
        content:
          `<div style="background:${colors.accent};color:#0F1115;font:600 12px/1 system-ui;` +
          `padding:6px 8px;border-radius:14px;white-space:nowrap;box-shadow:0 2px 8px #0006">` +
          `${go ? `<span style="margin-right:4px;padding:1px 3px;border-radius:6px;` +
                  `background:#0F1115;font-size:12px">${go}</span>` : ''}` +
          `${p.fixed_time ? '🔒' : ''}<b>${idx + 1}</b> ${p.name}` +
          `${time ? ` · ${time}` : ''}${indoor}` +
          `${pk ? `<span style="margin-left:5px;padding:1px 4px;border-radius:6px;` +
                  `background:#0F1115;color:#fff;font-size:10px">${pk}</span>` : ''}</div>`,
        anchor: new naver.maps.Point(12, 12),
      },
    });
  });
  [start, end].forEach((e) => {
    if (!e) return;
    new naver.maps.Marker({
      position: new naver.maps.LatLng(e.lat, e.lng),
      map, zIndex: 100,
      icon: {
        content:
          `<div style="background:${colors.bg};color:${colors.text};` +
          `border:2px solid ${colors.accent};font:700 12px/1 system-ui;` +
          `padding:6px 9px;border-radius:14px;white-space:nowrap;` +
          `box-shadow:0 2px 8px #0008">${e.label}</div>`,
        anchor: new naver.maps.Point(12, 12),
      },
    });
  });
  if (end) path.push(new naver.maps.LatLng(end.lat, end.lng));

  if (path.length > 1) {
    if (route) {
      // 구간마다 따로 긋는다 — 수단이 섞인 하루를 한 스타일로 그릴 수 없고,
      // 실측 좌표(travel_path)를 쓸 자리도 없어진다. buildHtml 쪽과 같은 규칙이다.
      points.forEach((p, idx) => {
        const from = idx === 0
          ? (start ? { lat: start.lat, lng: start.lng } : null)
          : { lat: points[idx - 1].geo!.lat, lng: points[idx - 1].geo!.lng };
        if (!from) return;
        const real = p.travel_path ?? [];
        const seg = real.length > 1
          ? real.map(([lng, lat]) => new naver.maps.LatLng(lat, lng))
          : [new naver.maps.LatLng(from.lat, from.lng),
             new naver.maps.LatLng(p.geo!.lat, p.geo!.lng)];
        const st = real.length > 1
          ? (LEG_STYLE[(p.transport ?? '') as keyof typeof LEG_STYLE] ?? LEG_STYLE._default)
          : LEG_STYLE._estimate;
        new naver.maps.Polyline({
          map, path: seg, strokeColor: st.color, strokeWeight: st.weight,
          strokeOpacity: st.opacity, strokeStyle: st.style, strokeLineCap: 'round',
        });
      });
      if (end && points.length) {
        const last = points[points.length - 1].geo!;
        new naver.maps.Polyline({
          map, strokeColor: LEG_STYLE._estimate.color,
          strokeWeight: LEG_STYLE._estimate.weight,
          strokeOpacity: LEG_STYLE._estimate.opacity,
          strokeStyle: LEG_STYLE._estimate.style,
          path: [new naver.maps.LatLng(last.lat, last.lng),
                 new naver.maps.LatLng(end.lat, end.lng)],
        });
      }
      points.forEach((p, idx) => {
        if (p.travel_min_from_prev <= 0) return;
        // 첫 장소의 구간은 '출발지 → 첫 장소'다. 여기가 비면 집에서 얼마나
        // 가야 하는지가 지도에서 사라진다.
        const from = idx === 0
          ? (start ? { lat: start.lat, lng: start.lng } : null)
          : { lat: points[idx - 1].geo!.lat, lng: points[idx - 1].geo!.lng };
        if (!from) return;
        const mid = new naver.maps.LatLng(
          (from.lat + p.geo!.lat) / 2,
          (from.lng + p.geo!.lng) / 2,
        );
        new naver.maps.Marker({
          position: mid, map, zIndex: 50,
          icon: {
            content:
              `<div style="background:${colors.bg}ee;color:${colors.textDim};` +
              `border:1px solid ${colors.border};font:600 10px/1 system-ui;` +
              `padding:3px 6px;border-radius:9px;white-space:nowrap">${legLabel(p, { compact: true })}</div>`,
            anchor: new naver.maps.Point(20, 8),
          },
        });
      });
    }
    const bounds = new naver.maps.LatLngBounds(path[0], path[0]);
    path.forEach((p: any) => bounds.extend(p));
    map.fitBounds(bounds, { top: 60, right: 40, bottom: 60, left: 40 });
  }
}

function MapFrame({
  html, itinerary, keyParam, route, onFail,
}: {
  html: string;
  itinerary: Itinerary;
  keyParam: string;
  route: boolean;
  onFail: (kind: 'auth' | 'load') => void;
}) {
  if (Platform.OS === 'web') {
    return <WebMap itinerary={itinerary} keyParam={keyParam} route={route} onFail={onFail} />;
  }
  // 네이티브에서만 로드 — 웹 번들에 포함되지 않도록 지연 require
  const { WebView } = require('react-native-webview');
  return (
    <WebView
      originWhitelist={['*']}
      source={{ html, baseUrl: 'https://localhost' }}
      style={{ backgroundColor: colors.bg }}
      javaScriptEnabled
      domStorageEnabled
      scrollEnabled={false}
      onMessage={(e: { nativeEvent: { data: string } }) => {
        const msg = JSON.parse(e.nativeEvent.data || '{}');
        if (msg.kind === 'auth') onFail('auth');
        else if (msg.kind === 'error') onFail('load');
      }}
    />
  );
}

type Failure = null | 'auth' | 'load';

/** 좌표 목록만 있는 경우(큐레이션)를 일정 형태로 감싼다. 지도 렌더 로직을 하나로 유지한다. */
export function pointsToItinerary(
  points: {
    name: string; lat: number; lng: number;
    indoor?: boolean | null; parking?: Parking | null;
    parking_note?: string | null; category?: string | null;
  }[],
): Itinerary {
  return {
    id: 'points', date: null, total_travel_min: 0, total_dwell_min: 0,
    map_path: points.map((p) => ({ lat: p.lat, lng: p.lng, name: p.name })),
    version: 1, notes: [],
    items: points.map((p, i) => ({
      seq: i + 1, name: p.name, kind: 'venue', dwell_min: 0,
      travel_min_from_prev: 0, evidence_ids: [],
      indoor: p.indoor ?? null,
      parking: p.parking ?? 'unknown',
      parking_note: p.parking_note ?? null,
      geo: { lat: p.lat, lng: p.lng, name: p.name },
    })),
  };
}

export function NaverMap({
  itinerary, height = 260, route = true,
}: {
  itinerary: Itinerary;
  height?: number;
  /** 지점 사이를 선으로 이을지. 일정은 true, 큐레이션 같은 묶음은 false */
  route?: boolean;
}) {
  const [keyParam, setKeyParam] = useState(NAVER_MAP_KEY_PARAM);
  const [failed, setFailed] = useState<Failure>(null);
  const [retried, setRetried] = useState(false);

  const html = useMemo(() => buildHtml(itinerary, keyParam, route),
                       [itinerary, keyParam, route]);
  const points = itinerary.items.filter((i) => i.geo);

  // 훅은 조기 반환보다 **위**에 있어야 한다. 아래 폴백 화면이 return 하는 순간
  // 이 useCallback 이 건너뛰어져 훅 개수가 5 → 4 로 줄고, React 가
  // "Rendered fewer hooks than expected" 로 화면 전체를 무너뜨린다.
  // 지도 키가 정상일 때는 failed 가 계속 null 이라 드러나지 않다가,
  // SDK 로드가 401 을 맞는 순간(예: NCP Maps 미신청) 처음 터진다.
  const handleFail = useCallback((kind: 'auth' | 'load') => {
    if (kind === 'auth') {
      setFailed('auth');        // 인증 실패는 재시도해도 소용없다 — 바로 원인을 알린다
      return;
    }
    if (!retried) {
      setRetried(true);
      setKeyParam(OTHER_PARAM(keyParam));   // 파라미터명 폴백 1회
    } else {
      setFailed('load');
    }
  }, [keyParam, retried]);

  if (!NAVER_MAP_KEY || failed) {
    const title = !NAVER_MAP_KEY
      ? '지도 키가 설정되지 않았습니다'
      : failed === 'auth'
        ? '지도 키 인증에 실패했습니다'
        : '지도를 불러오지 못했습니다';
    const hint = !NAVER_MAP_KEY
      ? '.env 의 EXPO_PUBLIC_NAVER_MAP_KEY 를 설정하면 지도가 표시됩니다.'
      : failed === 'auth'
        ? 'NCP 콘솔에서 (1) Dynamic Map 체크 (2) 웹 서비스 URL에 https://localhost 등록을 확인해 주세요.'
        : '네트워크 또는 키 파라미터명(ncpKeyId / ncpClientId)을 확인해 주세요.';
    return (
      <View style={[s.fallback, { height }]}>
        <Text style={[type.h3, { color: colors.textDim }]}>{title}</Text>
        <Text style={[type.tiny, { marginTop: space(1), textAlign: 'center' }]}>{hint}</Text>
        <View style={s.coords}>
          {points.map((i, idx) => (
            <Text key={i.seq} style={type.tiny}>
              {`${idx + 1}. ${i.name}  (${i.geo!.lat.toFixed(4)}, ${i.geo!.lng.toFixed(4)})`}
            </Text>
          ))}
        </View>
      </View>
    );
  }

  return (
    <View style={[s.wrap, { height }]}>
      <MapFrame html={html} itinerary={itinerary} keyParam={keyParam}
                route={route} onFail={handleFail} />
    </View>
  );
}

const s = StyleSheet.create({
  wrap: { borderRadius: radius.lg, overflow: 'hidden', borderWidth: 1, borderColor: colors.border },
  fallback: {
    borderRadius: radius.lg, borderWidth: 1, borderColor: colors.border,
    backgroundColor: colors.surface, alignItems: 'center', justifyContent: 'center',
    padding: space(4), gap: space(1),
  },
  coords: { marginTop: space(3), gap: space(1), alignItems: 'center' },
});
