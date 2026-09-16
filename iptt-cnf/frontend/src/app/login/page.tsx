'use client';

import Link from 'next/link';

import { useRouter } from 'next/navigation';
import { useEffect, useState } from 'react';

import { useSession } from '@/components/session';
import { ErrorNote } from '@/components/ui';

export default function LoginPage() {
  const { session, signIn, loading } = useSession();
  const router = useRouter();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!loading && session) router.replace('/');
  }, [loading, session, router]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await signIn(username, password);
      router.replace('/');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Sign in failed');
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center px-4 py-10">
      <div className="w-full max-w-sm">
        <div className="mb-6">
          <div className="font-mono text-lg font-semibold tracking-tight text-accent">IPTT</div>
          <h1 className="mt-1 text-xl font-semibold">Infrastructure Project Tracking</h1>
          <p className="mt-1 text-sm text-muted">Sign in to continue.</p>
        </div>

        {/* method="post" matters even though submit() always preventDefaults:
            between first paint and hydration the handler is not attached yet,
            and a click in that window performs the browser's own submit. The
            default is GET, which would put the password in the URL - and so
            into browser history, the proxy access log and the Referer header.
            POST keeps it in the body. */}
        <form method="post" onSubmit={submit} className="card space-y-4 p-5">
          {error ? <ErrorNote message={error} /> : null}

          <div>
            <label htmlFor="username" className="label mb-1 block">Username</label>
            <input id="username" name="username" autoComplete="username" required
              className="field" value={username} onChange={(e) => setUsername(e.target.value)} />
          </div>

          <div>
            <label htmlFor="password" className="label mb-1 block">Password</label>
            <input id="password" name="password" type="password" autoComplete="current-password"
              required className="field" value={password} onChange={(e) => setPassword(e.target.value)} />
          </div>

          <button type="submit" disabled={busy} className="btn-primary w-full justify-center">
            {busy ? 'Signing in…' : 'Sign in'}
          </button>

          <p className="text-xs text-muted">
            Repeated failed attempts lock the account for 15 minutes.
        </p>
        <p className="mt-2 text-center text-xs text-muted">
          No account?{' '}
          <Link href="/register" className="text-accent hover:underline">
            Request one
          </Link>
          </p>
        </form>
      </div>
    </main>
  );
}
