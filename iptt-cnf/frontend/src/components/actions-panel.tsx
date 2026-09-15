'use client';

import { useCallback, useEffect, useState } from 'react';

import { ApiError, api } from '@/lib/api';
import type { ActionInput, ActionPriority, ActionStatus, LeadershipAction } from '@/lib/types';
import { Empty, ErrorNote, Panel, Spinner, classNames } from '@/components/ui';

const PRIORITIES: ActionPriority[] = ['High', 'Medium', 'Low'];
const STATUSES: ActionStatus[] = ['Open', 'In Progress', 'Closed'];

const EMPTY: ActionInput = {
  action_required: '', owner: '', target_date: null,
  priority: 'Medium', status: 'Open', circle: '', node_id: '', risk_area: '', remarks: '',
};

function EscalationPill({ action }: { action: LeadershipAction }) {
  if (action.overdue_days <= 0) {
    return <span className="pill bg-rule/40 text-muted">on time</span>;
  }
  const tone =
    action.escalation === 'Critical' ? 'bg-risk/15 text-risk'
      : action.escalation === 'High' ? 'bg-warn/15 text-warn'
        : 'bg-rule/40 text-muted';
  return (
    <span className={classNames('pill font-mono', tone)}>
      {action.overdue_days}d over
    </span>
  );
}

/** Leadership action tracker.
 *
 *  Ordered by real priority. The legacy list sorted the text column descending,
 *  which put High last, below Medium and Low (audit H8).
 */
