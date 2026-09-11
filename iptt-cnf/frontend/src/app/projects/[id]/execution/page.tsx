'use client';

import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { useRequireSession, useSession } from '@/components/session';
import { Shell } from '@/components/shell';
import {
  DelayPill,
  Empty,
  ErrorNote,
  StatusPill,
  classNames,
  Spinner,
} from '@/components/ui';
import { WorkbookControls } from '@/components/workbook';
import { api } from '@/lib/api';
import type {
  CircleCount,
  ExecutionGrid,
  ExecutionStatus,
  GridNode,
  Project,
  TaskRow,
} from '@/lib/types';

/** Activity groups. Ranges are contiguous and non-overlapping - the legacy
 *  `getTaskGroup` had overlapping ranges, so tasks 10-13 could never reach the
 *  logistics group and silently landed under planning instead. */
const GROUPS: { label: string; from: number; to: number }[] = [
  { label: 'Pre-checks & readiness', from: 1, to: 8 },
  { label: 'Planning & initiation', from: 9, to: 9 },
  { label: 'HW & IRM logistics', from: 10, to: 19 },
  { label: 'IP readiness', from: 20, to: 25 },
  { label: 'Cabling & labelling', from: 26, to: 28 },
  { label: 'HW configuration & handover', from: 29, to: 31 },
  { label: 'Application deployment', from: 32, to: 38 },
  { label: 'Clearances', from: 39, to: 40 },
  { label: 'Testing & ATP', from: 41, to: 44 },
  { label: 'IDC / NOC handover', from: 45, to: 48 },
  { label: 'Go-live', from: 49, to: 50 },
];

function groupOf(templateNumber: number): string {
  return (
    GROUPS.find((g) => templateNumber >= g.from && templateNumber <= g.to)?.label ??
    'Other'
  );
}

type SaveState = 'idle' | 'saving' | 'saved' | 'error';

interface PendingEdit {
  actual_start: string | null;
  actual_finish: string | null;
  delay_reason: string | null;
}

function deriveStatus(edit: PendingEdit): ExecutionStatus {
  if (edit.actual_finish) return 'Completed';
  if (edit.actual_start) return 'In Progress';
  return 'Not Started';
}

function TaskRowView({
  task,
  scopeId,
  canWrite,
  onSaved,
  onError,
}: {
  task: TaskRow;
  scopeId: number;
  canWrite: boolean;
  onSaved: (scopeId: number, taskId: number, next: TaskRow) => void;
  onError: (message: string) => void;
}) {
  const [edit, setEdit] = useState<PendingEdit>({
    actual_start: task.actual_start,
    actual_finish: task.actual_finish,
    delay_reason: task.delay_reason,
  });
  const [state, setState] = useState<SaveState>('idle');
  const [localError, setLocalError] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    setEdit({
      actual_start: task.actual_start,
      actual_finish: task.actual_finish,
      delay_reason: task.delay_reason,
    });
  }, [task.actual_start, task.actual_finish, task.delay_reason]);

  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);

  const status = deriveStatus(edit);

  /** Validation mirrors the server, which is the authority. The legacy grid
   *  enforced these rules only here, so anything calling the API directly could
   *  store a finish date before its start - and four such rows were found in
   *  the live data during migration. */
  function validate(next: PendingEdit): string | null {
    if (next.actual_finish && !next.actual_start) {
      return 'Enter an actual start before an actual finish.';
    }
    if (
      next.actual_start &&
      next.actual_finish &&
      next.actual_finish < next.actual_start
    ) {
      return 'Actual finish cannot be before actual start.';
    }
    return null;
  }

  const commit = useCallback(
    async (next: PendingEdit) => {
      const problem = validate(next);
      if (problem) {
        setLocalError(problem);
        setState('error');
        return;
      }
      setLocalError(null);
      setState('saving');
      try {
        await api.bulkUpdate([
          {
            scope_id: scopeId,
            task_id: task.task_id,
            actual_start: next.actual_start,
            actual_finish: next.actual_finish,
            status: deriveStatus(next),
            delay_reason: next.delay_reason,
          },
        ]);
        setState('saved');
        onSaved(scopeId, task.task_id, {
          ...task,
          ...next,
          status: deriveStatus(next),
        });
        setTimeout(() => setState('idle'), 1200);
      } catch (err) {
        setState('error');
        const message = err instanceof Error ? err.message : 'Save failed';
        setLocalError(message);
        onError(message);
      }
    },
    [scopeId, task, onSaved, onError],
  );

  /** Debounced so filling a row does not fire a request per keystroke. The
   *  legacy grid issued one PUT per field change - roughly 200 sequential
   *  requests to complete a single node. */
  function change(patch: Partial<PendingEdit>) {
    const next = { ...edit, ...patch };
    setEdit(next);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => void commit(next), 600);
  }

  const late = task.delay_days > 0;

  return (
    <tr
      className={classNames(
        'border-b border-rule/50 last:border-b-0',
        state === 'saved' && 'bg-ok/5',
        state === 'error' && 'bg-risk/5',
      )}
    >
      <td className="px-2 py-1 text-right font-mono text-2xs text-faint">
        {task.template_task_number}
      </td>
      <td className="max-w-[15rem] truncate px-2 py-1" title={task.task_name}>
        {task.task_name}
      </td>
      <td className="px-2 py-1 font-mono text-2xs text-muted">{task.planned_start ?? '—'}</td>
      <td className="px-2 py-1 font-mono text-2xs text-muted">{task.planned_finish ?? '—'}</td>
      <td className="px-1 py-1">
        <input
          type="date"
          aria-label={`Actual start for ${task.task_name}`}
          className="field !px-1.5 !py-0.5 !text-xs"
          disabled={!canWrite}
          value={edit.actual_start ?? ''}
          onChange={(e) => change({ actual_start: e.target.value || null })}
        />
      </td>
      <td className="px-1 py-1">
        <input
          type="date"
          aria-label={`Actual finish for ${task.task_name}`}
          className="field !px-1.5 !py-0.5 !text-xs"
          disabled={!canWrite}
          value={edit.actual_finish ?? ''}
          onChange={(e) => change({ actual_finish: e.target.value || null })}
        />
      </td>
      <td className="px-2 py-1">
        <StatusPill status={status} />
      </td>
      <td className="px-2 py-1 text-right">
        <DelayPill days={task.delay_days} />
      </td>
      <td className="px-1 py-1">
        <input
          type="text"
          aria-label={`Delay reason for ${task.task_name}`}
          placeholder={late ? 'Why?' : ''}
          className="field !px-1.5 !py-0.5 !text-xs"
          disabled={!canWrite}
          value={edit.delay_reason ?? ''}
          onChange={(e) => change({ delay_reason: e.target.value || null })}
        />
      </td>
      <td className="w-24 px-2 py-1 text-2xs">
        {state === 'saving' ? <span className="text-muted">saving…</span> : null}
        {state === 'saved' ? <span className="text-ok">saved</span> : null}
        {state === 'error' ? (
          <span className="text-risk" title={localError ?? undefined}>
            not saved
          </span>
        ) : null}
      </td>
    </tr>
  );
}

