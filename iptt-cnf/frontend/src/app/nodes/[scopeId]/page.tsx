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
  StatusPill,
  classNames,
} from '@/components/ui';
import { api } from '@/lib/api';
import type { NodeDetail } from '@/lib/types';

/** One node, every activity, in template order.
 *
 *  The execution grid shows all nodes at once and is built for bulk data entry.
 *  This is the view you want when a single site is in trouble and you need its
 *  whole history on one screen.
 */
export default function NodeDetailPage() {
  const { scopeId } = useParams<{ scopeId: string }>();
  const id = Number(scopeId);
  const { session, loading } = useRequireSession();
  const [data, setData] = useState<NodeDetail | null>(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      setData(await api.nodeDetail(id));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load the node');
    } finally {
      setBusy(false);
    }
  }, [id]);

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

  const done = data?.activities.filter((a) => a.status === 'Completed').length ?? 0;

  return (
    <Shell>
      <div className="mb-5">
        {data ? (
          <Link href={`/projects/${data.project_id}`} className="label hover:text-accent">
            ← Project dashboard
          </Link>
        ) : null}
        <h1 className="mt-1 font-mono text-xl font-semibold">{data?.node_id ?? 'Node'}</h1>
        {data ? (
          <p className="mt-0.5 text-sm text-muted">
            <Link
              href={`/projects/${data.project_id}/facilities/${encodeURIComponent(data.facility_name)}`}
              className="hover:text-accent"
            >
              {data.facility_name}
            </Link>
            {' · '}
            <Link
              href={`/projects/${data.project_id}/circles/${encodeURIComponent(data.circle)}`}
              className="hover:text-accent"
            >
              {data.circle}
            </Link>
            {' · '}
            {data.num_servers ?? '—'} servers
          </p>
        ) : null}
      </div>

      {error ? <ErrorNote message={error} onRetry={() => void load()} /> : null}
      {busy ? <Spinner label="Loading node" /> : null}

      {!busy && data ? (
        <>
          <div className="card mb-5 p-4">
            <div className="label">Furthest gate passed</div>
            <div className="mt-1 flex flex-wrap items-center gap-3">
              <span className="text-sm font-semibold">{data.stage}</span>
              <HealthBar value={data.weight} />
              <span className="font-mono text-xs text-muted">
                {done}/{data.activities.length} activities complete
              </span>
            </div>
          </div>

          <Panel title="Activities" subtitle="In template order" bleed>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[860px] text-sm">
                <thead className="sticky-head">
                  <tr className="border-b border-rule text-left text-xs text-muted">
                    <th className="px-3 py-2 text-right font-medium">#</th>
                    <th className="px-3 py-2 font-medium">Activity</th>
                    <th className="px-3 py-2 font-medium">Planned</th>
                    <th className="px-3 py-2 font-medium">Actual</th>
                    <th className="px-3 py-2 font-medium">Status</th>
                    <th className="px-3 py-2 text-right font-medium">Delay</th>
                    <th className="px-3 py-2 font-medium">Reason</th>
                  </tr>
                </thead>
                <tbody>
                  {data.activities.map((activity) => (
                    <tr
                      key={activity.execution_id}
                      className={classNames(
                        'border-b border-rule/60 last:border-b-0',
                        activity.delay_days > 7 && 'bg-risk/5',
                      )}
                    >
                      <td className="px-3 py-1.5 text-right font-mono text-xs text-faint">
                        {activity.template_task_number}
                      </td>
                      <td className="px-3 py-1.5">{activity.name}</td>
                      <td className="px-3 py-1.5 font-mono text-xs text-muted">
                        {activity.planned_start ?? '—'} → {activity.planned_finish ?? '—'}
                      </td>
                      <td className="px-3 py-1.5 font-mono text-xs">
                        {activity.actual_start ?? '—'} → {activity.actual_finish ?? '—'}
                      </td>
                      <td className="px-3 py-1.5">
                        <StatusPill status={activity.status} />
                      </td>
                      <td className="px-3 py-1.5 text-right">
                        <DelayPill days={activity.delay_days} />
                      </td>
                      <td className="px-3 py-1.5 text-xs text-muted">
                        {activity.delay_reason ?? '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {data.activities.length === 0 ? (
                <Empty message="This node has no activities yet — the project has no task template." />
              ) : null}
            </div>
          </Panel>
        </>
      ) : null}
    </Shell>
  );
}
