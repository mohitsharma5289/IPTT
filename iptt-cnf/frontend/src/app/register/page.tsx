'use client';

import Link from 'next/link';
import { useState } from 'react';

import { ApiError, api } from '@/lib/api';
import { ErrorNote } from '@/components/ui';

/** Self-service registration.
 *
 *  The account is created pending: an administrator must approve it before it
 *  can sign in. The legacy endpoint created *active* accounts with no approval
 *  and no password rules, so anyone who could reach the page could mint a
 *  working login (audit M9).
 *
 *  The success message is identical whether or not the username was already
 *  taken — deliberately. Saying "that name is taken" to an unauthenticated
 *  caller turns the form into a way to enumerate who has an account.
 */
export default function RegisterPage() {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (password !== confirm) {
      setError('The two passwords do not match');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const body = await api.register(username.trim(), password);
      setDone(body.detail);
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : 'Could not submit your request',
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto flex min-h-screen max-w-md flex-col justify-center px-4">
      <div className="mb-6">
        <span className="font-mono text-sm font-semibold tracking-tight text-accent">
          IPTT
        </span>
        <span className="ml-2 text-xs text-muted">Infrastructure Project Tracking</span>
      </div>

      {done ? (
        <div className="card space-y-4 p-5">
          <h1 className="text-base font-semibold">Request received</h1>
          <p className="text-sm text-muted">{done}</p>
          <Link href="/login" className="btn inline-block">
            Back to sign in
          </Link>
        </div>
      ) : (
        <form method="post" onSubmit={submit} className="card space-y-4 p-5">
          <div>
            <h1 className="text-base font-semibold">Request an account</h1>
            <p className="mt-1 text-sm text-muted">
              An administrator approves each request before the account can sign in.
            </p>
          </div>

          <label className="block">
            <span className="label">Username</span>
            <input
              id="username"
              name="username"
              className="field mt-1"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
              minLength={3}
              maxLength={150}
              autoComplete="username"
              autoFocus
            />
          </label>

          <label className="block">
            <span className="label">Password</span>
            <input
              id="password"
              name="password"
              type="password"
              className="field mt-1"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              autoComplete="new-password"
            />
            <p className="mt-1 text-xs text-faint">
              At least 12 characters, with an uppercase letter, a lowercase letter, a
              digit and a symbol.
            </p>
          </label>

          <label className="block">
            <span className="label">Confirm password</span>
            <input
              id="confirm"
              name="confirm"
              type="password"
              className="field mt-1"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              required
              autoComplete="new-password"
            />
          </label>

          {error ? <ErrorNote message={error} /> : null}

          <button type="submit" className="btn w-full" disabled={busy}>
            {busy ? 'Submitting…' : 'Request account'}
          </button>

          <p className="text-center text-xs text-muted">
            Already have one?{' '}
            <Link href="/login" className="text-accent hover:underline">
              Sign in
            </Link>
          </p>
        </form>
      )}
    </div>
  );
}
