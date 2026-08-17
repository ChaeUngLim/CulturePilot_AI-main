/**
 * 서버 연결 화면.
 *
 * 여기가 있는 이유 — 서버 주소가 빌드타임 `EXPO_PUBLIC_API_URL` 하나뿐이면,
 * 실기기에서 PC의 LAN 주소로 붙거나 터널 주소가 바뀔 때마다 .env 를 고치고
 * 앱을 다시 말아야 한다. 그 사이 사용자가 보는 건 "그냥 안 되는 앱"이다.
 *
 * 순서를 «확인 → 저장» 으로 고정했다. 먼저 저장하고 앱 전체가 안 되는 걸로
 * 확인하게 되면, 주소가 틀린 건지 서버가 죽은 건지 구분할 방법이 없다.
 */
import { useCallback, useEffect, useState } from 'react';
import { router } from 'expo-router';
import {
  KeyboardAvoidingView, Platform, Pressable, ScrollView, StyleSheet,
  Text, TextInput, View,
} from 'react-native';

import { probeServer, type ProbeResult } from '@/api/client';
import { Button, Card, Chip } from '@/components/ui';
import { apiUrl, ENV_API_URL, isMock, normalizeUrl, setApiUrl } from '@/config';
import { loadServerUrl, saveServerUrl } from '@/store/storage';
import { colors, radius, space, type } from '@/theme';

/** 자주 쓰는 주소. 환경마다 «localhost» 가 가리키는 곳이 달라서 골라 쓰게 둔다. */
const PRESETS: { label: string; url: string; hint: string }[] = [
  { label: '이 PC', url: 'http://localhost:8000', hint: '웹 · 시뮬레이터' },
  { label: '안드로이드 에뮬레이터', url: 'http://10.0.2.2:8000', hint: '에뮬레이터가 보는 PC' },
];

