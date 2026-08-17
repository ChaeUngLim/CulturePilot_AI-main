import { ActivityIndicator, Pressable, StyleSheet, Text, View, type ViewStyle } from 'react-native';

import { colors, radius, space, type } from '@/theme';

export function Card({ children, style }: { children: React.ReactNode; style?: ViewStyle }) {
  return <View style={[s.card, style]}>{children}</View>;
}

export function Chip({ label, tone = 'default' }: { label: string; tone?: 'default' | 'ok' | 'warn' | 'danger' | 'accent' }) {
  const bg = {
    default: colors.surfaceAlt, ok: colors.okSoft, warn: colors.warnSoft,
    danger: colors.dangerSoft, accent: colors.accentSoft,
  }[tone];
  const fg = {
    default: colors.textDim, ok: colors.ok, warn: colors.warn,
    danger: colors.danger, accent: colors.accent,
  }[tone];
  return (
    <View style={[s.chip, { backgroundColor: bg }]}>
      <Text style={[type.tiny, { color: fg, fontWeight: '600' }]}>{label}</Text>
    </View>
  );
}

export function Button({
  label, onPress, variant = 'primary', disabled, loading, style,
}: {
  label: string;
  onPress: () => void;
  variant?: 'primary' | 'ghost' | 'outline';
  disabled?: boolean;
  loading?: boolean;
  style?: ViewStyle;
}) {
  const isPrimary = variant === 'primary';
  return (
    <Pressable
      onPress={onPress}
      disabled={disabled || loading}
      style={({ pressed }) => [
        s.btn,
        isPrimary && { backgroundColor: colors.accent },
        variant === 'outline' && { borderWidth: 1, borderColor: colors.border },
        (disabled || loading) && { opacity: 0.45 },
        pressed && { opacity: 0.75 },
        style,
      ]}
    >
      {loading ? (
        <ActivityIndicator size="small" color={isPrimary ? colors.bg : colors.text} />
      ) : (
        <Text style={[type.body, { fontWeight: '600', color: isPrimary ? '#0F1115' : colors.text }]}>
          {label}
        </Text>
      )}
    </Pressable>
  );
}

export function Empty({ title, body }: { title: string; body: string }) {
  return (
    <View style={s.empty}>
      <Text style={[type.h3, { textAlign: 'center' }]}>{title}</Text>
      <Text style={[type.small, { textAlign: 'center', marginTop: space(2) }]}>{body}</Text>
    </View>
  );
}

const s = StyleSheet.create({
  card: {
    backgroundColor: colors.surface,
    borderRadius: radius.lg,
    borderWidth: 1,
    borderColor: colors.border,
    padding: space(4),
  },
  chip: {
    paddingHorizontal: space(2),
    paddingVertical: space(1),
    borderRadius: radius.sm,
    alignSelf: 'flex-start',
  },
  btn: {
    paddingVertical: space(3),
    paddingHorizontal: space(4),
    borderRadius: radius.md,
    alignItems: 'center',
    justifyContent: 'center',
    minHeight: 44,
  },
  empty: { padding: space(8), alignItems: 'center', justifyContent: 'center' },
});
