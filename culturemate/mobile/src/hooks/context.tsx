import { createContext, useContext, type ReactNode } from 'react';

import { useCultureMate } from './useCultureMate';

type Ctx = ReturnType<typeof useCultureMate>;

const CultureMateContext = createContext<Ctx | null>(null);

export function CultureMateProvider({ children }: { children: ReactNode }) {
  const value = useCultureMate();
  return <CultureMateContext.Provider value={value}>{children}</CultureMateContext.Provider>;
}

export function useCM(): Ctx {
  const ctx = useContext(CultureMateContext);
  if (!ctx) throw new Error('useCM must be used within CultureMateProvider');
  return ctx;
}