export default function ConnectScreen() {
  const [draft, setDraft] = useState('');
  const [probing, setProbing] = useState(false);
  const [result, setResult] = useState<ProbeResult | null>(null);
  const [saved, setSaved] = useState<string | null>(null);

  const active = apiUrl();
  const mock = isMock();

  useEffect(() => {
    void (async () => {
      const stored = await loadServerUrl();
      setSaved(stored);
      setDraft(active || stored || ENV_API_URL);
    })();
    // 최초 1회만. 이후 입력창의 주인은 사용자다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const normalized = normalizeUrl(draft);

  const probe = useCallback(async (url: string) => {
    setProbing(true);
    setResult(null);
    const r = await probeServer(url);
    setProbing(false);
    setResult(r);
    return r;
  }, []);

  /** 주소를 저장하고 적용한다. 루트가 상태를 다시 세운 뒤 «오늘» 로 돌려보낸다. */
  const apply = useCallback(async (url: string | null) => {
    // null = 저장 삭제(빌드타임 기본값으로 복귀), '' = 목 모드
    await saveServerUrl(url);
    setApiUrl(url === null ? ENV_API_URL : url);
    router.replace('/');
  }, []);

  const dirty = normalized !== active;

  return (
    <KeyboardAvoidingView
      style={{ flex: 1, backgroundColor: colors.bg }}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
    >
      <ScrollView contentContainerStyle={s.content} keyboardShouldPersistTaps="handled">

        {/* ── 지금 상태 ─────────────────────────────────────────── */}
        <Card>
          <View style={s.row}>
            <Chip label={mock ? '목 모드' : '서버 연결'} tone={mock ? 'accent' : 'ok'} />
            <Text style={[type.tiny, { flex: 1, textAlign: 'right' }]}>
              {saved === null ? '.env 기본값' : '직접 지정'}
            </Text>
          </View>
          <Text style={[type.mono, { marginTop: space(2) }]} numberOfLines={2}>
            {active || '(없음 — 백엔드 없이 동작 중)'}
          </Text>
          <Text style={[type.small, { marginTop: space(3) }]}>
            {mock
              ? '목 데이터로 전체 플로우가 돕니다. 실제 일정·검증 결과는 아닙니다.'
              : '대화 · 일정 · 아카이브가 이 서버를 통해 처리됩니다.'}
          </Text>
        </Card>

        {/* ── 주소 입력 ─────────────────────────────────────────── */}
        <Text style={[type.h3, { marginTop: space(6) }]}>서버 주소</Text>
        <TextInput
          value={draft}
          onChangeText={(t) => { setDraft(t); setResult(null); }}
          placeholder="http://localhost:8000"
          placeholderTextColor={colors.textFaint}
          autoCapitalize="none"
          autoCorrect={false}
          keyboardType="url"
          inputMode="url"
          onSubmitEditing={() => { if (normalized) void probe(normalized); }}
          style={s.input}
        />

        <View style={s.presets}>
          {PRESETS.map((p) => (
            <Pressable
              key={p.url}
              onPress={() => { setDraft(p.url); setResult(null); }}
              style={({ pressed }) => [s.preset, pressed && { opacity: 0.6 }]}
            >
              <Text style={[type.small, { color: colors.text }]}>{p.label}</Text>
              <Text style={type.tiny}>{p.hint}</Text>
            </Pressable>
          ))}
        </View>

        <Text style={[type.tiny, { marginTop: space(2) }]}>
          실기기에서 localhost 는 폰 자신을 가리킵니다. PC 의 LAN 주소
          (예: http://192.168.0.10:8000) 나 터널 주소를 넣어 주세요.
        </Text>

        {/* ── 확인 ─────────────────────────────────────────────── */}
        <Button
          label={probing ? '확인 중' : '연결 테스트'}
          onPress={() => void probe(normalized)}
          variant="outline"
          loading={probing}
          disabled={!normalized}
          style={{ marginTop: space(4) }}
        />

        {result && <ProbeReport result={result} url={normalized} />}

        {/* ── 적용 ─────────────────────────────────────────────── */}
        <Button
          label={
            !dirty ? '이미 연결된 주소입니다'
              : result && !result.ok ? '확인 실패 — 그래도 저장'
                : '저장하고 연결'
          }
          onPress={() => {
            void (async () => {
              // 결과를 이미 본 뒤라면 실패였더라도 사용자의 판단을 따른다 —
              // 주소는 맞는데 서버가 잠깐 내려가 있는 경우가 있다.
              if (result) {
                await apply(normalized);
                return;
              }
              const r = await probe(normalized);
              if (r.ok) await apply(normalized);
            })();
          }}
          disabled={!normalized || !dirty || probing}
          style={{ marginTop: space(4) }}
        />

        <Text style={[type.tiny, { marginTop: space(2), textAlign: 'center' }]}>
          연결하면 이전 서버의 대화 · 일정 캐시는 지워집니다.
        </Text>

        {/* ── 되돌아갈 자리 ─────────────────────────────────────── */}
        <View style={s.escape}>
          <Button
            label="백엔드 없이 둘러보기 (목 모드)"
            onPress={() => void apply('')}
            variant="ghost"
            disabled={mock}
          />
          {ENV_API_URL !== '' && (
            <Button
              label=".env 기본값으로 되돌리기"
              onPress={() => void apply(null)}
              variant="ghost"
              disabled={saved === null}
            />
          )}
        </View>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

/** 확인 결과. 실패하면 «왜» 까지 보여야 사용자가 다음 수를 고를 수 있다. */
function ProbeReport({ result, url }: { result: ProbeResult; url: string }) {
  const d = result.diagnostics;
  return (
    <Card style={{ marginTop: space(3), borderColor: result.ok ? colors.ok : colors.danger }}>
      <View style={s.row}>
        <Chip label={result.ok ? '응답함' : '연결 실패'} tone={result.ok ? 'ok' : 'danger'} />
        <Text style={[type.tiny, { flex: 1, textAlign: 'right' }]}>{result.ms}ms</Text>
      </View>
      <Text style={[type.mono, { marginTop: space(2) }]} numberOfLines={2}>{url}</Text>

      {!result.ok && (
        <>
          <Text style={[type.small, { marginTop: space(2), color: colors.danger }]}>
            {result.error}
          </Text>
          <Text style={[type.tiny, { marginTop: space(2) }]}>
            백엔드실행.bat 으로 서버가 떠 있는지, 포트가 8000 인지 확인해 주세요.
          </Text>
        </>
      )}

      {/* 붙긴 했는데 키가 비면 화면이 빈다. 그 원인을 여기서 미리 드러낸다. */}
      {result.ok && d && (
        <View style={{ marginTop: space(3), gap: space(2) }}>
          <Text style={type.tiny}>서버가 가진 키</Text>
          <View style={s.flags}>
            <Chip label={`LLM ${d.llm?.effective ?? '?'}`} tone="accent" />
            <Flag on={d.naver_maps} label="지도·길찾기" />
            <Flag on={d.naver_local_search} label="장소검색" />
            <Flag on={d.culture_api} label="공연·전시" />
            <Flag on={d.weather?.key} label="날씨" />
            <Flag on={d.websearch} label="웹검색" />
          </View>
        </View>
      )}

      {result.ok && !d && (
        <Text style={[type.tiny, { marginTop: space(2) }]}>
          연결은 됐지만 진단 정보를 읽지 못했습니다 — 구버전 서버일 수 있습니다.
        </Text>
      )}
    </Card>
  );
}

function Flag({ on, label }: { on?: boolean; label: string }) {
  return <Chip label={`${on ? '●' : '○'} ${label}`} tone={on ? 'ok' : 'default'} />;
}

const s = StyleSheet.create({
  content: { padding: space(4), paddingBottom: space(10) },
  row: { flexDirection: 'row', alignItems: 'center', gap: space(2) },
  input: {
    marginTop: space(2),
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.md,
    paddingHorizontal: space(3),
    paddingVertical: space(3),
    color: colors.text,
    fontSize: 15,
    minHeight: 46,
  },
  presets: { flexDirection: 'row', flexWrap: 'wrap', gap: space(2), marginTop: space(2) },
  preset: {
    backgroundColor: colors.surfaceAlt,
    borderRadius: radius.sm,
    paddingHorizontal: space(3),
    paddingVertical: space(2),
  },
  flags: { flexDirection: 'row', flexWrap: 'wrap', gap: space(2) },
  escape: {
    marginTop: space(8),
    borderTopWidth: 1,
    borderTopColor: colors.border,
    paddingTop: space(4),
    gap: space(1),
  },
});
