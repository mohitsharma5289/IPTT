'use client';

import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useCallback, useEffect, useState } from 'react';

import { useRequireSession, useSession } from '@/components/session';
import { Shell } from '@/components/shell';
import { SheetImport } from '@/components/sheet-import';
import { Dialog, Empty, ErrorNote, Field, Panel, Spinner } from '@/components/ui';
import { ApiError, api } from '@/lib/api';
import type { BaselineReadiness, Project, TemplateRow } from '@/lib/types';

/** An editable row.
 *
 *  One template activity is not one database row: the template is materialised
 *  per node, so activity 32 of a 57-node project is 57 task rows. Every edit
 *  here therefore fans out across the whole project, which is why the row shows
 *  the node count and why removing an activity with recorded dates is refused.
 */
function EditableRow({
  row,
  projectId,
  editable,
  onChanged,
  onError,
}: {
  row: TemplateRow;
  projectId: number;
  editable: boolean;
  onChanged: () => void;
  onError: (message: string | null) => void;
}) {
  const [draft, setDraft] = useState(row);
  const [saving, setSaving] = useState(false);

  useEffect(() => setDraft(row), [row]);

  const dirty =
    draft.name !== row.name ||
    draft.duration_days !== row.duration_days ||
    draft.predecessor_template_number !== row.predecessor_template_number ||
    draft.owner_role !== row.owner_role ||
    draft.is_prerequisite !== row.is_prerequisite;

  async function save() {
    if (!dirty) return;
    setSaving(true);
    onError(null);
    try {
      await api.updateTemplateRow(projectId, row.template_task_number, {
        name: draft.name,
        duration_days: draft.duration_days,
        predecessor_template_number: draft.predecessor_template_number,
        is_prerequisite: draft.is_prerequisite,
        owner_role: draft.owner_role,
      });
      onChanged();
    } catch (err) {
      // Put the row back: a rejected edit (a cycle, a dangling predecessor)
      // must not leave the screen showing a value the server refused.
      setDraft(row);
      onError(err instanceof ApiError ? err.message : 'Could not save the change');
    } finally {
      setSaving(false);
    }
  }

  async function remove() {
    if (!window.confirm(`Remove activity ${row.template_task_number} from every node?`)) return;
    onError(null);
    try {
      await api.deleteTemplateRow(projectId, row.template_task_number);
      onChanged();
    } catch (err) {
      const message = err instanceof ApiError ? err.message : 'Could not remove the activity';
      if (
        err instanceof ApiError &&
        err.status === 409 &&
        message.includes('recorded dates') &&
        window.confirm(`${message}\n\nRemove it anyway?`)
      ) {
        try {
          await api.deleteTemplateRow(projectId, row.template_task_number, true);
          onChanged();
          return;
        } catch (force) {
          onError(force instanceof ApiError ? force.message : 'Could not remove');
          return;
        }
      }
      onError(message);
    }
  }

  if (!editable) {
    return (
      <tr className="border-b border-rule/60 last:border-b-0">
        <td className="px-3 py-1.5 text-right font-mono text-faint">
          {row.template_task_number}
        </td>
        <td className="px-3 py-1.5">{row.name}</td>
        <td className="px-3 py-1.5 text-right font-mono text-muted">{row.duration_days} wd</td>
        <td className="px-3 py-1.5 text-right font-mono text-muted">
          {row.predecessor_template_number ?? '—'}
        </td>
        <td className="px-3 py-1.5">
          {row.is_prerequisite ? (
            <span className="pill bg-accent/15 text-accent">prerequisite</span>
          ) : (
            <span className="text-faint">—</span>
          )}
        </td>
        <td className="px-3 py-1.5 text-muted">{row.owner_role ?? '—'}</td>
        <td className="px-3 py-1.5 text-right font-mono text-muted">
          {row.recorded_dates || '—'}
        </td>
      </tr>
    );
  }

  return (
    <tr className="border-b border-rule/60 last:border-b-0">
      <td className="px-3 py-1 text-right font-mono text-faint">{row.template_task_number}</td>
      <td className="px-2 py-1">
        <input
          className="field !py-1 !text-xs"
          value={draft.name}
          disabled={saving}
          onChange={(e) => setDraft({ ...draft, name: e.target.value })}
          onBlur={save}
          aria-label={`Name of activity ${row.template_task_number}`}
        />
      </td>
      <td className="px-2 py-1">
        <input
          type="number"
          min={0}
          className="field !w-20 !py-1 text-right !text-xs"
          value={draft.duration_days}
          disabled={saving}
          onChange={(e) => setDraft({ ...draft, duration_days: Number(e.target.value) })}
          onBlur={save}
          aria-label={`Duration of activity ${row.template_task_number}`}
        />
      </td>
      <td className="px-2 py-1">
        <input
          type="number"
          min={0}
          className="field !w-20 !py-1 text-right !text-xs"
          value={draft.predecessor_template_number ?? ''}
          disabled={saving}
          placeholder="—"
          onChange={(e) =>
            setDraft({
              ...draft,
              predecessor_template_number: e.target.value ? Number(e.target.value) : null,
            })
          }
          onBlur={save}
          aria-label={`Predecessor of activity ${row.template_task_number}`}
        />
      </td>
      <td className="px-2 py-1">
        <input
          type="checkbox"
          checked={draft.is_prerequisite}
          disabled={saving}
          onChange={(e) => {
            const next = { ...draft, is_prerequisite: e.target.checked };
            setDraft(next);
            // Checkboxes have no useful blur, so commit immediately.
            void (async () => {
              setSaving(true);
              onError(null);
              try {
                await api.updateTemplateRow(projectId, row.template_task_number, {
                  is_prerequisite: e.target.checked,
                });
                onChanged();
              } catch (err) {
                setDraft(row);
                onError(err instanceof ApiError ? err.message : 'Could not save');
              } finally {
                setSaving(false);
              }
            })();
          }}
          aria-label={`Activity ${row.template_task_number} is a prerequisite gate`}
        />
      </td>
      <td className="px-2 py-1">
        <input
          className="field !py-1 !text-xs"
          value={draft.owner_role ?? ''}
          disabled={saving}
          placeholder="—"
          onChange={(e) => setDraft({ ...draft, owner_role: e.target.value || null })}
          onBlur={save}
          aria-label={`Owner role for activity ${row.template_task_number}`}
        />
      </td>
      <td className="px-3 py-1 text-right font-mono text-muted">
        {row.recorded_dates || '—'}
      </td>
      <td className="px-3 py-1 text-right">
        <button type="button" className="text-xs text-risk hover:underline" onClick={remove}>
          Remove
        </button>
      </td>
    </tr>
  );
}

