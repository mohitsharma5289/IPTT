'use client';

import { useRef, useState } from 'react';

import { ApiError, api } from '@/lib/api';
import type { ImportReport } from '@/lib/types';
import { ErrorNote, classNames } from '@/components/ui';

/** Download and upload controls for the execution workbook.
 *
 *  An upload is a dry run first, always. The user sees exactly which cells would
 *  change and has to press Apply. The legacy importer wrote immediately, without
 *  authentication, without an audit entry, and matched activity columns by
 *  position — so a reordered spreadsheet silently corrupted every node in the
 *  project (audit C3).
 */
export function WorkbookControls({
  projectId,
  circle,
  canWrite,
  onApplied,
}: {
  projectId: number;
  circle?: string;
  canWrite: boolean;
  onApplied: () => void;
}) {
  const fileInput = useRef<HTMLInputElement>(null);
  const [pending, setPending] = useState<File | null>(null);
  const [report, setReport] = useState<ImportReport | null>(null);
  const [busy, setBusy] = useState<'idle' | 'exporting' | 'checking' | 'applying'>('idle');
  const [error, setError] = useState<string | null>(null);

  function reset() {
    setPending(null);
    setReport(null);
    setError(null);
    if (fileInput.current) fileInput.current.value = '';
  }

  async function download() {
    setError(null);
    setBusy('exporting');
    try {
      await api.exportWorkbook(projectId, circle);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Download failed');
    } finally {
      setBusy('idle');
    }
  }

  async function check(file: File) {
    setError(null);
    setPending(file);
    setBusy('checking');
    try {
      setReport(await api.importWorkbook(projectId, file, true));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'The file could not be checked');
      setPending(null);
    } finally {
      setBusy('idle');
    }
  }

  async function apply() {
    if (!pending) return;
    setError(null);
    setBusy('applying');
    try {
      const result = await api.importWorkbook(projectId, pending, false);
      setReport(result);
      if (result.ok) {
        onApplied();
        setPending(null);
        if (fileInput.current) fileInput.current.value = '';
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'The import failed');
    } finally {
      setBusy('idle');
    }
  }

  const applied = report && !report.dry_run && report.ok;

  return (
    <section className="card mb-4 overflow-hidden">
      <header className="flex flex-wrap items-center gap-3 border-b border-rule px-4 py-2.5">
        <div className="mr-auto">
          <h2 className="text-sm font-semibold">Workbook</h2>
          <p className="mt-0.5 text-xs text-muted">
            {circle ? `Circle ${circle}` : 'All circles'} · activities are matched by
            number, so columns and rows can be reordered or removed
          </p>
        </div>

        <button
          type="button"
          className="btn-ghost !py-1 !text-xs"
          onClick={() => void download()}
          disabled={busy !== 'idle'}
        >
          {busy === 'exporting' ? 'Preparing…' : 'Download'}
        </button>

        {canWrite ? (
          <>
            <input
              ref={fileInput}
              id="workbook-upload"
              type="file"
              accept=".xlsx"
              className="sr-only"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) void check(file);
              }}
            />
            <label
              htmlFor="workbook-upload"
              className={classNames(
                'btn-ghost !py-1 !text-xs cursor-pointer',
                busy !== 'idle' && 'pointer-events-none opacity-50',
              )}
            >
              {busy === 'checking' ? 'Checking…' : 'Upload'}
            </label>
          </>
        ) : null}
      </header>

      {error ? (
        <div className="px-4 py-3">
          <ErrorNote message={error} />
        </div>
      ) : null}

      {report ? (
        <div className="px-4 py-3 text-xs">
          {/* --- outcome ------------------------------------------------- */}
          {applied ? (
            <p className="mb-3 rounded border border-ok/40 bg-ok/10 px-3 py-2 text-ok">
              Applied {report.change_count} change
              {report.change_count === 1 ? '' : 's'} across {report.nodes_matched} node
              {report.nodes_matched === 1 ? '' : 's'}. Every change is in the audit log.
            </p>
          ) : null}

          {!report.ok ? (
            <div className="mb-3 rounded border border-risk/40 bg-risk/10 px-3 py-2">
              <p className="font-semibold text-risk">
                Nothing was applied. {report.errors.length} problem
                {report.errors.length === 1 ? '' : 's'} found.
              </p>
              <ul className="mt-1.5 space-y-1 text-risk/90">
                {report.errors.slice(0, 12).map((message) => (
                  <li key={message}>• {message}</li>
                ))}
              </ul>
              {report.errors.length > 12 ? (
                <p className="mt-1 text-risk/80">
                  …and {report.errors.length - 12} more.
                </p>
              ) : null}
            </div>
          ) : null}

          {/* --- what was read ------------------------------------------- */}
          <dl className="mb-3 grid grid-cols-2 gap-x-6 gap-y-1 sm:grid-cols-4">
            {[
              ['Rows read', report.rows_read],
              ['Nodes matched', report.nodes_matched],
              ['Activities checked', report.activities_considered],
              ['Changes', report.change_count],
            ].map(([label, value]) => (
              <div key={String(label)} className="flex justify-between gap-2">
                <dt className="text-muted">{label}</dt>
                <dd className="font-mono">{value}</dd>
              </div>
            ))}
          </dl>

          {report.warnings.length > 0 ? (
            <ul className="mb-3 space-y-1 text-warn">
              {report.warnings.slice(0, 6).map((message) => (
                <li key={message}>• {message}</li>
              ))}
            </ul>
          ) : null}

          {/* --- the diff ------------------------------------------------- */}
          {report.ok && report.changes.length > 0 ? (
            <div className="mb-3 max-h-72 overflow-auto rounded border border-rule">
              <table className="w-full min-w-[560px]">
                <thead className="sticky top-0 bg-raised">
                  <tr>
                    <th className="px-2 py-1.5 text-left font-medium text-muted">Node</th>
                    <th className="px-2 py-1.5 text-right font-medium text-muted">#</th>
                    <th className="px-2 py-1.5 text-left font-medium text-muted">Activity</th>
                    <th className="px-2 py-1.5 text-left font-medium text-muted">Field</th>
                    <th className="px-2 py-1.5 text-left font-medium text-muted">From</th>
                    <th className="px-2 py-1.5 text-left font-medium text-muted">To</th>
                  </tr>
                </thead>
                <tbody>
                  {report.changes.map((change, index) => (
                    <tr
                      key={`${change.node_id}-${change.template_task_number}-${change.field}-${index}`}
                      className="border-t border-rule/60"
                    >
                      <td className="px-2 py-1 font-mono">{change.node_id}</td>
                      <td className="px-2 py-1 text-right font-mono text-faint">
                        {change.template_task_number}
                      </td>
                      <td className="max-w-[14rem] truncate px-2 py-1">{change.task_name}</td>
                      <td className="px-2 py-1 text-muted">
                        {change.field === 'actual_start' ? 'start' : 'finish'}
                      </td>
                      <td className="px-2 py-1 font-mono text-muted">
                        {change.old_value ?? '—'}
                      </td>
                      <td className="px-2 py-1 font-mono text-ink">
                        {change.new_value ?? '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {report.changes_truncated > 0 ? (
                <p className="border-t border-rule bg-raised px-2 py-1.5 text-muted">
                  …and {report.changes_truncated} more changes not listed.
                </p>
              ) : null}
            </div>
          ) : null}

          {report.ok && report.dry_run && report.change_count === 0 ? (
            <p className="mb-3 text-muted">
              This file matches what is already recorded. Nothing to apply.
            </p>
          ) : null}

          {/* --- actions -------------------------------------------------- */}
          <div className="flex flex-wrap gap-2">
            {report.ok && report.dry_run && report.change_count > 0 ? (
              <button
                type="button"
                className="btn-primary !py-1 !text-xs"
                onClick={() => void apply()}
                disabled={busy !== 'idle'}
              >
                {busy === 'applying'
                  ? 'Applying…'
                  : `Apply ${report.change_count} change${report.change_count === 1 ? '' : 's'}`}
              </button>
            ) : null}
            <button type="button" className="btn-ghost !py-1 !text-xs" onClick={reset}>
              {applied ? 'Done' : 'Cancel'}
            </button>
          </div>
        </div>
      ) : null}
    </section>
  );
}
