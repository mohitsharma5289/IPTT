'use client';

import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';

import { useRequireSession } from '@/components/session';
import { Shell } from '@/components/shell';
import { Empty, ErrorNote, HealthBar, Panel, Spinner, Stat } from '@/components/ui';
import { api } from '@/lib/api';
import type { GovernanceRow } from '@/lib/types';

/** Governance dashboard: every programme, weakest first.
 *
 *  "Planning completion" is the share of a programme's projects that have a
 *  generated plan. It is deliberately *not* derived from `baseline_version`,
 *  which is 1 on every project from creation and says nothing about whether
 *  planning has happened.
 *
 *  A PM sees only their assigned projects here, so the dashboard cannot be used
 *  to read the whole portfolio without an assignment.
 */
export default function GovernancePage() {
  const { session, loading } = useRequireSession();
  const [rows, setRows] = useState<GovernanceRow[]>([]);
  const [totals, setTotals] = useState<Record<string, number>>({});
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const body = await api.governance();
      setRows(body.programmes);
      setTotals(body.totals);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load governance');
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
        <h1 className="mt-1 text-xl font-semibold">Governance dashboard</h1>
        <p className="mt-0.5 text-sm text-muted">
          Every programme, weakest first. Health is weighted by node, not averaged
          across projects.
        </p>
      </div>

      {error ? <ErrorNote message={error} onRetry={() => void load()} /> : null}

      {busy ? (
        <Spinner label="Loading governance" />
      ) : (
        <>
          <div className="card mb-5 grid grid-cols-2 divide-rule md:grid-cols-3 lg:grid-cols-6">
            <Stat label="Programmes" value={totals.programmes ?? 0} hint="in view" />
            <Stat label="Projects" value={totals.projects ?? 0} hint="across them" />
            <Stat
              label="Planned"
              value={totals.planned_projects ?? 0}
              hint="have a generated plan"
            />
            <Stat label="Nodes" value={totals.nodes ?? 0} hint="in scope" />
            <Stat
              label="At risk"
              value={totals.at_risk_nodes ?? 0}
              hint="more than 7 working days late"
              tone={(totals.at_risk_nodes ?? 0) > 0 ? 'risk' : 'ok'}
            />
            <Stat
              label="Delay"
              value={totals.total_delay_days ?? 0}
              hint="working days, cumulative"
              tone={(totals.total_delay_days ?? 0) > 0 ? 'warn' : 'ok'}
            />
          </div>

          <Panel title="Programmes" subtitle="Weakest first" bleed>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[820px] text-sm">
                <thead className="sticky-head">
                  <tr className="border-b border-rule text-left text-xs text-muted">
                    <th className="px-3 py-2 font-medium">Programme</th>
                    <th className="px-3 py-2 text-right font-medium">Projects</th>
                    <th className="px-3 py-2 text-right font-medium">Planned</th>
                    <th className="px-3 py-2 text-right font-medium">Planning</th>
                    <th className="px-3 py-2 text-right font-medium">Nodes</th>
                    <th className="px-3 py-2 font-medium">Health</th>
                    <th className="px-3 py-2 text-right font-medium">At risk</th>
                    <th className="px-3 py-2 text-right font-medium">Delay</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr
                      key={row.programme_id}
                      className="border-b border-rule/60 last:border-b-0"
                    >
                      <td className="px-3 py-2">
                        <Link
                          href={`/programmes/${row.programme_id}`}
                          className="font-medium hover:text-accent"
                        >
                          {row.programme_name}
                        </Link>
                        <div className="text-xs text-faint">{row.status}</div>
                      </td>
                      <td className="px-3 py-2 text-right font-mono text-xs">
                        {row.total_projects}
                      </td>
                      <td className="px-3 py-2 text-right font-mono text-xs">
                        {row.planned_projects}
                      </td>
                      <td className="px-3 py-2 text-right font-mono text-xs text-muted">
                        {row.planning_completion}%
                      </td>
                      <td className="px-3 py-2 text-right font-mono text-xs">
                        {row.total_nodes}
                      </td>
                      <td className="px-3 py-2">
                        <HealthBar value={row.health} />
                        <div className="text-xs text-faint">{row.health_status}</div>
                      </td>
                      <td className="px-3 py-2 text-right font-mono text-xs">
                        {row.at_risk_nodes || '—'}
                      </td>
                      <td className="px-3 py-2 text-right font-mono text-xs">
                        {row.total_delay_days || '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {rows.length === 0 ? (
                <Empty message="No programmes yet. Create one from the portfolio." />
              ) : null}
            </div>
          </Panel>
        </>
      )}
    </Shell>
  );
}