function NodePanel({
  node,
  canWrite,
  onSaved,
  onError,
}: {
  node: GridNode;
  canWrite: boolean;
  onSaved: (scopeId: number, taskId: number, next: TaskRow) => void;
  onError: (message: string) => void;
}) {
  const [open, setOpen] = useState(false);

  const grouped = useMemo(() => {
    const map = new Map<string, TaskRow[]>();
    for (const task of node.tasks) {
      const key = groupOf(task.template_task_number);
      const list = map.get(key);
      if (list) list.push(task);
      else map.set(key, [task]);
    }
    return [...map.entries()];
  }, [node.tasks]);

  const done = node.tasks.filter((t) => t.status === 'Completed').length;
  const worst = Math.max(0, ...node.tasks.map((t) => t.delay_days));

  return (
    <section className="card overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full flex-wrap items-center gap-x-4 gap-y-1 px-4 py-2.5 text-left hover:bg-raised"
      >
        <span className="font-mono text-sm font-semibold">{node.node_id}</span>
        <span className="font-mono text-xs text-muted">{node.circle}</span>
        <span className="min-w-0 flex-1 truncate text-xs text-muted">
          {node.facility_name}
        </span>
        <span className="font-mono text-xs text-muted">
          {done}/{node.tasks.length}
        </span>
        <DelayPill days={worst} />
        <span aria-hidden className="font-mono text-xs text-faint">
          {open ? '−' : '+'}
        </span>
      </button>

      {open ? (
        <div className="border-t border-rule">
          {grouped.map(([label, tasks]) => (
            <div key={label}>
              <h3 className="label border-b border-rule bg-raised px-4 py-1.5">{label}</h3>
              <div className="overflow-x-auto">
                <table className="w-full min-w-[980px] table-fixed text-xs">
                  <colgroup>
                    <col className="w-10" />
                    <col className="w-[17rem]" />
                    <col className="w-24" />
                    <col className="w-24" />
                    <col className="w-36" />
                    <col className="w-36" />
                    <col className="w-28" />
                    <col className="w-24" />
                    <col />
                    <col className="w-20" />
                  </colgroup>
                  <thead>
                    <tr className="border-b border-rule/60 text-2xs">
                      <th className="px-2 py-1 text-right font-medium text-faint">#</th>
                      <th className="px-2 py-1 text-left font-medium text-faint">Activity</th>
                      <th className="px-2 py-1 text-left font-medium text-faint">Plan start</th>
                      <th className="px-2 py-1 text-left font-medium text-faint">Plan finish</th>
                      <th className="px-2 py-1 text-left font-medium text-faint">Actual start</th>
                      <th className="px-2 py-1 text-left font-medium text-faint">Actual finish</th>
                      <th className="px-2 py-1 text-left font-medium text-faint">Status</th>
                      <th className="px-2 py-1 text-right font-medium text-faint">Delay</th>
                      <th className="px-2 py-1 text-left font-medium text-faint">Reason</th>
                      <th className="px-2 py-1 text-left font-medium text-faint" />
                    </tr>
                  </thead>
                  <tbody>
                    {tasks.map((task) => (
                      <TaskRowView
                        key={task.task_id}
                        task={task}
                        scopeId={node.scope_id}
                        canWrite={canWrite}
                        onSaved={onSaved}
                        onError={onError}
                      />
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ))}
        </div>
      ) : null}
    </section>
  );
}

export default function ExecutionPage() {
  const params = useParams<{ id: string }>();
  const projectId = Number(params.id);
  const { session, loading } = useRequireSession();
  const { can } = useSession();
  const canWrite = can('write');

  const [project, setProject] = useState<Project | null>(null);
  const [grid, setGrid] = useState<ExecutionGrid | null>(null);
  const [circles, setCircles] = useState<CircleCount[]>([]);
  const [circle, setCircle] = useState<string>('');
  const [page, setPage] = useState(1);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const pageSize = 8;

  const load = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const [p, g, c] = await Promise.all([
        api.project(projectId),
        api.grid(projectId, page, pageSize, circle || undefined),
        api.circles(projectId),
      ]);
      setProject(p);
      setGrid(g);
      setCircles(c.circles);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load the execution grid');
    } finally {
      setBusy(false);
    }
  }, [projectId, page, circle]);

  useEffect(() => {
    if (session && Number.isFinite(projectId)) void load();
  }, [session, projectId, load]);

  /** Apply a saved row in place so the grid does not reload and collapse every
   *  open node under the PM's cursor. */
  const applySaved = useCallback(
    (scopeId: number, taskId: number, next: TaskRow) => {
      setGrid((current) =>
        current
          ? {
              ...current,
              nodes: current.nodes.map((node) =>
                node.scope_id !== scopeId
                  ? node
                  : {
                      ...node,
                      tasks: node.tasks.map((t) => (t.task_id === taskId ? next : t)),
                    },
              ),
            }
          : current,
      );
    },
    [],
  );

  const pages = grid ? Math.max(1, Math.ceil(grid.total_nodes / grid.page_size)) : 1;

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
        <div className="min-w-0">
          <Link href={`/projects/${projectId}`} className="label hover:text-accent">
            ← {project?.name ?? 'Project'}
          </Link>
          <h1 className="mt-1 text-xl font-semibold">Execution</h1>
          <p className="mt-0.5 text-sm text-muted">
            {canWrite
              ? 'Dates save automatically. Planned dates are the baseline and are read-only here.'
              : 'You have read-only access to this project.'}
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <label htmlFor="circle" className="label">
            Circle
          </label>
          <select
            id="circle"
            className="field w-auto !py-1 !text-xs"
            value={circle}
            onChange={(e) => {
              setCircle(e.target.value);
              setPage(1);
            }}
          >
            <option value="">All circles</option>
            {circles.map((c) => (
              <option key={c.circle} value={c.circle}>
                {c.circle} ({c.node_count})
              </option>
            ))}
          </select>
        </div>
      </div>

      <WorkbookControls
        projectId={projectId}
        circle={circle || undefined}
        canWrite={canWrite}
        onApplied={() => void load()}
      />

      {error ? <ErrorNote message={error} onRetry={() => void load()} /> : null}
      {busy ? <Spinner label="Loading nodes" /> : null}

      {!busy && grid ? (
        <>
          <div className="space-y-2">
            {grid.nodes.map((node) => (
              <NodePanel
                key={node.scope_id}
                node={node}
                canWrite={canWrite}
                onSaved={applySaved}
                onError={setError}
              />
            ))}
            {grid.nodes.length === 0 ? <Empty message="No nodes match this filter." /> : null}
          </div>

          <nav
            className="mt-5 flex items-center justify-between gap-3 text-xs"
            aria-label="Pagination"
          >
            <span className="text-muted">
              Page {grid.page} of {pages} · {grid.total_nodes} nodes
            </span>
            <div className="flex gap-2">
              <button
                type="button"
                className="btn-ghost !py-1 !text-xs"
                disabled={page <= 1}
                onClick={() => setPage((p) => Math.max(1, p - 1))}
              >
                Previous
              </button>
              <button
                type="button"
                className="btn-ghost !py-1 !text-xs"
                disabled={page >= pages}
                onClick={() => setPage((p) => Math.min(pages, p + 1))}
              >
                Next
              </button>
            </div>
          </nav>
        </>
      ) : null}
    </Shell>
  );
}
