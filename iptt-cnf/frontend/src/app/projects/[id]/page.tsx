'use client';

import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useCallback, useEffect, useMemo, useState } from 'react';

import { useRequireSession, useSession } from '@/components/session';
import { Shell } from '@/components/shell';
import {
  DelayPill,
  Dialog,
  Empty,
  ErrorNote,
  Field,
  HealthBar,
  Panel,
  Spinner,
  Stat,
  classNames,
} from '@/components/ui';
import { ActionsPanel } from '@/components/actions-panel';
import { ApiError, api } from '@/lib/api';
import { useRouter } from 'next/navigation';
import type {
  BaselineHistoryEntry,
  BaselineReadiness,
  GovernanceMatrix,
  Heatmap,
  Kpis,
  NodeRow,
  Project,
  Stage,
} from '@/lib/types';

const PROJECT_STATUSES = ['Not Started', 'In Progress', 'Completed', 'On Hold'];

/** Why this project has no plan yet, in the PM's terms.
 *
 *  A newly created project shows an empty dashboard, which reads as broken
 *  unless something says what is missing. The plan generates itself once the
 *  kickoff date, the scope and the task template all exist.
 */
function ReadinessNote({
  readiness,
  projectId,
}: {
  readiness: BaselineReadiness;
  projectId: number;
}) {
  if (readiness.planned) return null;
  return (
    <div className="card mb-5 border-warn/40 bg-warn/5 p-4">
      <h2 className="text-sm font-semibold text-warn">No plan generated yet</h2>
      <p className="mt-1 text-sm text-muted">
        This project still needs{' '}
        {readiness.missing.length === 1
          ? readiness.missing[0]
          : `${readiness.missing.slice(0, -1).join(', ')} and ${readiness.missing.at(-1)}`}
        . The plan is generated
        automatically as soon as all three are in place — there is no separate step.
      </p>
      <div className="mt-3 flex flex-wrap gap-2">
        <Link href={`/projects/${projectId}/scope`} className="btn-ghost !py-1 !text-xs">
          Add scope
        </Link>
        <Link href={`/projects/${projectId}/tasks`} className="btn-ghost !py-1 !text-xs">
          Load task template
        </Link>
      </div>
    </div>
  );
}

function EditProjectDialog({
  open,
  project,
  onClose,
  onSaved,
}: {
  open: boolean;
  project: Project;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState(project.name);
  const [status, setStatus] = useState(project.status);
  const [start, setStart] = useState(project.project_start_date ?? '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setName(project.name);
    setStatus(project.status);
    setStart(project.project_start_date ?? '');
    setError(null);
  }, [open, project]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const body: Record<string, unknown> = {};
    if (name.trim() !== project.name) body.name = name.trim();
    if (status !== project.status) body.status = status;
    if ((start || null) !== (project.project_start_date ?? null)) {
      body.project_start_date = start || null;
    }
    try {
      if (Object.keys(body).length) await api.updateProject(project.id, body);
      onSaved();
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save the project');
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} title="Edit project" onClose={onClose}>
      <form onSubmit={submit} className="space-y-3">
        <Field label="Name">
          <input
            className="field"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
            maxLength={200}
          />
        </Field>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Status">
            <select className="field" value={status} onChange={(e) => setStatus(e.target.value)}>
              {PROJECT_STATUSES.map((s) => (
                <option key={s}>{s}</option>
              ))}
            </select>
          </Field>
          <Field
            label="Kickoff date"
            hint={
              project.baseline_locked
                ? 'Locked: work has been recorded. Re-baseline to move it.'
                : 'Day 0 for the generated plan.'
            }
          >
            <input
              type="date"
              className="field"
              value={start}
              onChange={(e) => setStart(e.target.value)}
              disabled={project.baseline_locked}
            />
          </Field>
        </div>
        {error ? <ErrorNote message={error} /> : null}
        <div className="flex justify-end gap-2 pt-1">
          <button type="button" className="btn-ghost" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button type="submit" className="btn" disabled={busy}>
            {busy ? 'Saving…' : 'Save'}
          </button>
        </div>
      </form>
    </Dialog>
  );
}

