'use client';

import Link from 'next/link';
import { useCallback, useEffect, useMemo, useState } from 'react';

import { useRequireSession } from '@/components/session';
import { Shell } from '@/components/shell';
import { Empty, ErrorNote, Panel, Spinner, classNames } from '@/components/ui';
import { api } from '@/lib/api';
import type { Programme, Project } from '@/lib/types';

/** Portfolio home. Preserves the legacy pipeline buckets - Ongoing, Setup,
 *  Completed - which is how the deployment teams navigate. */

type Bucket = 'ongoing' | 'setup' | 'completed';

function bucketOf(project: Project): Bucket {
  if (project.status === 'Completed') return 'completed';
  // "Setup" means the project is not yet ready to execute: no kickoff date, no
  // scope, or no task template loaded.
  if (!project.project_start_date || project.node_count === 0 || project.task_count === 0) {
    return 'setup';
  }
  return 'ongoing';
}

const BUCKET_COPY: Record<Bucket, { title: string; blurb: string }> = {
  ongoing: { title: 'Ongoing', blurb: 'Baselined and in execution' },
  setup: { title: 'Setup', blurb: 'Missing a start date, scope or task template' },
  completed: { title: 'Completed', blurb: 'Closed out' },
};

function ProjectCard({ project }: { project: Project }) {
  const bucket = bucketOf(project);
  const missing: string[] = [];
  if (!project.project_start_date) missing.push('start date');
  if (project.node_count === 0) missing.push('scope');
  if (project.task_count === 0) missing.push('tasks');

  return (
    <Link
      href={`/projects/${project.id}`}
      className="card block p-4 transition-colors hover:border-accent focus-visible:border-accent"
    >
      <div className="label">{project.programme_name}</div>
      <h3 className="mt-1 text-sm font-semibold leading-snug">{project.name}</h3>

      <dl className="mt-3 grid grid-cols-3 gap-2 border-t border-rule pt-3 text-xs">
        <div>
          <dt className="text-faint">Nodes</dt>
          <dd className="font-mono text-sm text-ink">{project.node_count}</dd>
        </div>
        <div>
          <dt className="text-faint">Activities</dt>
          <dd className="font-mono text-sm text-ink">{project.task_count.toLocaleString()}</dd>
        </div>
        <div>
          <dt className="text-faint">Kickoff</dt>
          <dd className="font-mono text-sm text-ink">
            {project.project_start_date ?? '—'}
          </dd>
        </div>
      </dl>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        {project.baseline_locked ? (
          <span className="pill bg-accent/15 text-accent">
            baseline v{project.baseline_version}
          </span>
        ) : (
          <span className="pill bg-rule/40 text-muted">not baselined</span>
        )}
        {bucket === 'setup' && missing.length > 0 ? (
          <span className="pill bg-warn/15 text-warn">needs {missing.join(', ')}</span>
        ) : null}
      </div>
    </Link>
  );
}

export default function PortfolioPage() {
  const { session, loading } = useRequireSession();
  const [programmes, setProgrammes] = useState<Programme[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [programmeFilter, setProgrammeFilter] = useState<number | 'all'>('all');
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const [p, pr] = await Promise.all([api.programmes(), api.projects()]);
      setProgrammes(p);
      setProjects(pr);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load the portfolio');
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    if (session) void load();
  }, [session, load]);

  const visible = useMemo(
    () =>
      programmeFilter === 'all'
        ? projects
        : projects.filter((p) => p.programme_id === programmeFilter),
    [projects, programmeFilter],
  );

  const buckets = useMemo(() => {
    const grouped: Record<Bucket, Project[]> = { ongoing: [], setup: [], completed: [] };
    for (const project of visible) grouped[bucketOf(project)].push(project);
    return grouped;
  }, [visible]);

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
        <div>
          <h1 className="text-xl font-semibold">Portfolio</h1>
          <p className="mt-0.5 text-sm text-muted">
            {projects.length} project{projects.length === 1 ? '' : 's'} across{' '}
            {programmes.length} programme{programmes.length === 1 ? '' : 's'}
          </p>
        </div>

        <div className="flex items-center gap-2">
          <label htmlFor="programme" className="label">
            Programme
          </label>
          <select
            id="programme"
            className="field w-auto"
            value={programmeFilter}
            onChange={(e) =>
              setProgrammeFilter(e.target.value === 'all' ? 'all' : Number(e.target.value))
            }
          >
            <option value="all">All programmes</option>
            {programmes.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name} ({p.project_count})
              </option>
            ))}
          </select>
          {programmeFilter !== 'all' ? (
            <Link href={`/programmes/${programmeFilter}`} className="btn-ghost !py-1 !text-xs">
              Programme view
            </Link>
          ) : null}
        </div>
      </div>

      {error ? <ErrorNote message={error} onRetry={() => void load()} /> : null}
      {busy ? <Spinner label="Loading portfolio" /> : null}

      {!busy && !error ? (
        <div className="space-y-5">
          {(['ongoing', 'setup', 'completed'] as Bucket[]).map((bucket) => (
            <Panel
              key={bucket}
              title={BUCKET_COPY[bucket].title}
              subtitle={BUCKET_COPY[bucket].blurb}
              actions={
                <span className="font-mono text-xs text-muted">
                  {buckets[bucket].length}
                </span>
              }
            >
              {buckets[bucket].length === 0 ? (
                <Empty message="Nothing here." />
              ) : (
                <div
                  className={classNames(
                    'grid gap-3',
                    'sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4',
                  )}
                >
                  {buckets[bucket].map((project) => (
                    <ProjectCard key={project.id} project={project} />
                  ))}
                </div>
              )}
            </Panel>
          ))}
        </div>
      ) : null}
    </Shell>
  );
}
