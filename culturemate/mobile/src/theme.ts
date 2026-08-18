export const colors = {
  bg: '#0F1115',
  surface: '#171A21',
  surfaceAlt: '#1F232C',
  border: '#2A2F3A',
  text: '#ECEEF3',
  textDim: '#9AA3B2',
  textFaint: '#6B7382',
  accent: '#7C9CF5',
  accentSoft: '#243049',
  warn: '#F0B457',
  warnSoft: '#3A2E17',
  danger: '#E8797A',
  dangerSoft: '#3A2022',
  ok: '#6FCF97',
  okSoft: '#1D3226',
};

export const space = (n: number) => n * 4;

export const radius = { sm: 8, md: 12, lg: 16, xl: 22 };

export const type = {
  h1: { fontSize: 26, fontWeight: '700' as const, color: colors.text },
  h2: { fontSize: 19, fontWeight: '700' as const, color: colors.text },
  h3: { fontSize: 16, fontWeight: '600' as const, color: colors.text },
  body: { fontSize: 15, color: colors.text, lineHeight: 22 },
  small: { fontSize: 13, color: colors.textDim, lineHeight: 19 },
  tiny: { fontSize: 11, color: colors.textFaint },
  mono: { fontSize: 12, color: colors.textDim, fontFamily: 'monospace' as const },
};

export const severityColor = (s: number) =>
  s >= 3 ? colors.danger : s === 2 ? colors.warn : colors.accent;

export const severitySoft = (s: number) =>
  s >= 3 ? colors.dangerSoft : s === 2 ? colors.warnSoft : colors.accentSoft;
