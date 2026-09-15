'use client';

import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useCallback, useEffect, useState } from 'react';

import { useRequireSession, useSession } from '@/components/session';
import { Shell } from '@/components/shell';
import { SheetImport } from '@/components/sheet-import';
import { Empty, ErrorNote, Panel, Spinner } from '@/components/ui';
import { api } from '@/lib/api';
import type { Project, TemplateRow } from '@/lib/types';

export default function TemplatePage() {
  const { id } = useParams<{ id: string }>();
  const projectId = Number(id);
  const { session, loading } = useRequireSession();
  const { can } = useSession();
  const isAdmin = can('admin');

  const [project, setProject] = useState<Project | null>(null);
  const [rows, setRows] = useState<TemplateRow[]>([]);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setBusy(true); setError(null);
    try {
      const [p, t] = await Promise.all([api.project(projectId), api.template(projectId)]);
      setProject(p); setRows(t);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load the template');
    } finally { setBusy(false); }
  }, [projectId]);

  useEffect(() => { if (session) void load(); }, [session, load]);

  if (loading || !session) return <Shell><Spinner label="Checking your session" /></Shell>;

  const nodeCount = rows[0]?.node_count ?? 0;

  return (
    <Shell>
      <div className="mb-5">
        <Link href={`/projects/${projectId}`} className="label hover:text-accent">
          ← {project?.name ?? 'Project'}
        </Link>
        <h1 className="mt-1 text-xl font-semibold">Task template</h1>
        <p className="mt-0.5 text-sm text-muted">
          The ordered activities every node goes through. {rows.length} activities
          × {nodeCount} nodes = {(rows.length * nodeCount).toLocaleString()} tracked items.
        </p>
      </div>

      {error ? <ErrorNote message={error} onRetry={() => void load()} /> : null}

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

      {busy ? <Spinner label="Loading template" /> : (
        <Panel title="Activities" subtitle="In template order" bleed>
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
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.template_task_number} className="border-b border-rule/60 last:border-b-0">
                    <td className="px-3 py-1.5 text-right font-mono text-faint">
                      {row.template_task_number}
                    </td>
                    <td className="px-3 py-1.5">{row.name}</td>
                    <td className="px-3 py-1.5 text-right font-mono text-muted">
                      {row.duration_days} wd
                    </td>
                    <td className="px-3 py-1.5 text-right font-mono text-muted">
                      {row.predecessor_template_number ?? '—'}
                    </td>
                    <td className="px-3 py-1.5">
                      {row.is_prerequisite
                        ? <span className="pill bg-accent/15 text-accent">prerequisite</span>
                        : <span className="text-faint">—</span>}
                    </td>
                    <td className="px-3 py-1.5 text-muted">{row.owner_role ?? '—'}</td>
                    <td className="px-3 py-1.5 text-right font-mono text-muted">
                      {row.recorded_dates || '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {rows.length === 0 ? (
              <Empty message="No template loaded. Download the starter sheet above to begin." />
            ) : null}
          </div>
        </Panel>
      )}
    </Shell>
  );
}
