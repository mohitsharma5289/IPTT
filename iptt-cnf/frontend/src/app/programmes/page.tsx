'use client';

import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';

import { useRequireSession } from '@/components/session';
import { Shell } from '@/components/shell';
import { Empty, ErrorNote, Panel, Spinner } from '@/components/ui';
import { api } from '@/lib/api';
import type { Programme } from '@/lib/types';

/** Programme view: the list the legacy Quick Reports linked to.
 *
 *  The roll-up for a single programme already existed at /programmes/[id], but
 *  was only reachable by selecting a programme filter on the portfolio — so in
 *  practice nobody found it.
 */
export default function ProgrammeListPage() {
  const { session, loading } = useRequireSession();
  const [programmes, setProgrammes] = useState<Programme[]>([]);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      setProgrammes(await api.programmes());
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load programmes');
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    if (session) void load();
  }, [session, load]);

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
        <Link href="/" className="label hover:text-accent">
          ← Portfolio
        </Link>
        <h1 className="mt-1 text-xl font-semibold">Programmes</h1>
        <p className="mt-0.5 text-sm text-muted">
          {programmes.length} programme{programmes.length === 1 ? '' : 's'}
        </p>
      </div>

      {error ? <ErrorNote message={error} onRetry={() => void load()} /> : null}

      {busy ? (
        <Spinner label="Loading programmes" />
      ) : programmes.length === 0 ? (
        <Panel title="No programmes yet" subtitle="Create one from the portfolio">
          <p className="text-sm text-muted">
            A programme groups related projects. Every project belongs to one.
          </p>
        </Panel>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {programmes.map((programme) => (
            <Link
              key={programme.id}
              href={`/programmes/${programme.id}`}
              className="card block p-4 transition-colors hover:border-accent focus-visible:border-accent"
            >
              <div className="label">{programme.status}</div>
              <h2 className="mt-1 text-sm font-semibold leading-snug">
                {programme.name}
              </h2>
              <dl className="mt-3 grid grid-cols-2 gap-2 border-t border-rule pt-3 text-xs">
                <div>
                  <dt className="text-faint">Projects</dt>
                  <dd className="font-mono text-sm text-ink">{programme.project_count}</dd>
                </div>
                <div>
                  <dt className="text-faint">Nodes</dt>
                  <dd className="font-mono text-sm text-ink">
                    {programme.node_count.toLocaleString()}
                  </dd>
                </div>
              </dl>
            </Link>
          ))}
        </div>
      )}
    </Shell>
  );
}