export function ActionsPanel({
  projectId,
  canWrite,
}: {
  projectId: number;
  canWrite: boolean;
}) {
  const [actions, setActions] = useState<LeadershipAction[]>([]);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<number | 'new' | null>(null);
  const [draft, setDraft] = useState<ActionInput>(EMPTY);

  const load = useCallback(async () => {
    setBusy(true);
    try {
      setActions(await api.actions(projectId));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load actions');
    } finally { setBusy(false); }
  }, [projectId]);

  useEffect(() => { void load(); }, [load]);

  function startEdit(action: LeadershipAction) {
    setEditing(action.id);
    setDraft({
      action_required: action.action_required,
      owner: action.owner ?? '',
      target_date: action.target_date,
      priority: action.priority,
      status: action.status,
      circle: action.circle ?? '',
      node_id: action.node_id ?? '',
      risk_area: action.risk_area ?? '',
      remarks: action.remarks ?? '',
    });
  }

  async function save(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    const body: ActionInput = {
      ...draft,
      owner: draft.owner || null,
      circle: draft.circle || null,
      node_id: draft.node_id || null,
      risk_area: draft.risk_area || null,
      remarks: draft.remarks || null,
      target_date: draft.target_date || null,
    };
    try {
      if (editing === 'new') await api.createAction(projectId, body);
      else if (typeof editing === 'number') await api.updateAction(editing, body);
      setEditing(null); setDraft(EMPTY);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save the action');
    }
  }

  async function remove(action: LeadershipAction) {
    setError(null);
    try { await api.deleteAction(action.id); await load(); }
    catch (err) { setError(err instanceof Error ? err.message : 'Could not delete'); }
  }

  const open = actions.filter((a) => a.status !== 'Closed').length;
  const overdue = actions.filter((a) => a.overdue_days > 0).length;

  return (
    <Panel
      title="Leadership actions"
      subtitle={
        actions.length
          ? `${open} open · ${overdue} overdue · highest priority first`
          : 'Escalations tracked against this project'
      }
      bleed
      actions={
        canWrite ? (
          <button type="button" className="btn-ghost !py-1 !text-xs"
            onClick={() => { setEditing(editing === 'new' ? null : 'new'); setDraft(EMPTY); }}>
            {editing === 'new' ? 'Cancel' : 'Add action'}
          </button>
        ) : null
      }
    >
      {error ? <div className="px-4 py-3"><ErrorNote message={error} /></div> : null}

      {editing !== null ? (
        <form onSubmit={save} className="space-y-2 border-b border-rule bg-raised px-4 py-3 text-xs">
          <div>
            <label htmlFor="act-text" className="label mb-1 block">Action required</label>
            <textarea id="act-text" required rows={2} className="field !text-xs"
              value={draft.action_required}
              onChange={(e) => setDraft({ ...draft, action_required: e.target.value })} />
          </div>
          <div className="grid gap-2 sm:grid-cols-3 lg:grid-cols-6">
            <div>
              <label htmlFor="act-owner" className="label mb-1 block">Owner</label>
              <input id="act-owner" className="field !py-1 !text-xs" value={draft.owner ?? ''}
                onChange={(e) => setDraft({ ...draft, owner: e.target.value })} />
            </div>
            <div>
              <label htmlFor="act-target" className="label mb-1 block">Target</label>
              <input id="act-target" type="date" className="field !py-1 !text-xs"
                value={draft.target_date ?? ''}
                onChange={(e) => setDraft({ ...draft, target_date: e.target.value || null })} />
            </div>
            <div>
              <label htmlFor="act-priority" className="label mb-1 block">Priority</label>
              <select id="act-priority" className="field !py-1 !text-xs" value={draft.priority}
                onChange={(e) => setDraft({ ...draft, priority: e.target.value as ActionPriority })}>
                {PRIORITIES.map((p) => <option key={p} value={p}>{p}</option>)}
              </select>
            </div>
            <div>
              <label htmlFor="act-status" className="label mb-1 block">Status</label>
              <select id="act-status" className="field !py-1 !text-xs" value={draft.status}
                onChange={(e) => setDraft({ ...draft, status: e.target.value as ActionStatus })}>
                {STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
              </select>
            </div>
            <div>
              <label htmlFor="act-circle" className="label mb-1 block">Circle</label>
              <input id="act-circle" className="field !py-1 !text-xs" value={draft.circle ?? ''}
                onChange={(e) => setDraft({ ...draft, circle: e.target.value })} />
            </div>
            <div>
              <label htmlFor="act-risk" className="label mb-1 block">Risk area</label>
              <input id="act-risk" className="field !py-1 !text-xs" value={draft.risk_area ?? ''}
                onChange={(e) => setDraft({ ...draft, risk_area: e.target.value })} />
            </div>
          </div>
          <div className="flex gap-2 pt-1">
            <button type="submit" className="btn-primary !py-1 !text-xs">
              {editing === 'new' ? 'Create' : 'Save'}
            </button>
            <button type="button" className="btn-ghost !py-1 !text-xs"
              onClick={() => { setEditing(null); setDraft(EMPTY); }}>Cancel</button>
          </div>
        </form>
      ) : null}

      {busy ? <Spinner label="Loading actions" /> : actions.length === 0 ? (
        <Empty message="No escalations recorded." />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[760px] text-xs">
            <thead className="sticky-head">
              <tr className="border-b border-rule">
                <th className="px-3 py-2 text-left font-medium text-muted">Priority</th>
                <th className="px-3 py-2 text-left font-medium text-muted">Action</th>
                <th className="px-3 py-2 text-left font-medium text-muted">Owner</th>
                <th className="px-3 py-2 text-left font-medium text-muted">Target</th>
                <th className="px-3 py-2 text-left font-medium text-muted">Status</th>
                <th className="px-3 py-2 text-left font-medium text-muted">Overdue</th>
                <th className="px-3 py-2" />
              </tr>
            </thead>
            <tbody>
              {actions.map((action) => (
                <tr key={action.id} className="border-b border-rule/60 last:border-b-0">
                  <td className="px-3 py-1.5">
                    <span className={classNames('pill',
                      action.priority === 'High' ? 'bg-risk/15 text-risk'
                        : action.priority === 'Medium' ? 'bg-warn/15 text-warn'
                          : 'bg-rule/40 text-muted')}>
                      {action.priority}
                    </span>
                  </td>
                  <td className="max-w-[22rem] px-3 py-1.5">
                    {action.action_required}
                    {action.circle || action.risk_area ? (
                      <span className="ml-2 text-faint">
                        {[action.circle, action.risk_area].filter(Boolean).join(' · ')}
                      </span>
                    ) : null}
                  </td>
                  <td className="px-3 py-1.5 text-muted">{action.owner ?? '—'}</td>
                  <td className="px-3 py-1.5 font-mono text-muted">{action.target_date ?? '—'}</td>
                  <td className="px-3 py-1.5">{action.status}</td>
                  <td className="px-3 py-1.5"><EscalationPill action={action} /></td>
                  <td className="whitespace-nowrap px-3 py-1.5 text-right">
                    {canWrite ? (
                      <>
                        <button type="button" className="text-xs text-accent hover:underline"
                          onClick={() => startEdit(action)}>Edit</button>
                        <button type="button" className="ml-3 text-xs text-risk hover:underline"
                          onClick={() => void remove(action)}>Delete</button>
                      </>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}
