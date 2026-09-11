'use client';

import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useCallback, useEffect, useMemo, useState } from 'react';

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
  classNames,
} from '@/components/ui';
import { api } from '@/lib/api';
import type {
  GovernanceMatrix,
  Heatmap,
  Kpis,
  NodeRow,
  Project,
  Stage,
} from '@/lib/types';

interface Loaded {
  project: Project;
  kpis: Kpis;
  nodes: NodeRow[];
  matrix: GovernanceMatrix;
  heatmap: Heatmap;
  stages: Stage[];
}

/** Stage mix rendered along the real ladder, so the shape of the pipeline is
 *  visible rather than a list of counts. Ordered by sequence position - the
 *  legacy chart ordered by count, which hid where work was piling up. */
function StageLadder({ stages, mix }: { stages: Stage[]; mix: Record<string, number> }) {
  const rows = useMemo(() => {
    const ordered = [
      { name: 'Not Started', position: 0, weight: 0 },
      ...[...stages].sort((a, b) => a.position - b.position),
    ];
    return ordered
      .map((stage) => ({ ...stage, count: mix[stage.name] ?? 0 }))
      .filter((row) => row.count > 0);
  }, [stages, mix]);

  const max = Math.max(1, ...rows.map((r) => r.count));

  if (rows.length === 0) return <Empty message="No nodes have reached a stage yet." />;

  return (
    <ol className="space-y-1.5">
      {rows.map((row) => (
        <li key={row.name} className="grid grid-cols-[1fr_auto] items-center gap-3">
          <div className="min-w-0">
            <div className="flex items-baseline justify-between gap-2">
              <span className="truncate text-xs">{row.name}</span>
              <span className="font-mono text-2xs text-faint">w{row.weight}</span>
            </div>
            <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-rule/50">
              <div
                className="h-full rounded-full bg-accent"
                style={{ width: `${(row.count / max) * 100}%` }}
              />
            </div>
          </div>
          <span className="w-8 text-right font-mono text-sm">{row.count}</span>
        </li>
      ))}
    </ol>
  );
}

/** Delay shading. Intensity is proportional to accumulated delay, capped so one
 *  extreme circle does not flatten the rest. */
function heatTone(days: number, max: number) {
  if (days <= 0) return { background: 'rgb(var(--rule) / 0.25)' };
  const ratio = Math.min(1, days / Math.max(max, 1));
  return { background: `rgb(var(--risk) / ${(0.12 + ratio * 0.5).toFixed(3)})` };
}

