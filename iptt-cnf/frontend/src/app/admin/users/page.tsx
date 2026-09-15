'use client';

import { useCallback, useEffect, useState } from 'react';

import { useRequireSession, useSession } from '@/components/session';
import { Shell } from '@/components/shell';
import { Empty, ErrorNote, Panel, Spinner, classNames } from '@/components/ui';
import { ApiError, api } from '@/lib/api';
import type { AdminUser, Project, Role } from '@/lib/types';

const ROLES: Role[] = ['admin', 'pm', 'viewer'];

export default function UsersPage() {
  const { session, loading } = useRequireSession();
  const { can } = useSession();
  const isAdmin = can('admin');

  const [users, setUsers] = useState<AdminUser[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState({ username: '', password: '', role: 'viewer' as Role });
  const [editingAssignments, setEditingAssignments] = useState<number | null>(null);

  const load = useCallback(async () => {
    setBusy(true);
    try {
      const [u, p] = await Promise.all([api.users(), api.projects()]);
      setUsers(u); setProjects(p); setError(null);
    } catch (err) {
      setError(err instanceof ApiError && err.isForbidden
        ? 'Administrator access required.'
        : err instanceof Error ? err.message : 'Could not load users');
    } finally { setBusy(false); }
  }, []);

  useEffect(() => { if (session) void load(); }, [session, load]);

  async function act<T>(fn: () => Promise<T>, after?: (value: T) => void) {
    setError(null); setNotice(null);
    try { const value = await fn(); after?.(value); await load(); }
    catch (err) { setError(err instanceof ApiError ? err.message : 'That did not work'); }
  }

  if (loading || !session) return <Shell><Spinner label="Checking your session" /></Shell>;
  if (!isAdmin) {
    return <Shell><ErrorNote message="Administrator access required." /></Shell>;
  }

  const needingRotation = users.filter((u) => u.must_change_password).length;

  return (
    <Shell>
      <div className="mb-5">
        <h1 className="text-xl font-semibold">Users</h1>
        <p className="mt-0.5 text-sm text-muted">
          {users.length} account{users.length === 1 ? '' : 's'}
          {needingRotation ? ` · ${needingRotation} still on a migrated default password` : ''}
        </p>
      </div>

      {error ? <ErrorNote message={error} onRetry={() => void load()} /> : null}
      {notice ? (
        <div className="mb-4 rounded border border-accent/40 bg-accent-soft px-3 py-2 text-sm text-accent-ink">
          {notice}
        </div>
      ) : null}

      {busy ? <Spinner label="Loading users" /> : (
        <Panel
          title="Accounts"
          subtitle="Deactivating keeps the audit trail; deleting would not"
          bleed
          actions={
            <button type="button" className="btn-ghost !py-1 !text-xs"
              onClick={() => setAdding((v) => !v)}>
              {adding ? 'Cancel' : 'Add user'}
            </button>
          }
        >
          {adding ? (
            <form
              className="grid gap-2 border-b border-rule bg-raised px-4 py-3 sm:grid-cols-4"
              onSubmit={(e) => {
                e.preventDefault();
                void act(
                  () => api.createUser(draft.username, draft.password, draft.role),
                  () => { setAdding(false); setDraft({ username: '', password: '', role: 'viewer' }); },
                );
              }}
            >
              <div>
                <label htmlFor="u-name" className="label mb-1 block">Username</label>
                <input id="u-name" required minLength={3} className="field !py-1 !text-xs"
                  value={draft.username}
                  onChange={(e) => setDraft({ ...draft, username: e.target.value })} />
              </div>
              <div>
                <label htmlFor="u-pass" className="label mb-1 block">Initial password</label>
                <input id="u-pass" type="text" required minLength={12} className="field !py-1 !text-xs"
                  value={draft.password}
                  onChange={(e) => setDraft({ ...draft, password: e.target.value })} />
              </div>
              <div>
                <label htmlFor="u-role" className="label mb-1 block">Role</label>
                <select id="u-role" className="field !py-1 !text-xs" value={draft.role}
                  onChange={(e) => setDraft({ ...draft, role: e.target.value as Role })}>
                  {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
                </select>
              </div>
              <div className="flex items-end">
                <button type="submit" className="btn-primary !py-1 !text-xs">Create</button>
              </div>
              <p className="text-2xs text-muted sm:col-span-4">
                At least 12 characters with upper and lower case, a digit and a symbol.
                The user must change it at first sign-in.
              </p>
            </form>
          ) : null}

          <div className="overflow-x-auto">
            <table className="w-full min-w-[820px] text-xs">
              <thead className="sticky-head">
                <tr className="border-b border-rule">
                  {['User', 'Role', 'State', 'Last sign-in', 'Assigned projects', ''].map((h) => (
                    <th key={h} className="px-3 py-2 text-left font-medium text-muted">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {users.map((user) => (
                  <tr key={user.id} className="border-b border-rule/60 last:border-b-0 align-top">
                    <td className="px-3 py-2 font-mono">
                      {user.username}
                      {user.id === session.id ? (
                        <span className="ml-2 text-faint">(you)</span>
                      ) : null}
                    </td>
                    <td className="px-3 py-2">
                      <select
                        aria-label={`Role for ${user.username}`}
                        className="field w-24 !py-0.5 !text-xs" value={user.role}
                        onChange={(e) => void act(() => api.setUserRole(user.id, e.target.value as Role))}
                      >
                        {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
                      </select>
                    </td>
                    <td className="space-x-1 px-3 py-2">
                      <span className={classNames('pill',
                        user.is_active ? 'bg-ok/15 text-ok' : 'bg-rule/40 text-muted')}>
                        {user.is_active ? 'active' : 'inactive'}
                      </span>
                      {user.is_locked ? (
                        <span className="pill bg-risk/15 text-risk">locked</span>
                      ) : null}
                      {user.must_change_password ? (
                        <span className="pill bg-warn/15 text-warn">must rotate</span>
                      ) : null}
                    </td>
                    <td className="px-3 py-2 font-mono text-muted">
                      {user.last_login_at ? user.last_login_at.slice(0, 16).replace('T', ' ') : 'never'}
                    </td>
                    <td className="px-3 py-2">
                      {editingAssignments === user.id ? (
                        <div className="space-y-1">
                          {projects.map((project) => (
                            <label key={project.id} className="flex items-center gap-1.5">
                              <input
                                type="checkbox"
                                defaultChecked={user.assigned_project_ids.includes(project.id)}
                                onChange={(e) => {
                                  const next = e.target.checked
                                    ? [...user.assigned_project_ids, project.id]
                                    : user.assigned_project_ids.filter((i) => i !== project.id);
                                  void act(() => api.setUserAssignments(user.id, next));
                                }}
                              />
                              <span className="truncate">{project.name}</span>
                            </label>
                          ))}
                          <button type="button" className="text-accent hover:underline"
                            onClick={() => setEditingAssignments(null)}>done</button>
                        </div>
                      ) : (
                        <button type="button" className="text-left hover:text-accent"
                          onClick={() => setEditingAssignments(user.id)}>
                          {user.role === 'admin' ? (
                            <span className="text-faint">all projects</span>
                          ) : user.assigned_project_ids.length ? (
                            <span>{user.assigned_project_ids.length} project
                              {user.assigned_project_ids.length === 1 ? '' : 's'}</span>
                          ) : (
                            <span className="text-faint">none — cannot write</span>
                          )}
                        </button>
                      )}
                    </td>
                    <td className="whitespace-nowrap px-3 py-2 text-right">
                      <button type="button" className="text-accent hover:underline"
                        onClick={() => void act(
                          () => api.resetUserPassword(user.id),
                          (r) => setNotice(
                            `Temporary password for ${r.username}: ${r.temporary_password} — shown once, pass it on securely.`,
                          ),
                        )}>Reset password</button>
                      <button type="button"
                        className={classNames('ml-3 hover:underline',
                          user.is_active ? 'text-risk' : 'text-ok')}
                        onClick={() => void act(() => api.setUserActive(user.id, !user.is_active))}>
                        {user.is_active ? 'Deactivate' : 'Reactivate'}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {users.length === 0 ? <Empty message="No accounts." /> : null}
          </div>
        </Panel>
      )}
    </Shell>
  );
}
