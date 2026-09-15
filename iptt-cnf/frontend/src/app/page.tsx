'use client';

import Link from 'next/link';
import { useCallback, useEffect, useMemo, useState } from 'react';

import { useRequireSession } from '@/components/session';
import { Shell } from '@/components/shell';
import {
  Dialog,
  Empty,
  ErrorNote,
  Field,
  Panel,
  Spinner,
  classNames,
} from '@/components/ui';
import { ApiError } from '@/lib/api';
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

const PROJECT_STATUSES = ['Not Started', 'In Progress', 'Completed', 'On Hold'];

/** Create a project.
 *
 *  This is the gap that made the rebuild unusable for anything new: the legacy
 *  app had `create_project_ui`, and until now neither this UI nor the API had
 *  any equivalent, so the tool could only ever show projects that arrived with
 *  the data migration.
 *
 *  A kickoff date is optional here. Supplying it plans nothing on its own - the
 *  project also needs scope and a task template, and the plan is generated
 *  automatically on whichever save completes that set.
 */
function NewProjectDialog({
  open,
  programmes,
  defaultProgrammeId,
  onClose,
  onCreated,
}: {
  open: boolean;
  programmes: Programme[];
  defaultProgrammeId?: number;
  onClose: () => void;
  onCreated: (project: Project) => void;
}) {
  const [name, setName] = useState('');
  const [programmeId, setProgrammeId] = useState<number | ''>(defaultProgrammeId ?? '');
  const [status, setStatus] = useState(PROJECT_STATUSES[0]);
  const [start, setStart] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setName('');
    setProgrammeId(defaultProgrammeId ?? (programmes[0]?.id ?? ''));
    setStatus(PROJECT_STATUSES[0]);
    setStart('');
    setError(null);
  }, [open, defaultProgrammeId, programmes]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (programmeId === '') {
      setError('Pick a programme');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const project = await api.createProject({
        programme_id: Number(programmeId),
        name: name.trim(),
        status,
        project_start_date: start || null,
      });
      onCreated(project);
      onClose();
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : 'Could not create the project',
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog
      open={open}
      title="New project"
      description="Scope and a task template come next; the plan generates itself once both exist."
      onClose={onClose}
    >
      <form onSubmit={submit} className="space-y-3">
        <Field label="Programme">
          <select
            className="field"
            value={programmeId}
            onChange={(e) => setProgrammeId(e.target.value ? Number(e.target.value) : '')}
            required
          >
            {programmes.length === 0 ? <option value="">No programmes yet</option> : null}
            {programmes.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </Field>

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

        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Status">
            <select className="field" value={status} onChange={(e) => setStatus(e.target.value)}>
              {PROJECT_STATUSES.map((s) => (
                <option key={s}>{s}</option>
              ))}
            </select>
          </Field>
          <Field label="Kickoff date" hint="Optional. Day 0 for the generated plan.">
            <input
              type="date"
              className="field"
              value={start}
              onChange={(e) => setStart(e.target.value)}
            />
          </Field>
        </div>

        {error ? <ErrorNote message={error} /> : null}

        <div className="flex justify-end gap-2 pt-1">
          <button type="button" className="btn-ghost" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button type="submit" className="btn" disabled={busy || programmes.length === 0}>
            {busy ? 'Creating…' : 'Create project'}
          </button>
        </div>
      </form>
    </Dialog>
  );
}

function NewProgrammeDialog({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (programme: Programme) => void;
}) {
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setName('');
      setError(null);
    }
  }, [open]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onCreated(await api.createProgramme({ name: name.trim(), status: 'Active' }));
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not create the programme');
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} title="New programme" onClose={onClose}>
      <form onSubmit={submit} className="space-y-3">
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
        {error ? <ErrorNote message={error} /> : null}
        <div className="flex justify-end gap-2 pt-1">
          <button type="button" className="btn-ghost" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button type="submit" className="btn" disabled={busy}>
            {busy ? 'Creating…' : 'Create programme'}
          </button>
        </div>
      </form>
    </Dialog>
  );
}

export default function PortfolioPage() {
  const { session, loading, can } = useRequireSession();
  const [programmes, setProgrammes] = useState<Programme[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [programmeFilter, setProgrammeFilter] = useState<number | 'all'>('all');
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [newProject, setNewProject] = useState(false);
  const [newProgramme, setNewProgramme] = useState(false);

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

        <div className="flex flex-wrap items-center gap-2">
          {can('admin') ? (
            <>
              <button type="button" className="btn" onClick={() => setNewProject(true)}>
                New project
              </button>
              <button
                type="button"
                className="btn-ghost"
                onClick={() => setNewProgramme(true)}
              >
                New programme
              </button>
            </>
          ) : null}
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

      <NewProjectDialog
        open={newProject}
        programmes={programmes}
        defaultProgrammeId={programmeFilter === 'all' ? undefined : programmeFilter}
        onClose={() => setNewProject(false)}
        onCreated={() => void load()}
      />
      <NewProgrammeDialog
        open={newProgramme}
        onClose={() => setNewProgramme(false)}
        onCreated={() => void load()}
      />

      {error ? <ErrorNote message={error} onRetry={() => void load()} /> : null}
      {busy ? <Spinner label="Loading portfolio" /> : null}

      {!busy && !error && projects.length === 0 ? (
        <Panel
          title="Nothing here yet"
          subtitle={
            programmes.length === 0
              ? 'Start by creating a programme, then a project inside it.'
              : 'Create a project to get going.'
          }
        >
          <p className="text-sm text-muted">
            A project needs a kickoff date, its scope of nodes, and a task template.
            Once all three are in place the plan is generated for you.
          </p>
        </Panel>
      ) : null}

      {!busy && !error && projects.length > 0 ? (
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