export default function ProjectDashboard() {
  const params = useParams<{ id: string }>();
  const projectId = Number(params.id);
  const { session, loading } = useRequireSession();

  const [data, setData] = useState<Loaded | null>(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [circleFilter, setCircleFilter] = useState<string>('all');

  const load = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const [project, kpis, nodes, matrix, heatmap, stages] = await Promise.all([
        api.project(projectId),
        api.kpis(projectId),
        api.nodes(projectId),
        api.matrix(projectId),
        api.heatmap(projectId),
        api.stages(),
      ]);
      setData({
        project,
        kpis,
        nodes: nodes.nodes,
        matrix,
        heatmap,
        stages: stages.stages,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load the dashboard');
    } finally {
      setBusy(false);
    }
  }, [projectId]);

  useEffect(() => {
    if (session && Number.isFinite(projectId)) void load();
  }, [session, projectId, load]);

  const circles = useMemo(
    () => (data ? [...new Set(data.nodes.map((n) => n.circle))].sort() : []),
    [data],
  );

  const visibleNodes = useMemo(() => {
    if (!data) return [];
    const rows =
      circleFilter === 'all'
        ? data.nodes
        : data.nodes.filter((n) => n.circle === circleFilter);
    // Worst first: the point of this table is triage.
    return [...rows].sort(
      (a, b) => b.worst_delay_days - a.worst_delay_days || a.weight - b.weight,
    );
  }, [data, circleFilter]);

  if (loading || !session) {
    return (
      <Shell>
        <Spinner label="Checking your session" />
      </Shell>
    );
  }

  if (busy) {
    return (
      <Shell>
        <Spinner label="Loading dashboard" />
      </Shell>
    );
  }

  if (error || !data) {
    return (
      <Shell>
        <ErrorNote message={error ?? 'Not found'} onRetry={() => void load()} />
      </Shell>
    );
  }

  const { project, kpis, matrix, heatmap, stages } = data;
  const maxCircleDelay = Math.max(0, ...heatmap.by_circle.map((c) => c.total_delay_days));
  const maxFacilityDelay = Math.max(
    0,
    ...heatmap.by_facility.map((f) => f.total_delay_days),
  );

  return (
    <Shell>
      <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <Link href="/" className="label hover:text-accent">
            ← {project.programme_name}
          </Link>
          <h1 className="mt-1 truncate text-xl font-semibold">{project.name}</h1>
          <p className="mt-0.5 text-sm text-muted">
            Kickoff {project.project_start_date ?? 'not set'} · baseline v
            {project.baseline_version} · {project.node_count} nodes
          </p>
        </div>
        <Link href={`/projects/${projectId}/execution`} className="btn-primary">
          Open execution grid
        </Link>
      </div>

      <div className="card mb-5 grid grid-cols-2 divide-rule md:grid-cols-3 lg:grid-cols-6">
        <Stat
          label="Health"
          value={kpis.health.toFixed(1)}
          hint={kpis.trend}
          tone={kpis.health >= 80 ? 'ok' : kpis.health >= 50 ? 'warn' : 'risk'}
        />
        <Stat label="Nodes" value={kpis.total_nodes} hint="in scope" />
        <Stat label="Live" value={kpis.live_nodes} hint={`${kpis.progress}% of nodes`} />
        <Stat
          label="At risk"
          value={kpis.at_risk_nodes}
          hint="more than 7 working days late"
          tone={kpis.at_risk_nodes > 0 ? 'risk' : 'ok'}
        />
        <Stat
          label="Delay"
          value={kpis.total_delay_days}
          hint="working days, cumulative"
          tone={kpis.total_delay_days > 0 ? 'warn' : 'ok'}
        />
        <Stat
          label="Circles"
          value={circles.length}
          hint="geographies in play"
        />
      </div>

      <div className="mb-5 grid gap-5 lg:grid-cols-[1fr_1fr]">
        <Panel title="Stage mix" subtitle="Nodes by furthest gate passed">
          <StageLadder stages={stages} mix={kpis.stage_mix} />
        </Panel>

        <Panel title="Delay by circle" subtitle="Cumulative working days late">
          {heatmap.by_circle.length === 0 ? (
            <Empty message="No delay recorded." />
          ) : (
            <ul className="space-y-1">
              {heatmap.by_circle.map((entry) => (
                <li
                  key={entry.key}
                  className="flex items-center justify-between rounded px-2.5 py-1.5 text-xs"
                  style={heatTone(entry.total_delay_days, maxCircleDelay)}
                >
                  <span className="font-mono font-medium">{entry.key}</span>
                  <span className="text-muted">
                    {entry.delayed_tasks} activities ·{' '}
                    <span className="font-mono text-ink">{entry.total_delay_days}d</span>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>

      <div className="mb-5">
        <Panel
          title="Governance matrix"
          subtitle="Stage against circle"
          bleed
          actions={
            <span className="font-mono text-xs text-muted">
              {matrix.grand_total} nodes
            </span>
          }
        >
          <div className="overflow-x-auto">
            <table className="w-full min-w-[520px] text-xs">
              <thead>
                <tr className="border-b border-rule bg-raised">
                  <th className="sticky left-0 z-10 bg-raised px-3 py-2 text-left font-medium text-muted">Stage</th>
                  {matrix.circles.map((circle) => (
                    <th key={circle} className="px-2 py-2 text-right font-mono font-medium text-muted">
                      {circle}
                    </th>
                  ))}
                  <th className="px-3 py-2 text-right font-medium text-muted">Total</th>
                </tr>
              </thead>
              <tbody>
                {matrix.rows.map((row, index) => {
                  const isTotal = row.stage === 'Total';
                  return (
                    <tr
                      key={`${row.stage}-${index}`}
                      className={classNames(
                        'border-b border-rule/60 last:border-b-0',
                        isTotal && 'bg-raised font-semibold',
                      )}
                    >
                      <td className={classNames('sticky left-0 z-10 px-3 py-1.5', isTotal ? 'bg-raised' : 'bg-surface')}>{row.stage}</td>
                      {matrix.circles.map((circle) => {
                        const value = Number(row[circle] ?? 0);
                        return (
                          <td
                            key={circle}
                            className={classNames(
                              'px-2 py-1.5 text-right font-mono',
                              value === 0 && 'text-faint',
                            )}
                          >
                            {value || '·'}
                          </td>
                        );
                      })}
                      <td className="px-3 py-1.5 text-right font-mono">{row.Total}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Panel>
      </div>

      <div className="mb-5">
        <Panel title="Delay by facility" subtitle="Worst ten by cumulative working days late">
          {heatmap.by_facility.length === 0 ? (
            <Empty message="No delay recorded." />
          ) : (
            <ul className="grid gap-1 sm:grid-cols-2">
              {heatmap.by_facility.slice(0, 10).map((entry) => (
                <li
                  key={entry.key}
                  className="flex items-center justify-between gap-3 rounded px-2.5 py-1.5 text-xs"
                  style={heatTone(entry.total_delay_days, maxFacilityDelay)}
                >
                  <span className="truncate font-medium">{entry.key}</span>
                  <span className="shrink-0 text-muted">
                    {entry.delayed_tasks} ·{' '}
                    <span className="font-mono text-ink">{entry.total_delay_days}d</span>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>

      <Panel
        title="Nodes"
        subtitle="Sorted worst first"
        bleed
        actions={
          <div className="flex items-center gap-2">
            <label htmlFor="circle" className="label">
              Circle
            </label>
            <select
              id="circle"
              className="field w-auto !py-1 !text-xs"
              value={circleFilter}
              onChange={(e) => setCircleFilter(e.target.value)}
            >
              <option value="all">All ({data.nodes.length})</option>
              {circles.map((circle) => (
                <option key={circle} value={circle}>
                  {circle}
                </option>
              ))}
            </select>
          </div>
        }
      >
        <div className="overflow-x-auto">
          <table className="w-full min-w-[760px] text-xs">
            <thead className="sticky-head">
              <tr className="border-b border-rule">
                <th className="px-3 py-2 text-left font-medium text-muted">Node</th>
                <th className="px-3 py-2 text-left font-medium text-muted">Circle</th>
                <th className="px-3 py-2 text-left font-medium text-muted">Facility</th>
                <th className="sticky left-0 z-10 bg-raised px-3 py-2 text-left font-medium text-muted">Stage</th>
                <th className="px-3 py-2 text-left font-medium text-muted">Health</th>
                <th className="px-3 py-2 text-right font-medium text-muted">Done</th>
                <th className="px-3 py-2 text-right font-medium text-muted">Worst delay</th>
              </tr>
            </thead>
            <tbody>
              {visibleNodes.map((node) => (
                <tr key={node.scope_id} className="border-b border-rule/60 last:border-b-0">
                  <td className="px-3 py-1.5 font-mono">{node.node_id}</td>
                  <td className="px-3 py-1.5 font-mono text-muted">{node.circle}</td>
                  <td className="max-w-[16rem] truncate px-3 py-1.5 text-muted">
                    {node.facility_name}
                  </td>
                  <td className="px-3 py-1.5">{node.stage}</td>
                  <td className="px-3 py-1.5">
                    <HealthBar value={node.weight} />
                  </td>
                  <td className="px-3 py-1.5 text-right font-mono text-muted">
                    {node.completed_tasks}/{node.total_tasks}
                  </td>
                  <td className="px-3 py-1.5 text-right">
                    <DelayPill days={node.worst_delay_days} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {visibleNodes.length === 0 ? <Empty message="No nodes match this filter." /> : null}
        </div>
      </Panel>
    </Shell>
  );
}
