'use client';

import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useCallback, useEffect, useMemo, useState } from 'react';

import { useRequireSession, useSession } from '@/components/session';
import { Shell } from '@/components/shell';
import { SheetImport } from '@/components/sheet-import';
import { Empty, ErrorNote, Panel, Spinner, classNames } from '@/components/ui';
import { ApiError, api } from '@/lib/api';
import type { Project, ScopeRow } from '@/lib/types';

export default function ScopePage() {
  const { id } = useParams<{ id: string }>();
  const projectId = Number(id);
  const { session, loading } = useRequireSession();
  const { can } = useSession();
  const isAdmin = can('admin');

  const [project, setProject] = useState<Project | null>(null);
  const [rows, setRows] = useState<ScopeRow[]>([]);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState('');
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState({
    node_id: '', circle: '', facility_name: '', num_servers: 1, priority: 1,
  });

  const load = useCallback(async () => {
    setBusy(true); setError(null);
    try {
      const [p, s] = await Promise.all([api.project(projectId), api.scope(projectId)]);
      setProject(p); setRows(s);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load the scope');
    } finally { setBusy(false); }
  }, [projectId]);

  useEffect(() => { if (session) void load(); }, [session, load]);

  const visible = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    if (!needle) return rows;
    return rows.filter((r) =>
      [r.node_id, r.circle, r.facility_name].some((v) => v.toLowerCase().includes(needle)));
  }, [rows, filter]);

  async function addNode(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      await api.addScope(projectId, draft);
      setDraft({ node_id: '', circle: '', facility_name: '', num_servers: 1, priority: 1 });
      setAdding(false);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not add the node');
    }
  }

  async function remove(row: ScopeRow) {
    setError(null);
    try {
      await api.deleteScope(projectId, row.id);
      await load();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setError(`${err.message} (use the execution grid to review that history first)`);
      } else {
        setError(err instanceof Error ? err.message : 'Could not remove the node');
      }
    }
  }

  if (loading || !session) return <Shell><Spinner label="Checking your session" /></Shell>;

  return (
    <Shell>
      <div className="mb-5">
        <Link href={`/projects/${projectId}`} className="label hover:text-accent">
          ← {project?.name ?? 'Project'}
        </Link>
        <h1 className="mt-1 text-xl font-semibold">Scope</h1>
        <p className="mt-0.5 text-sm text-muted">
          The network nodes this project deploys. Each node gets its own copy of the
          task template.
        </p>
      </div>

      {error ? <ErrorNote message={error} onRetry={() => void load()} /> : null}

      <SheetImport
        label="Scope sheet"
        canWrite={isAdmin}
        onDownload={() => api.downloadScopeTemplate(projectId)}
        onUpload={(file, dryRun, replace) => api.importScope(projectId, file, dryRun, replace)}
        onApplied={() => void load()}
        extraToggle={{
          label: 'Replace mode',
          hint: 'Also remove nodes missing from the sheet. Nodes with recorded dates are never removed silently.',
        }}
      />

      {busy ? <Spinner label="Loading nodes" /> : (
        <Panel
          title="Nodes"
          subtitle={`${visible.length} of ${rows.length}`}
          bleed
          actions={
            <div className="flex flex-wrap items-center gap-2">
              <input
                type="search" placeholder="Filter…" aria-label="Filter nodes"
                className="field w-40 !py-1 !text-xs"
                value={filter} onChange={(e) => setFilter(e.target.value)}
              />
              {isAdmin ? (
                <button type="button" className="btn-ghost !py-1 !text-xs"
                  onClick={() => setAdding((v) => !v)}>
                  {adding ? 'Cancel' : 'Add node'}
                </button>
              ) : null}
            </div>
          }
        >
          {adding ? (
            <form onSubmit={addNode}
              className="grid gap-2 border-b border-rule bg-raised px-4 py-3 sm:grid-cols-6">
              {([
                ['node_id', 'Node ID', 'text'],
                ['circle', 'Circle', 'text'],
                ['facility_name', 'Facility', 'text'],
                ['num_servers', 'Servers', 'number'],
                ['priority', 'Priority', 'number'],
              ] as const).map(([key, label, type]) => (
                <div key={key}>
                  <label htmlFor={`new-${key}`} className="label mb-1 block">{label}</label>
                  <input id={`new-${key}`} type={type} required className="field !py-1 !text-xs"
                    value={String(draft[key])}
                    onChange={(e) => setDraft({
                      ...draft,
                      [key]: type === 'number' ? Number(e.target.value) : e.target.value,
                    })} />
                </div>
              ))}
              <div className="flex items-end">
                <button type="submit" className="btn-primary !py-1 !text-xs">Add</button>
              </div>
            </form>
          ) : null}

          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px] text-xs">
              <thead className="sticky-head">
                <tr className="border-b border-rule">
                  {['Node', 'Circle', 'Facility', 'Servers', 'Priority', 'Activities', 'Recorded', '']
                    .map((h) => (
                      <th key={h} className={classNames(
                        'px-3 py-2 font-medium text-muted',
                        ['Servers', 'Priority', 'Activities'].includes(h) ? 'text-right' : 'text-left',
                      )}>{h}</th>
                    ))}
                </tr>
              </thead>
              <tbody>
                {visible.map((row) => (
                  <tr key={row.id} className="border-b border-rule/60 last:border-b-0">
                    <td className="px-3 py-1.5 font-mono">{row.node_id}</td>
                    <td className="px-3 py-1.5 font-mono text-muted">{row.circle}</td>
                    <td className="max-w-[18rem] truncate px-3 py-1.5">{row.facility_name}</td>
                    <td className="px-3 py-1.5 text-right font-mono">{row.num_servers}</td>
                    <td className="px-3 py-1.5 text-right font-mono text-muted">{row.priority}</td>
                    <td className="px-3 py-1.5 text-right font-mono text-muted">{row.task_count}</td>
                    <td className="px-3 py-1.5">
                      {row.has_execution_data
                        ? <span className="pill bg-accent/15 text-accent">has history</span>
                        : <span className="text-faint">—</span>}
                    </td>
                    <td className="px-3 py-1.5 text-right">
                      {isAdmin ? (
                        <button type="button" className="text-xs text-risk hover:underline"
                          onClick={() => void remove(row)}>Remove</button>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {visible.length === 0 ? <Empty message="No nodes match." /> : null}
          </div>
        </Panel>
      )}
    </Shell>
  );
}
