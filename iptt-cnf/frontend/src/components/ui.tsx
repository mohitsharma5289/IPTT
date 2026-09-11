'use client';

import type { ReactNode } from 'react';

/** Small shared primitives. Deliberately few: the legacy UI had 20 templates
 *  each re-inventing its own card, badge and table styling. */

export function classNames(...parts: (string | false | null | undefined)[]) {
  return parts.filter(Boolean).join(' ');
}

// --- status encoding --------------------------------------------------------
// State is encoded in form as well as colour, so it survives a greyscale print
// and does not rely on hue alone.

export function StatusPill({ status }: { status: string }) {
  const tone =
    status === 'Completed'
      ? 'bg-ok/15 text-ok'
      : status === 'In Progress'
        ? 'bg-accent/15 text-accent'
        : 'bg-rule/40 text-muted';
  return <span className={classNames('pill', tone)}>{status}</span>;
}

export function DelayPill({ days }: { days: number }) {
  if (days <= 0) return <span className="text-faint">&mdash;</span>;
  const tone =
    days >= 15 ? 'bg-risk/15 text-risk' : days >= 7 ? 'bg-warn/15 text-warn' : 'bg-rule/40 text-muted';
  return (
    <span className={classNames('pill font-mono', tone)}>
      {days}d late
    </span>
  );
}

export function HealthBar({ value }: { value: number }) {
  const pct = Math.max(0, Math.min(100, value));
  const tone = pct >= 80 ? 'bg-ok' : pct >= 50 ? 'bg-warn' : 'bg-risk';
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-full max-w-[7rem] overflow-hidden rounded-full bg-rule/60">
        <div className={classNames('h-full rounded-full', tone)} style={{ width: `${pct}%` }} />
      </div>
      <span className="font-mono text-xs text-muted">{value.toFixed(0)}</span>
    </div>
  );
}

// --- layout -----------------------------------------------------------------

export function Stat({
  label,
  value,
  hint,
  tone = 'default',
}: {
  label: string;
  value: ReactNode;
  hint?: string;
  tone?: 'default' | 'ok' | 'warn' | 'risk';
}) {
  const colour =
    tone === 'ok' ? 'text-ok' : tone === 'warn' ? 'text-warn' : tone === 'risk' ? 'text-risk' : 'text-ink';
  return (
    <div className="border-r border-rule px-4 py-3 last:border-r-0">
      <div className="label">{label}</div>
      <div className={classNames('mt-1 font-mono text-2xl font-semibold leading-none', colour)}>
        {value}
      </div>
      {hint ? <div className="mt-1.5 text-xs text-muted">{hint}</div> : null}
    </div>
  );
}

export function Panel({
  title,
  subtitle,
  actions,
  children,
  bleed = false,
}: {
  title: string;
  subtitle?: string;
  actions?: ReactNode;
  children: ReactNode;
  bleed?: boolean;
}) {
  return (
    <section className="card overflow-hidden">
      <header className="flex flex-wrap items-baseline justify-between gap-2 border-b border-rule px-4 py-3">
        <div>
          <h2 className="text-sm font-semibold">{title}</h2>
          {subtitle ? <p className="mt-0.5 text-xs text-muted">{subtitle}</p> : null}
        </div>
        {actions}
      </header>
      <div className={bleed ? '' : 'p-4'}>{children}</div>
    </section>
  );
}

export function Empty({ message }: { message: string }) {
  return <p className="py-6 text-center text-sm text-muted">{message}</p>;
}

export function Spinner({ label = 'Loading' }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 py-8 text-sm text-muted" role="status">
      <span className="h-3 w-3 animate-spin rounded-full border-2 border-rule border-t-accent" />
      {label}
    </div>
  );
}

export function ErrorNote({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div
      role="alert"
      className="flex flex-wrap items-center gap-3 rounded border border-risk/40 bg-risk/10 px-3 py-2 text-sm text-risk"
    >
      <span className="flex-1">{message}</span>
      {onRetry ? (
        <button type="button" onClick={onRetry} className="btn-ghost !py-1 !text-xs">
          Try again
        </button>
      ) : null}
    </div>
  );
}
