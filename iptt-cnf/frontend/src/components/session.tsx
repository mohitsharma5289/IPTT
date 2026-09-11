'use client';

import { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';

import { ApiError, api } from '@/lib/api';
import type { Role, Session } from '@/lib/types';

interface SessionState {
  session: Session | null;
  loading: boolean;
  signIn: (username: string, password: string) => Promise<Session>;
  signOut: () => Promise<void>;
  refresh: () => Promise<void>;
  can: (action: 'write' | 'admin') => boolean;
}

const SessionContext = createContext<SessionState | null>(null);

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);
  const router = useRouter();

  const refresh = useCallback(async () => {
    try {
      setSession(await api.me());
    } catch (error) {
      if (error instanceof ApiError && error.isAuth) setSession(null);
      else setSession(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const signIn = useCallback(async (username: string, password: string) => {
    const next = await api.login(username, password);
    setSession(next);
    return next;
  }, []);

  const signOut = useCallback(async () => {
    try {
      await api.logout();
    } finally {
      setSession(null);
      router.push('/login');
    }
  }, [router]);

  /** Authorisation shown in the UI is a convenience only. Every rule is
   *  enforced again on the server - the legacy app gated writes with
   *  JavaScript alone, so calling the API directly bypassed all of it. */
  const can = useCallback(
    (action: 'write' | 'admin') => {
      if (!session) return false;
      const role: Role = session.role;
      return action === 'admin' ? role === 'admin' : role === 'admin' || role === 'pm';
    },
    [session],
  );

  return (
    <SessionContext.Provider value={{ session, loading, signIn, signOut, refresh, can }}>
      {children}
    </SessionContext.Provider>
  );
}

export function useSession(): SessionState {
  const context = useContext(SessionContext);
  if (!context) throw new Error('useSession must be used inside SessionProvider');
  return context;
}

/** Redirects to /login when there is no session. */
export function useRequireSession() {
  const state = useSession();
  const router = useRouter();
  useEffect(() => {
    if (!state.loading && !state.session) router.replace('/login');
  }, [state.loading, state.session, router]);
  return state;
}
