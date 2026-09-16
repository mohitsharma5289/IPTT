'use client';

import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';

import { useRequireSession } from '@/components/session';
import { Shell } from '@/components/shell';
import { Empty, ErrorNote, HealthBar, Panel, Spinner, Stat } from '@/components/ui';
import { api } from '@/lib/api';
import type { CircleIntelligenceRow } from '@/lib/types';

/** Circle intelligence: every circle across the whole portfolio.
 *
 *  A circle spans projects and programmes, which is why this cannot be
 *  assembled from the per-project circle roll-up — the same circle appears in
 *  several of those, and the node counts must be unioned rather than added.
 *  The programmes and projects columns show that overlap explicitly.
 */
export default function CircleIntelligencePage() {
  const { session, loading } = useRequireSession();
  const [rows, setRows] = useState<CircleIntelligenceRow[]>([]);
  const [totals, setTotals] = useState<Record<string, number>>({});
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const body = await api.circleIntelligence();
      setRows(body.circles);
      setTotals(body.totals);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load circles');
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
        <h1 className="mt-1 text-xl font-semibold">Circle intelligence</h1>
        <p className="mt-0.5 text-sm text-muted">
          Every circle across the portfolio, weakest first. A circle usually spans
          several projects, so the counts below are unions, not sums.
        </p>
      </div>

      {error ? <ErrorNote message={error} onRetry={() => void load()} /> : null}

      {busy ? (
        <Spinner label="Loading circles" />
      ) : (
        <>
          <div className="card mb-5 grid grid-cols-2 divide-rule md:grid-cols-3 lg:grid-cols-6">
            <Stat label="Circles" value={totals.circles ?? 0} hint="in view" />
            <Stat label="Facilities" value={totals.facilities ?? 0} hint="distinct sites" />
            <Stat label="Nodes" value={totals.nodes ?? 0} hint="in scope" />
            <Stat
              label="Live"
              value={totals.completed_nodes ?? 0}
              hint="reached the final gate"
              tone="ok"
            />
            <Stat label="In flight" value={totals.wip_nodes ?? 0} hint="started, not live" />
            <Stat
              label="Delay"
              value={totals.total_delay_days ?? 0}
              hint="working days, cumulative"
              tone={(totals.total_delay_days ?? 0) > 0 ? 'warn' : 'ok'}
            />
          </div>

          <Panel title="Circles" subtitle="Weakest first" bleed>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[900px] text-sm">
                <thead className="sticky-head">
                  <tr className="border-b border-rule text-left text-xs text-muted">
                    <th className="px-3 py-2 font-medium">Circle</th>
                    <th className="px-3 py-2 text-right font-medium">Facilities</th>
                    <th className="px-3 py-2 text-right font-medium">Programmes</th>
                    <th className="px-3 py-2 text-right font-medium">Projects</th>
                    <th className="px-3 py-2 text-right font-medium">Nodes</th>
                    <th className="px-3 py-2 text-right font-medium">Live</th>
                    <th className="px-3 py-2 text-right font-medium">In flight</th>
                    <th className="px-3 py-2 text-right font-medium">Progress</th>
                    <th className="px-3 py-2 font-medium">Health</th>
                    <th className="px-3 py-2 text-right font-medium">Delay</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr key={row.circle} className="border-b border-rule/60 last:border-b-0">
                      <td className="px-3 py-2 font-mono text-xs font-semibold">
                        {row.circle}
                      </td>
                      <td className="px-3 py-2 text-right font-mono text-xs">
                        {row.facilities}
                      </td>
                      <td className="px-3 py-2 text-right font-mono text-xs text-muted">
                        {row.programmes}
                      </td>
                      <td className="px-3 py-2 text-right font-mono text-xs text-muted">
                        {row.projects}
                      </td>
                      <td className="px-3 py-2 text-right font-mono text-xs">
                        {row.total_nodes}
                      </td>
                      <td className="px-3 py-2 text-right font-mono text-xs text-ok">
                        {row.completed_nodes || '—'}
                      </td>
                      <td className="px-3 py-2 text-right font-mono text-xs">
                        {row.wip_nodes || '—'}
                      </td>
                      <td className="px-3 py-2 text-right font-mono text-xs text-muted">
                        {row.progress}%
                      </td>
                      <td className="px-3 py-2">
                        <HealthBar value={row.health} />
                      </td>
                      <td className="px-3 py-2 text-right font-mono text-xs">
                        {row.total_delay_days || '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {rows.length === 0 ? (
                <Empty message="No scope anywhere yet, so there are no circles to report." />
              ) : null}
            </div>
          </Panel>
        </>
      )}
    </Shell>
  );
}
