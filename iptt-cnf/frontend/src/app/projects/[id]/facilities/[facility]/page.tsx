'use client';

import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useCallback, useEffect, useState } from 'react';

import { useRequireSession } from '@/components/session';
import { Shell } from '@/components/shell';
import { DelayPill, Empty, ErrorNote, HealthBar, Panel, Spinner, Stat } from '@/components/ui';
import { api } from '@/lib/api';
import type { FacilityDetail } from '@/lib/types';

/** One facility. Several nodes often share a building, and when a site has a
 *  power or access problem they all stall together — which is invisible on any
 *  view organised by circle. */
export default function FacilityDetailPage() {
  const { id, facility } = useParams<{ id: string; facility: string }>();
  const projectId = Number(id);
  const facilityName = decodeURIComponent(facility);
  const { session, loading } = useRequireSession();
  const [data, setData] = useState<FacilityDetail | null>(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      setData(await api.facilityDetail(projectId, facilityName));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load the facility');
    } finally {
      setBusy(false);
    }
  }, [projectId, facilityName]);

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
          ← Project dashboard
        </Link>
        <h1 className="mt-1 text-xl font-semibold">{facilityName}</h1>
        <p className="mt-0.5 text-sm text-muted">
          {data ? `${data.node_count} nodes · ${data.circles.join(', ')}` : ''}
        </p>
      </div>

      {error ? <ErrorNote message={error} onRetry={() => void load()} /> : null}
      {busy ? <Spinner label="Loading facility" /> : null}

      {!busy && data ? (
        <>
          <div className="card mb-5 grid grid-cols-2 divide-rule md:grid-cols-4">
            <Stat label="Health" value={data.health.toFixed(1)} hint="weighted by node" />
            <Stat label="Nodes" value={data.node_count} hint="at this site" />
            <Stat label="Progress" value={`${data.progress}%`} hint="of nodes live" />
            <Stat label="Servers" value={data.total_servers} hint="installed capacity" />
          </div>

          <Panel title="Nodes at this facility" bleed>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[620px] text-sm">
                <thead className="sticky-head">
                  <tr className="border-b border-rule text-left text-xs text-muted">
                    <th className="px-3 py-2 font-medium">Node</th>
                    <th className="px-3 py-2 font-medium">Circle</th>
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
                      <td className="px-3 py-2">
                        <Link
                          href={`/projects/${projectId}/circles/${encodeURIComponent(node.circle)}`}
                          className="font-mono text-xs text-muted hover:text-accent"
                        >
                          {node.circle}
                        </Link>
                      </td>
                      <td className="px-3 py-2 text-right font-mono text-xs text-muted">
                        {node.num_servers ?? '—'}
                      </td>
                      <td className="px-3 py-2 text-xs">{node.stage}</td>
                      <td className="px-3 py-2">
                        <HealthBar value={node.weight} />
                      </td>
                      <td className="px-3 py-2 text-right">
                        <DelayPill days={node.total_delay_days} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {data.nodes.length === 0 ? <Empty message="No nodes at this facility." /> : null}
            </div>
          </Panel>
        </>
      ) : null}
    </Shell>
  );
}
