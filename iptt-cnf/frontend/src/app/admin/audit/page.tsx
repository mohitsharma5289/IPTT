'use client';

import { useCallback, useEffect, useState } from 'react';

import { useRequireSession, useSession } from '@/components/session';
import { Shell } from '@/components/shell';
import { Empty, ErrorNote, Panel, Spinner } from '@/components/ui';
import { ApiError, api } from '@/lib/api';
import type { AuditEntry } from '@/lib/types';

const PAGE = 100;

export default function AuditPage() {
  const { session, loading } = useRequireSession();
  const { can } = useSession();
  const isAdmin = can('admin');

  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [cursor, setCursor] = useState<number | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actions, setActions] = useState<string[]>([]);
  const [filters, setFilters] = useState({ action: '', source: '', actor: '' });

  const load = useCallback(
    async (append = false) => {
      setBusy(true);
      try {
        const page = await api.audit({
          ...Object.fromEntries(
            Object.entries(filters).filter(([, v]) => v !== ''),
          ),
          limit: PAGE,
          ...(append && cursor ? { cursor } : {}),
        });
        setEntries((current) => (append ? [...current, ...page.entries] : page.entries));
        setCursor(page.next_cursor);
        setHasMore(page.has_more);
        setError(null);
      } catch (err) {
        setError(err instanceof ApiError && err.isForbidden
          ? 'Administrator access required.'
          : err instanceof Error ? err.message : 'Could not load the audit log');
      } finally { setBusy(false); }
    },
    [filters, cursor],
  );

  useEffect(() => {
    if (!session || !isAdmin) return;
    void api.auditActions().then(setActions).catch(() => setActions([]));
  }, [session, isAdmin]);

  useEffect(() => {
    if (!session || !isAdmin) return;
    setCursor(null);
    void load(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session, isAdmin, filters]);

  if (loading || !session) return <Shell><Spinner label="Checking your session" /></Shell>;
  if (!isAdmin) return <Shell><ErrorNote message="Administrator access required." /></Shell>;

  return (
    <Shell>
      <div className="mb-5">
        <h1 className="text-xl font-semibold">Audit log</h1>
        <p className="mt-0.5 text-sm text-muted">
          Every change, with who made it and where it came from. Fully paginated —
          nothing is out of reach.
        </p>
      </div>

      {error ? <ErrorNote message={error} onRetry={() => void load(false)} /> : null}

      <Panel
        title="Entries"
        subtitle={`${entries.length} shown${hasMore ? ', more available' : ''}`}
        bleed
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <select aria-label="Filter by action" className="field w-auto !py-1 !text-xs"
              value={filters.action}
              onChange={(e) => setFilters({ ...filters, action: e.target.value })}>
              <option value="">All actions</option>
              {actions.map((a) => <option key={a} value={a}>{a}</option>)}
            </select>
            <select aria-label="Filter by source" className="field w-auto !py-1 !text-xs"
              value={filters.source}
              onChange={(e) => setFilters({ ...filters, source: e.target.value })}>
              <option value="">All sources</option>
              <option value="api">api</option>
              <option value="excel-import">excel-import</option>
              <option value="legacy-import">legacy-import</option>
            </select>
            <input type="search" placeholder="Actor…" aria-label="Filter by actor"
              className="field w-28 !py-1 !text-xs" value={filters.actor}
              onChange={(e) => setFilters({ ...filters, actor: e.target.value })} />
          </div>
        }
      >
        <div className="overflow-x-auto">
          <table className="w-full min-w-[900px] text-xs">
            <thead className="sticky-head">
              <tr className="border-b border-rule">
                {['When', 'Who', 'Action', 'Source', 'Node', 'Activity', 'Field', 'From', 'To']
                  .map((h) => (
                    <th key={h} className="px-2.5 py-2 text-left font-medium text-muted">{h}</th>
                  ))}
              </tr>
            </thead>
            <tbody>
              {entries.map((entry) => (
                <tr key={entry.id} className="border-b border-rule/60 last:border-b-0">
                  <td className="whitespace-nowrap px-2.5 py-1.5 font-mono text-muted">
                    {entry.created_at.slice(0, 16).replace('T', ' ')}
                  </td>
                  <td className="px-2.5 py-1.5">
                    <span className="font-mono">{entry.actor_username}</span>
                    {entry.actor_role ? (
                      <span className="ml-1.5 text-faint">{entry.actor_role}</span>
                    ) : null}
                  </td>
                  <td className="px-2.5 py-1.5">{entry.action}</td>
                  <td className="px-2.5 py-1.5 font-mono text-faint">{entry.source}</td>
                  <td className="px-2.5 py-1.5 font-mono text-muted">{entry.node_id ?? '—'}</td>
                  <td className="max-w-[12rem] truncate px-2.5 py-1.5 text-muted">
                    {entry.task_name ?? '—'}
                  </td>
                  <td className="px-2.5 py-1.5 text-muted">{entry.field ?? '—'}</td>
                  <td className="max-w-[10rem] truncate px-2.5 py-1.5 font-mono text-muted">
                    {entry.old_value ?? '—'}
                  </td>
                  <td className="max-w-[10rem] truncate px-2.5 py-1.5 font-mono">
                    {entry.new_value ?? '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {busy && entries.length === 0 ? <Spinner label="Loading" /> : null}
          {!busy && entries.length === 0 ? <Empty message="No entries match." /> : null}
        </div>

        {hasMore ? (
          <div className="border-t border-rule px-4 py-2.5">
            <button type="button" className="btn-ghost !py-1 !text-xs"
              onClick={() => void load(true)} disabled={busy}>
              {busy ? 'Loading…' : 'Load more'}
            </button>
          </div>
        ) : null}
      </Panel>
    </Shell>
  );
}