function AddActivityDialog({
  open,
  projectId,
  existing,
  onClose,
  onAdded,
}: {
  open: boolean;
  projectId: number;
  existing: TemplateRow[];
  onClose: () => void;
  onAdded: () => void;
}) {
  const nextNumber = Math.max(0, ...existing.map((r) => r.template_task_number)) + 1;
  const [number, setNumber] = useState(nextNumber);
  const [name, setName] = useState('');
  const [duration, setDuration] = useState(1);
  const [predecessor, setPredecessor] = useState<number | ''>('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setNumber(nextNumber);
    setName('');
    setDuration(1);
    setPredecessor(existing.length ? existing[existing.length - 1].template_task_number : '');
    setError(null);
  }, [open, nextNumber, existing]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.addTemplateRow(projectId, {
        template_task_number: number,
        name: name.trim(),
        duration_days: duration,
        predecessor_template_number: predecessor === '' ? null : Number(predecessor),
      });
      onAdded();
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not add the activity');
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog
      open={open}
      title="Add activity"
      description="Added to every node in the project's scope."
      onClose={onClose}
    >
      <form onSubmit={submit} className="space-y-3">
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Activity number" hint="Its position in the template order.">
            <input
              type="number"
              min={1}
              className="field"
              value={number}
              onChange={(e) => setNumber(Number(e.target.value))}
              required
            />
          </Field>
          <Field label="Duration (working days)">
            <input
              type="number"
              min={0}
              className="field"
              value={duration}
              onChange={(e) => setDuration(Number(e.target.value))}
              required
            />
          </Field>
        </div>
        <Field label="Name">
          <input
            className="field"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
            maxLength={200}
            autoFocus
          />
        </Field>
        <Field label="Starts after" hint="Leave blank to start at kickoff.">
          <select
            className="field"
            value={predecessor}
            onChange={(e) => setPredecessor(e.target.value ? Number(e.target.value) : '')}
          >
            <option value="">— nothing —</option>
            {existing.map((r) => (
              <option key={r.template_task_number} value={r.template_task_number}>
                {r.template_task_number} · {r.name}
              </option>
            ))}
          </select>
        </Field>
        {error ? <ErrorNote message={error} /> : null}
        <div className="flex justify-end gap-2 pt-1">
          <button type="button" className="btn-ghost" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button type="submit" className="btn" disabled={busy}>
            {busy ? 'Adding…' : 'Add activity'}
          </button>
        </div>
      </form>
    </Dialog>
  );
}

