'use client';

import { useRef, useState } from 'react';

import { ApiError } from '@/lib/api';
import type { SheetImportSummary } from '@/lib/types';
import { ErrorNote, classNames } from '@/components/ui';

/** Shared upload control for the scope and template sheets.
 *
 *  Every import is a dry run first. The legacy equivalents deleted everything in
 *  the project and re-inserted, unauthenticated and with no preview.
 */
export function SheetImport({
  label,
  onDownload,
  onUpload,
  canWrite,
  onApplied,
  extraToggle,
}: {
  label: string;
  onDownload: () => Promise<void>;
  onUpload: (file: File, dryRun: boolean, extra: boolean) => Promise<SheetImportSummary>;
  canWrite: boolean;
  onApplied: () => void;
  extraToggle?: { label: string; hint: string };
}) {
  const input = useRef<HTMLInputElement>(null);
  const [pending, setPending] = useState<File | null>(null);
  const [summary, setSummary] = useState<SheetImportSummary | null>(null);
  const [extra, setExtra] = useState(false);
  const [busy, setBusy] = useState<'idle' | 'down' | 'check' | 'apply'>('idle');
  const [error, setError] = useState<string | null>(null);

  function reset() {
    setPending(null);
    setSummary(null);
    setError(null);
    if (input.current) input.current.value = '';
  }

  async function run(file: File, dryRun: boolean) {
    setError(null);
    setBusy(dryRun ? 'check' : 'apply');
    try {
      const result = await onUpload(file, dryRun, extra);
      setSummary(result);
      if (!dryRun && result.ok) {
        onApplied();
        reset();
        setSummary(result);
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'The file could not be processed');
      setPending(null);
    } finally {
      setBusy('idle');
    }
  }

  const count = (value: unknown) =>
    Array.isArray(value) ? value.length : typeof value === 'number' ? value : 0;
  const changes = summary
    ? count(summary.to_create) + count(summary.to_update) + count(summary.to_remove)
    : 0;

  return (
    <section className="card mb-4 overflow-hidden">
      <header className="flex flex-wrap items-center gap-3 border-b border-rule px-4 py-2.5">
        <h2 className="mr-auto text-sm font-semibold">{label}</h2>
        <button type="button" className="btn-ghost !py-1 !text-xs"
          onClick={() => { setBusy('down'); void onDownload().finally(() => setBusy('idle')); }}
          disabled={busy !== 'idle'}>
          {busy === 'down' ? 'Preparing…' : 'Download'}
        </button>
        {canWrite ? (
          <>
            <input ref={input} id={`sheet-${label}`} type="file" accept=".xlsx" className="sr-only"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) { setPending(file); void run(file, true); }
              }} />
            <label htmlFor={`sheet-${label}`}
              className={classNames('btn-ghost !py-1 !text-xs cursor-pointer',
                busy !== 'idle' && 'pointer-events-none opacity-50')}>
              {busy === 'check' ? 'Checking…' : 'Upload'}
            </label>
          </>
        ) : null}
      </header>

      {error ? <div className="px-4 py-3"><ErrorNote message={error} /></div> : null}

      {summary ? (
        <div className="space-y-3 px-4 py-3 text-xs">
          {!summary.ok ? (
            <div className="rounded border border-risk/40 bg-risk/10 px-3 py-2">
              <p className="font-semibold text-risk">
                Nothing was applied. {summary.errors.length} problem
                {summary.errors.length === 1 ? '' : 's'}.
              </p>
              <ul className="mt-1.5 space-y-1 text-risk/90">
                {summary.errors.slice(0, 10).map((m) => <li key={m}>• {m}</li>)}
              </ul>
            </div>
          ) : !summary.dry_run ? (
            <p className="rounded border border-ok/40 bg-ok/10 px-3 py-2 text-ok">
              Applied. {changes} change{changes === 1 ? '' : 's'} written and audited.
            </p>
          ) : null}

          {summary.ok ? (
            <dl className="grid grid-cols-2 gap-x-6 gap-y-1 sm:grid-cols-4">
              {[
                ['Rows read', summary.rows_read ?? summary.activities_in_sheet ?? 0],
                ['To create', count(summary.to_create)],
                ['To update', count(summary.to_update)],
                ['To remove', count(summary.to_remove)],
              ].map(([k, v]) => (
                <div key={String(k)} className="flex justify-between gap-2">
                  <dt className="text-muted">{k}</dt>
                  <dd className="font-mono">{v}</dd>
                </div>
              ))}
            </dl>
          ) : null}

          {summary.ok && summary.dry_run && (summary.removes?.length || count(summary.to_remove)) ? (
            <p className="text-warn">
              Will remove: {(summary.removes ?? (summary.to_remove as number[]) ?? [])
                .slice(0, 20).join(', ')}
            </p>
          ) : null}

          {extraToggle && summary.dry_run ? (
            <label className="flex items-start gap-2">
              <input type="checkbox" checked={extra} onChange={(e) => setExtra(e.target.checked)}
                className="mt-0.5" />
              <span>
                <span className="font-medium">{extraToggle.label}</span>
                <span className="block text-muted">{extraToggle.hint}</span>
              </span>
            </label>
          ) : null}

          <div className="flex flex-wrap gap-2">
            {summary.ok && summary.dry_run && pending ? (
              <button type="button" className="btn-primary !py-1 !text-xs"
                onClick={() => void run(pending, false)} disabled={busy !== 'idle'}>
                {busy === 'apply' ? 'Applying…' : changes ? `Apply ${changes} change${changes === 1 ? '' : 's'}` : 'Apply'}
              </button>
            ) : null}
            <button type="button" className="btn-ghost !py-1 !text-xs" onClick={reset}>
              {summary.ok && !summary.dry_run ? 'Done' : 'Cancel'}
            </button>
          </div>
        </div>
      ) : null}
    </section>
  );
}
