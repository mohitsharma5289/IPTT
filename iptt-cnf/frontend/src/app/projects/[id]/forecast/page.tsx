'use client';

import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useCallback, useEffect, useState } from 'react';

import { useRequireSession } from '@/components/session';
import { Shell } from '@/components/shell';
import { Empty, ErrorNote, Panel, Spinner, Stat, classNames } from '@/components/ui';
import { api } from '@/lib/api';
import type { ProjectForecast } from '@/lib/types';

function RiskPill({ risk }: { risk: string }) {
  const tone =
    risk === 'High'
      ? 'bg-risk/15 text-risk'
      : risk === 'Medium'
        ? 'bg-warn/15 text-warn'
        : 'bg-ok/15 text-ok';
  return <span className={classNames('pill', tone)}>{risk.toLowerCase()}</span>;
}

/** Forecast dashboard: where each node lands if it carries on as it has been.
 *
 *  The projection runs over working days. The legacy screen added the forecast
 *  remaining duration to today as *calendar* days, while the duration itself was
 *  measured in working days — inflating every forecast by roughly 40%, and more
 *  across a festival period. Figures here will therefore read earlier than the
 *  legacy screen for the same data; that is the correction, not a discrepancy.
 */
export default function ForecastPage() {
  const { id } = useParams<{ id: string }>();
  const projectId = Number(id);
  const { session, loading } = useRequireSession();
  const [data, setData] = useState<ProjectForecast | null>(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      setData(await api.forecast(projectId));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load the forecast');
    } finally {
      setBusy(false);
    }
  }, [projectId]);

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
        <Link href={`/projects/${projectId}`} className="label hover:text-accent">
          ← {data?.project_name ?? 'Project'}
        </Link>
        <h1 className="mt-1 text-xl font-semibold">Forecast</h1>
        <p className="mt-0.5 text-sm text-muted">
          Where each node lands at its current pace. Projected over working days.
        </p>
      </div>

      {error ? <ErrorNote message={error} onRetry={() => void load()} /> : null}
      {busy ? <Spinner label="Forecasting" /> : null}

      {!busy && data ? (
        <>
          <div className="card mb-5 grid grid-cols-2 divide-rule md:grid-cols-3 lg:grid-cols-6">
            <Stat label="Nodes" value={data.total_nodes} hint={`${data.started_nodes} started`} />
            <Stat label="High risk" value={data.high_risk} hint="more than 15 days adrift" tone={data.high_risk ? 'risk' : 'ok'} />
            <Stat label="Medium" value={data.medium_risk} hint="6 to 15 days" tone={data.medium_risk ? 'warn' : 'ok'} />
            <Stat label="Low" value={data.low_risk} hint="5 days or fewer" tone="ok" />
            <Stat label="Critical" value={data.critical_nodes} hint="beyond recovery without help" tone={data.critical_nodes ? 'risk' : 'ok'} />
            <Stat label="Go-live" value={data.forecast_go_live ?? '—'} hint="last node, forecast" />
          </div>

          {data.insights.length > 0 ? (
            <Panel title="Executive insights" subtitle="Generated from the figures below">
              <ul className="space-y-1.5 text-sm">
                {data.insights.map((insight) => (
                  <li key={insight} className="flex gap-2">
                    <span aria-hidden className="text-faint">
                      —
                    </span>
                    <span>{insight}</span>
                  </li>
                ))}
              </ul>
            </Panel>
          ) : null}

          <div className="mb-5" />

          <Panel title="Circles" subtitle="Worst average slip first" bleed>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[640px] text-sm">
                <thead className="sticky-head">
                  <tr className="border-b border-rule text-left text-xs text-muted">
                    <th className="px-3 py-2 font-medium">Circle</th>
                    <th className="px-3 py-2 text-right font-medium">Nodes</th>
                    <th className="px-3 py-2 text-right font-medium">Avg delay</th>
                    <th className="px-3 py-2 text-right font-medium">Avg progress</th>
                    <th className="px-3 py-2 text-right font-medium">High risk</th>
                    <th className="px-3 py-2 text-right font-medium">Forecast go-live</th>
                  </tr>
                </thead>
                <tbody>
                  {data.circles.map((circle) => (
                    <tr key={circle.circle} className="border-b border-rule/60 last:border-b-0">
                      <td className="px-3 py-2">
                        <Link
                          href={`/projects/${projectId}/circles/${encodeURIComponent(circle.circle)}`}
                          className="font-mono text-xs font-semibold hover:text-accent"
                        >
                          {circle.circle}
                        </Link>
                      </td>
                      <td className="px-3 py-2 text-right font-mono text-xs">{circle.nodes}</td>
                      <td className="px-3 py-2 text-right font-mono text-xs">
                        {circle.average_delay_days}
                      </td>
                      <td className="px-3 py-2 text-right font-mono text-xs text-muted">
                        {circle.average_progress}%
                      </td>
                      <td className="px-3 py-2 text-right font-mono text-xs">
                        {circle.high_risk || '—'}
                      </td>
                      <td className="px-3 py-2 text-right font-mono text-xs text-muted">
                        {circle.forecast_go_live ?? '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {data.circles.length === 0 ? <Empty message="No circles in scope." /> : null}
            </div>
          </Panel>

          <div className="mb-5" />

          <Panel title="Nodes" subtitle="Furthest adrift first" bleed>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[980px] text-sm">
                <thead className="sticky-head">
                  <tr className="border-b border-rule text-left text-xs text-muted">
                    <th className="px-3 py-2 font-medium">Node</th>
                    <th className="px-3 py-2 font-medium">Facility</th>
                    <th className="px-3 py-2 font-medium">Circle</th>
                    <th className="px-3 py-2 text-right font-medium">Progress</th>
                    <th className="px-3 py-2 text-right font-medium">Pace</th>
                    <th className="px-3 py-2 text-right font-medium">Slip</th>
                    <th className="px-3 py-2 font-medium">Blockers</th>
                    <th className="px-3 py-2 text-right font-medium">Go-live</th>
                    <th className="px-3 py-2 font-medium">Risk</th>
                    <th className="px-3 py-2 text-right font-medium">Confidence</th>
                  </tr>
                </thead>
                <tbody>
                  {data.nodes.map((node) => (
                    <tr key={node.scope_id} className="border-b border-rule/60 last:border-b-0">
                      <td className="px-3 py-2">
                        <Link
                          href={`/nodes/${node.scope_id}`}
                          className="font-mono text-xs hover:text-accent"
                        >
                          {node.node_id}
                        </Link>
                        {!node.started ? (
                          <span className="ml-1.5 text-xs text-faint">not started</span>
                        ) : null}
                      </td>
                      <td className="px-3 py-2 text-xs text-muted">{node.facility_name}</td>
                      <td className="px-3 py-2 font-mono text-xs text-muted">{node.circle}</td>
                      <td className="px-3 py-2 text-right font-mono text-xs">{node.progress}%</td>
                      <td
                        className="px-3 py-2 text-right font-mono text-xs text-muted"
                        title="Actual effort ÷ planned effort, capped at 3"
                      >
                        ×{node.performance_factor}
                      </td>
                      <td className="px-3 py-2 text-right font-mono text-xs">
                        {node.forecast_delay_days || '—'}
                      </td>
                      <td className="px-3 py-2 text-xs text-muted">
                        {node.delay_drivers.length === 0
                          ? '—'
                          : node.delay_drivers
                              .slice(0, 2)
                              .map((d) => d.activity)
                              .join(', ')}
                      </td>
                      <td className="px-3 py-2 text-right font-mono text-xs text-muted">
                        {node.forecast_go_live ?? '—'}
                      </td>
                      <td className="px-3 py-2">
                        <RiskPill risk={node.risk} />
                      </td>
                      <td className="px-3 py-2 text-right font-mono text-xs text-muted">
                        {node.confidence}
                        <span className="ml-1 text-faint">{node.confidence_band}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {data.nodes.length === 0 ? <Empty message="No nodes in scope yet." /> : null}
            </div>
          </Panel>
        </>
      ) : null}
    </Shell>
  );
}