/** Re-baselining, and the record of every previous one.
 *
 *  Kept deliberately explicit and reason-bearing: it archives the current
 *  execution state before replanning, and PM actuals are preserved (decision 6).
 *  The legacy equivalent deleted every execution row across four unguarded
 *  transactions with no rollback.
 */
function BaselinePanel({
  project,
  readiness,
  canWrite,
  onDone,
}: {
  project: Project;
  readiness: BaselineReadiness;
  canWrite: boolean;
  onDone: () => void;
}) {
  const [history, setHistory] = useState<BaselineHistoryEntry[]>([]);
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState('');
  const [kickoff, setKickoff] = useState(project.project_start_date ?? '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const loadHistory = useCallback(async () => {
    try {
      // `?? []` deliberately: a shape mismatch here previously set state to
      // undefined and crashed the whole dashboard on `history.length`.
      setHistory((await api.baselineHistory(project.id)).baselines ?? []);
    } catch {
      /* history is informational; a failure here must not break the page */
    }
  }, [project.id]);

  useEffect(() => {
    void loadHistory();
  }, [loadHistory]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const result = await api.rebaseline(project.id, {
        reason: reason.trim() || 're-baseline',
        kickoff_date: kickoff || null,
      });
      setNote(
        `Re-baselined to v${result.baseline_version}: ${result.tasks_planned} activities ` +
          `replanned, ${result.executions_preserved} recorded dates kept, ` +
          `${result.rows_archived} rows archived.`,
      );
      setOpen(false);
      setReason('');
      await loadHistory();
      onDone();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not re-baseline');
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel
      title="Baseline"
      subtitle={
        readiness.planned
          ? `Version ${project.baseline_version}${project.baseline_locked ? ' · locked, work recorded' : ' · no work recorded yet'}`
          : 'Not planned yet'
      }
      actions={
        canWrite && readiness.planned ? (
          <button type="button" className="btn-ghost !py-1 !text-xs" onClick={() => setOpen(true)}>
            Re-baseline
          </button>
        ) : null
      }
    >
      {note ? <p className="mb-3 text-sm text-ok">{note}</p> : null}

      {!readiness.planned ? (
        <Empty message="The plan generates itself once scope and a task template exist." />
      ) : history.length === 0 ? (
        <p className="text-sm text-muted">
          No re-baselines. The original plan is still in force.
        </p>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-rule text-left text-xs text-faint">
              <th className="pb-2 font-medium">Version</th>
              <th className="pb-2 font-medium">Archived</th>
              <th className="pb-2 font-medium">By</th>
              <th className="pb-2 font-medium">Rows</th>
              <th className="pb-2 font-medium">Reason</th>
            </tr>
          </thead>
          <tbody>
            {history.map((h) => (
              <tr key={h.baseline_version} className="border-b border-rule/60 last:border-0">
                <td className="py-2 font-mono text-xs">v{h.baseline_version}</td>
                <td className="py-2 font-mono text-xs text-muted">
                  {h.archived_at ? h.archived_at.slice(0, 16).replace('T', ' ') : '—'}
                </td>
                <td className="py-2 text-muted">{h.archived_by ?? '—'}</td>
                <td className="py-2 font-mono text-xs">{h.rows_archived}</td>
                <td className="py-2 text-muted">{h.reason ?? '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <Dialog
        open={open}
        title="Re-baseline this project"
        description="The current execution state is archived first, and recorded actual dates are kept."
        onClose={() => setOpen(false)}
      >
        <form onSubmit={submit} className="space-y-3">
          <Field label="Reason" hint="Recorded against the archive, so the history explains itself.">
            <input
              className="field"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="e.g. scope extended to 12 further nodes"
              required
              autoFocus
            />
          </Field>
          <Field label="New kickoff date" hint="Leave as-is to replan from the same Day 0.">
            <input
              type="date"
              className="field"
              value={kickoff}
              onChange={(e) => setKickoff(e.target.value)}
            />
          </Field>
          {error ? <ErrorNote message={error} /> : null}
          <div className="flex justify-end gap-2 pt-1">
            <button type="button" className="btn-ghost" onClick={() => setOpen(false)} disabled={busy}>
              Cancel
            </button>
            <button type="submit" className="btn" disabled={busy}>
              {busy ? 'Replanning…' : 'Re-baseline'}
            </button>
          </div>
        </form>
      </Dialog>
    </Panel>
  );
}

interface Loaded {
  project: Project;
  readiness: BaselineReadiness;
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
  const { can } = useSession();

  const [data, setData] = useState<Loaded | null>(null);
  const [editing, setEditing] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const router = useRouter();
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [circleFilter, setCircleFilter] = useState<string>('all');

  const load = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const [project, readiness, kpis, nodes, matrix, heatmap, stages] = await Promise.all([
        api.project(projectId),
        api.baselineReadiness(projectId),
        api.kpis(projectId),
        api.nodes(projectId),
        api.matrix(projectId),
        api.heatmap(projectId),
        api.stages(),
      ]);
      setData({
        project,
        readiness,
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

  const { project, readiness, kpis, matrix, heatmap, stages } = data;
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
            Kickoff {project.project_start_date ?? 'not set'} ·{' '}
            {readiness.planned
              ? `baseline v${project.baseline_version}`
              : 'not planned'} · {project.node_count} nodes
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {can('admin') ? (
            <button type="button" className="btn-ghost" onClick={() => setEditing(true)}>
              Edit
            </button>
          ) : null}
          <Link href={`/projects/${projectId}/forecast`} className="btn-ghost">Forecast</Link>
          <Link href={`/projects/${projectId}/scope`} className="btn-ghost">Scope</Link>
          <Link href={`/projects/${projectId}/tasks`} className="btn-ghost">Template</Link>
          <button type="button" className="btn-ghost"
            onClick={() => void api.downloadProjectPack(projectId)}>
            Executive pack (PDF)
          </button>
          <Link href={`/projects/${projectId}/execution`} className="btn-primary">
            Execution grid
          </Link>
        </div>
      </div>

      <EditProjectDialog
        open={editing}
        project={project}
        onClose={() => setEditing(false)}
        onSaved={() => void load()}
      />
      {deleteError ? <ErrorNote message={deleteError} /> : null}
      <ReadinessNote readiness={readiness} projectId={projectId} />

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
            {circleFilter !== 'all' ? (
              <button type="button" className="btn-ghost !py-1 !text-xs"
                onClick={() => void api.downloadCirclePack(projectId, circleFilter)}>
                {circleFilter} pack (PDF)
              </button>
            ) : null}
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

      <div className="mt-5">
        <BaselinePanel
          project={project}
          readiness={readiness}
          canWrite={can('admin')}
          onDone={() => void load()}
        />

        <ActionsPanel projectId={projectId} canWrite={can('write')} />

        {can('admin') ? (
          <Panel
            title="Danger zone"
            subtitle="Deleting a project removes its scope, template, execution history and archives"
          >
            <button
              type="button"
              className="btn-ghost !text-risk"
              onClick={async () => {
                if (!window.confirm(`Delete "${project.name}"? This cannot be undone.`)) return;
                setDeleteError(null);
                try {
                  await api.deleteProject(projectId);
                  router.push('/');
                } catch (err) {
                  // A 409 means recorded field data exists. Say so, and make
                  // the override a second, separate decision.
                  const message =
                    err instanceof ApiError ? err.message : 'Could not delete the project';
                  if (
                    err instanceof ApiError &&
                    err.status === 409 &&
                    window.confirm(`${message}\n\nDelete it anyway?`)
                  ) {
                    try {
                      await api.deleteProject(projectId, true);
                      router.push('/');
                      return;
                    } catch (force) {
                      setDeleteError(
                        force instanceof ApiError ? force.message : 'Could not delete',
                      );
                      return;
                    }
                  }
                  setDeleteError(message);
                }
              }}
            >
              Delete this project
            </button>
          </Panel>
        ) : null}
      </div>
    </Shell>
  );
}
