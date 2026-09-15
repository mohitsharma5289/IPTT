'use client';

import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useCallback, useEffect, useState } from 'react';

import { useRequireSession } from '@/components/session';
import { Shell } from '@/components/shell';
import { Empty, ErrorNote, HealthBar, Panel, Spinner, Stat } from '@/components/ui';
import { api } from '@/lib/api';
import type { ProgrammeRollup, Stage } from '@/lib/types';

export default function ProgrammePage() {
  const { id } = useParams<{ id: string }>();
  const programmeId = Number(id);
  const { session, loading } = useRequireSession();

  const [rollup, setRollup] = useState<ProgrammeRollup | null>(null);
  const [stages, setStages] = useState<Stage[]>([]);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setBusy(true); setError(null);
    try {
      const [r, s] = await Promise.all([api.programmeRollup(programmeId), api.stages()]);
      setRollup(r); setStages(s.stages);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load the programme');
    } finally { setBusy(false); }
  }, [programmeId]);

  useEffect(() => { if (session) void load(); }, [session, load]);

  if (loading || !session) return <Shell><Spinner label="Checking your session" /></Shell>;
  if (busy) return <Shell><Spinner label="Loading programme" /></Shell>;
  if (error || !rollup) {
    return <Shell><ErrorNote message={error ?? 'Not found'} onRetry={() => void load()} /></Shell>;
  }

  const order = new Map(stages.map((s) => [s.name, s.position]));
  const mix = Object.entries(rollup.stage_mix)
    .sort((a, b) => (order.get(a[0]) ?? 0) - (order.get(b[0]) ?? 0));
  const peak = Math.max(1, ...mix.map(([, v]) => v));

  return (
    <Shell>
      <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
        <div>
          <Link href="/" className="label hover:text-accent">← Portfolio</Link>
          <h1 className="mt-1 text-xl font-semibold">{rollup.programme_name}</h1>
          <p className="mt-0.5 text-sm text-muted">
            {rollup.total_projects} project{rollup.total_projects === 1 ? '' : 's'} ·{' '}
            {rollup.total_nodes} node{rollup.total_nodes === 1 ? '' : 's'}
          </p>
        </div>
        <button type="button" className="btn-primary"
          onClick={() => void api.downloadProgrammePack(programmeId)}>
          Executive pack (PDF)
        </button>
      </div>

      <div className="card mb-5 grid grid-cols-2 divide-rule md:grid-cols-3 lg:grid-cols-6">
        <Stat label="Health" value={rollup.health.toFixed(1)} hint={rollup.status}
          tone={rollup.health >= 80 ? 'ok' : rollup.health >= 50 ? 'warn' : 'risk'} />
        <Stat label="Projects" value={rollup.total_projects} hint="in programme" />
        <Stat label="Nodes" value={rollup.total_nodes} hint="across all projects" />
        <Stat label="Live" value={rollup.live_nodes} hint={`${rollup.progress}% of nodes`} />
        <Stat label="At risk" value={rollup.at_risk_nodes} hint="more than 7 working days late"
          tone={rollup.at_risk_nodes > 0 ? 'risk' : 'ok'} />
        <Stat label="Delay" value={rollup.total_delay_days} hint="working days, cumulative"
          tone={rollup.total_delay_days > 0 ? 'warn' : 'ok'} />
      </div>

      <p className="mb-5 max-w-[75ch] text-sm">{rollup.narrative}</p>

      <div className="mb-5 grid gap-5 lg:grid-cols-2">
        <Panel title="Projects" subtitle="Weakest first" bleed>
          {rollup.projects.length === 0 ? <Empty message="No projects yet." /> : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[460px] text-xs">
                <thead className="sticky-head">
                  <tr className="border-b border-rule">
                    <th className="px-3 py-2 text-left font-medium text-muted">Project</th>
                    <th className="px-3 py-2 text-right font-medium text-muted">Nodes</th>
                    <th className="px-3 py-2 text-left font-medium text-muted">Health</th>
                    <th className="px-3 py-2 text-right font-medium text-muted">At risk</th>
                  </tr>
                </thead>
                <tbody>
                  {[...rollup.projects].sort((a, b) => a.health - b.health).map((p) => (
                    <tr key={p.project_id} className="border-b border-rule/60 last:border-b-0">
                      <td className="px-3 py-1.5">
                        <Link href={`/projects/${p.project_id}`} className="hover:text-accent">
                          {p.project_name}
                        </Link>
                        <span className="block text-faint">{p.dominant_stage}</span>
                      </td>
                      <td className="px-3 py-1.5 text-right font-mono">{p.nodes}</td>
                      <td className="px-3 py-1.5"><HealthBar value={p.health} /></td>
                      <td className="px-3 py-1.5 text-right font-mono">
                        {p.at_risk_nodes || <span className="text-faint">—</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>

        <Panel title="Circles" subtitle="Counted by node, weakest first" bleed>
          {rollup.circles.length === 0 ? <Empty message="No nodes yet." /> : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[420px] text-xs">
                <thead className="sticky-head">
                  <tr className="border-b border-rule">
                    <th className="px-3 py-2 text-left font-medium text-muted">Circle</th>
                    <th className="px-3 py-2 text-right font-medium text-muted">Nodes</th>
                    <th className="px-3 py-2 text-left font-medium text-muted">Health</th>
                    <th className="px-3 py-2 text-right font-medium text-muted">Delay</th>
                  </tr>
                </thead>
                <tbody>
                  {rollup.circles.map((c) => (
                    <tr key={c.circle} className="border-b border-rule/60 last:border-b-0">
                      <td className="px-3 py-1.5 font-mono">{c.circle}</td>
                      <td className="px-3 py-1.5 text-right font-mono">{c.nodes}</td>
                      <td className="px-3 py-1.5"><HealthBar value={c.health} /></td>
                      <td className="px-3 py-1.5 text-right font-mono text-muted">
                        {c.total_delay_days || '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
      </div>

      <Panel title="Stage mix" subtitle="Every node in the programme, by furthest gate passed">
        {mix.length === 0 ? <Empty message="No nodes have reached a stage yet." /> : (
          <ol className="space-y-1.5">
            {mix.map(([name, count]) => (
              <li key={name} className="grid grid-cols-[1fr_auto] items-center gap-3">
                <div className="min-w-0">
                  <span className="truncate text-xs">{name}</span>
                  <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-rule/50">
                    <div className="h-full rounded-full bg-accent"
                      style={{ width: `${(count / peak) * 100}%` }} />
                  </div>
                </div>
                <span className="w-8 text-right font-mono text-sm">{count}</span>
              </li>
            ))}
          </ol>
        )}
      </Panel>
    </Shell>
  );
}