export default function TemplatePage() {
  const { id } = useParams<{ id: string }>();
  const projectId = Number(id);
  const { session, loading } = useRequireSession();
  const { can } = useSession();
  const isAdmin = can('admin');

  const [project, setProject] = useState<Project | null>(null);
  const [readiness, setReadiness] = useState<BaselineReadiness | null>(null);
  const [rows, setRows] = useState<TemplateRow[]>([]);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [rowError, setRowError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);

  const load = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const [p, t, r] = await Promise.all([
        api.project(projectId),
        api.template(projectId),
        api.baselineReadiness(projectId),
      ]);
      setProject(p);
      setRows(t);
      setReadiness(r);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load the template');
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

  const nodeCount = rows[0]?.node_count ?? 0;
  const locked = readiness?.locked ?? false;

  return (
    <Shell>
      <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
        <div>
          <Link href={`/projects/${projectId}`} className="label hover:text-accent">
            ← {project?.name ?? 'Project'}
          </Link>
          <h1 className="mt-1 text-xl font-semibold">Task template</h1>
          <p className="mt-0.5 text-sm text-muted">
            The ordered activities every node goes through. {rows.length} activities ×{' '}
            {nodeCount} nodes = {(rows.length * nodeCount).toLocaleString()} tracked items.
          </p>
        </div>
        {isAdmin ? (
          <button type="button" className="btn" onClick={() => setAdding(true)}>
            Add activity
          </button>
        ) : null}
      </div>

      {error ? <ErrorNote message={error} onRetry={() => void load()} /> : null}
      {rowError ? <ErrorNote message={rowError} /> : null}

      {isAdmin && rows.length > 0 ? (
        <p className="mb-4 text-xs text-muted">
          {locked
            ? 'Work has been recorded on this project, so the plan is frozen: template edits will not move existing dates. Re-baseline from the project page to replan.'
            : 'No work recorded yet, so the plan is regenerated automatically after each change.'}
        </p>
      ) : null}

      <AddActivityDialog
        open={adding}
        projectId={projectId}
        existing={rows}
        onClose={() => setAdding(false)}
        onAdded={() => void load()}
      />

      <SheetImport
        label="Template sheet"
        canWrite={isAdmin}
        onDownload={() => api.downloadTemplate(projectId)}
        onUpload={(file, dryRun, force) => api.importTemplate(projectId, file, dryRun, force)}
        onApplied={() => void load()}
        extraToggle={{
          label: 'Force removal',
          hint: 'Delete activities missing from the sheet even where PMs have recorded dates. This destroys that history.',
        }}
      />

      {busy ? (
        <Spinner label="Loading template" />
      ) : (
        <Panel
          title="Activities"
          subtitle={isAdmin ? 'In template order · edit in place' : 'In template order'}
          bleed
        >
          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px] text-xs">
              <thead className="sticky-head">
                <tr className="border-b border-rule">
                  <th className="px-3 py-2 text-right font-medium text-muted">#</th>
                  <th className="px-3 py-2 text-left font-medium text-muted">Activity</th>
                  <th className="px-3 py-2 text-right font-medium text-muted">Duration</th>
                  <th className="px-3 py-2 text-right font-medium text-muted">After</th>
                  <th className="px-3 py-2 text-left font-medium text-muted">Gate</th>
                  <th className="px-3 py-2 text-left font-medium text-muted">Owner role</th>
                  <th className="px-3 py-2 text-right font-medium text-muted">Recorded</th>
                  {isAdmin ? <th className="px-3 py-2" /> : null}
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <EditableRow
                    key={row.template_task_number}
                    row={row}
                    projectId={projectId}
                    editable={isAdmin}
                    onChanged={() => void load()}
                    onError={setRowError}
                  />
                ))}
              </tbody>
            </table>
            {rows.length === 0 ? (
              <Empty message="No template loaded. Add an activity above, or download the starter sheet." />
            ) : null}
          </div>
        </Panel>
      )}
    </Shell>
  );
}
