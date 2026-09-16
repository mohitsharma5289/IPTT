'use client';

import { useState } from 'react';

import { useRequireSession, useSession } from '@/components/session';
import { Shell } from '@/components/shell';
import { ErrorNote, Field, Panel, Spinner } from '@/components/ui';
import { ApiError, api } from '@/lib/api';

/** Your own account.
 *
 *  This closes a loop that was previously open: the bootstrap admin and every
 *  reset account are flagged `must_change_password` and shown a banner telling
 *  them to change it, and until now there was no screen on which to do so. The
 *  endpoint existed; nothing reached it.
 */
export default function AccountPage() {
  const { session, loading } = useRequireSession();
  const { refresh } = useSession();
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [confirm, setConfirm] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (next !== confirm) {
      setError('The two new passwords do not match');
      return;
    }
    setBusy(true);
    setError(null);
    setDone(false);
    try {
      await api.changePassword(current, next);
      setCurrent('');
      setNext('');
      setConfirm('');
      setDone(true);
      // Clears the must_change_password banner without a reload.
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not change the password');
    } finally {
      setBusy(false);
    }
  }

  if (loading || !session) {
    return (
      <Shell>
        <Spinner label="Checking your session" />
      </Shell>
    );
  }

  return (
    <Shell>
      <div className="mb-5">
        <h1 className="text-xl font-semibold">Your account</h1>
        <p className="mt-0.5 text-sm text-muted">
          Signed in as {session.username} ({session.role})
        </p>
      </div>

      <div className="max-w-md">
        <Panel
          title="Change password"
          subtitle={
            session.must_change_password
              ? 'Required: this account still uses a password it was issued with'
              : undefined
          }
        >
          <form onSubmit={submit} className="space-y-3">
            <Field label="Current password">
              <input
                type="password"
                className="field"
                value={current}
                onChange={(e) => setCurrent(e.target.value)}
                required
                autoComplete="current-password"
              />
            </Field>
            <Field
              label="New password"
              hint="At least 12 characters, with an uppercase letter, a lowercase letter, a digit and a symbol."
            >
              <input
                type="password"
                className="field"
                value={next}
                onChange={(e) => setNext(e.target.value)}
                required
                autoComplete="new-password"
              />
            </Field>
            <Field label="Confirm new password">
              <input
                type="password"
                className="field"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                required
                autoComplete="new-password"
              />
            </Field>

            {error ? <ErrorNote message={error} /> : null}
            {done ? <p className="text-sm text-ok">Password changed.</p> : null}

            <button type="submit" className="btn" disabled={busy}>
              {busy ? 'Changing…' : 'Change password'}
            </button>
          </form>
        </Panel>
      </div>
    </Shell>
  );
}
