'use client';

import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useCallback, useEffect, useState } from 'react';

import { useRequireSession } from '@/components/session';
import { Shell } from '@/components/shell';
import {
  DelayPill,
  Empty,
  ErrorNote,
  HealthBar,
  Panel,
  Spinner,
  Stat,
} from '@/components/ui';
import { api } from '@/lib/api';
import type { CircleDetail } from '@/lib/types';

/** One circle inside one project. Reached from the dashboard, the forecast, or
 *  the circle intelligence report. */
export default function CircleDetailPage() {
  const { id, circle } = useParams<{ id: string; circle: string }>();
  const projectId = Number(id);
  const circleName = decodeURIComponent(circle);
  const { session, loading } = useRequireSession();
  const [data, setData] = useState<CircleDetail | null>(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      setData(await api.circleDetail(projectId, circleName));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load the circle');
    } finally {
      setBusy(false);
    }
  }, [projectId, circleName]);

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
      <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
        <div>
          <Link href={`/projects/${projectId}`} className="label hover:text-accent">
            ← Project dashboard
          </Link>
          <h1 className="mt-1 text-xl font-semibold">Circle {circleName}</h1>
          <p className="mt-0.5 text-sm text-muted">
            {data ? `${data.node_count} nodes across ${data.facilities.length} facilities` : ''}
          </p>
        </div>
        <button
          type="button"
          className="btn-ghost"
          onClick={() => void api.downloadCirclePack(projectId, circleName)}
        >
          Circle pack (PDF)
        </button>
      </div>

      {error ? <ErrorNote message={error} onRetry={() => void load()} /> : null}
      {busy ? <Spinner label="Loading circle" /> : null}

      {!busy && data ? (
        <>
          <div className="card mb-5 grid grid-cols-2 divide-rule md:grid-cols-4">
            <Stat label="Health" value={data.health.toFixed(1)} hint="weighted by node" />
            <Stat label="Nodes" value={data.node_count} hint="in this circle" />
            <Stat label="Live" value={data.live_nodes} hint={`${data.progress}% of nodes`} />
            <Stat label="Facilities" value={data.facilities.length} hint="distinct sites" />
          </div>

          <Panel title="Nodes" subtitle="With the furthest gate each has passed" bleed>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[760px] text-sm">
                <thead className="sticky-head">
                  <tr className="border-b border-rule text-left text-xs text-muted">
                    <th className="px-3 py-2 font-medium">Node</th>
                    <th className="px-3 py-2 font-medium">Facility</th>
                    <th className="px-3 py-2 text-right font-medium">Servers</th>
                    <th className="px-3 py-2 font-medium">Stage</th>
                    <th className="px-3 py-2 font-medium">Progress</th>
                    <th className="px-3 py-2 text-right font-medium">Delay</th>
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
                      </td>
                      <td className="px-3 py-2 text-xs">
                        <Link
                          href={`/projects/${projectId}/facilities/${encodeURIComponent(node.facility_name)}`}
                          className="text-muted hover:text-accent"
                        >
                          {node.facility_name}
                        </Link>
                      </td>
                      <td className="px-3 py-2 text-right font-mono text-xs text-muted">
                        {node.num_servers ?? '—'}
                      </td>
                      <td className="px-3 py-2 text-xs">{node.stage}</td>
                      <td className="px-3 py-2">
                        <div className="flex items-center gap-2">
                          <HealthBar value={node.weight} />
                          <span className="font-mono text-xs text-muted">
                            {node.completed_tasks}/{node.total_tasks}
                          </span>
                        </div>
                      </td>
                      <td className="px-3 py-2 text-right">
                        <DelayPill days={node.total_delay_days} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {data.nodes.length === 0 ? <Empty message="No nodes in this circle." /> : null}
            </div>
          </Panel>
        </>
      ) : null}
    </Shell>
  );
}
