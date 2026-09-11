'use client';

import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';

import { useRequireSession } from '@/components/session';
import { Shell } from '@/components/shell';
import { Empty, ErrorNote, Panel, Spinner } from '@/components/ui';
import { api } from '@/lib/api';
import type { Project } from '@/lib/types';

/** Landing page for the Execution tab: pick a baselined project to work in. */
export default function ExecutionIndex() {
  const { session, loading } = useRequireSession();
  const [projects, setProjects] = useState<Project[]>([]);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      setProjects(await api.projects());
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load projects');
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    if (session) void load();
  }, [session, load]);

  if (loading || !session) {
    return <Shell><Spinner label="Checking your session" /></Shell>;
  }

  const ready = projects.filter((p) => p.node_count > 0 && p.task_count > 0);

  return (
    <Shell>
      <h1 className="mb-1 text-xl font-semibold">Execution</h1>
      <p className="mb-5 text-sm text-muted">Choose a project to record progress against.</p>

      {error ? <ErrorNote message={error} onRetry={() => void load()} /> : null}
      {busy ? <Spinner label="Loading" /> : null}

      {!busy && !error ? (
        <Panel title="Projects with scope loaded" subtitle={`${ready.length} available`}>
          {ready.length === 0 ? (
            <Empty message="No project has scope and a task template loaded yet." />
          ) : (
            <ul className="divide-y divide-rule">
              {ready.map((project) => (
                <li key={project.id}>
                  <Link
                    href={`/projects/${project.id}/execution`}
                    className="flex flex-wrap items-center gap-x-4 gap-y-1 py-2.5 hover:text-accent"
                  >
                    <span className="text-sm font-medium">{project.name}</span>
                    <span className="text-xs text-muted">{project.programme_name}</span>
                    <span className="ml-auto font-mono text-xs text-muted">
                      {project.node_count} nodes
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      ) : null}
    </Shell>
  );
}
