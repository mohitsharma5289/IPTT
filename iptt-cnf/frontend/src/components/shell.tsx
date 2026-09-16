'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useEffect, useState } from 'react';

import { useSession } from '@/components/session';
import { classNames } from '@/components/ui';

function ThemeToggle() {
  const [dark, setDark] = useState(false);

  useEffect(() => {
    let stored: string | null = null;
    try {
      stored = window.localStorage.getItem('iptt_theme');
    } catch {
      /* storage can be unavailable; fall back to the OS preference */
    }
    const prefersDark =
      stored === 'dark' ||
      (stored === null && window.matchMedia('(prefers-color-scheme: dark)').matches);
    setDark(prefersDark);
    document.documentElement.classList.toggle('dark', prefersDark);
  }, []);

  function toggle() {
    const next = !dark;
    setDark(next);
    document.documentElement.classList.toggle('dark', next);
    try {
      window.localStorage.setItem('iptt_theme', next ? 'dark' : 'light');
    } catch {
      /* per-viewer convenience only */
    }
  }

  return (
    <button
      type="button"
      onClick={toggle}
      className="btn-ghost !px-2 !py-1"
      aria-label={dark ? 'Switch to light theme' : 'Switch to dark theme'}
    >
      <span aria-hidden className="font-mono text-xs">
        {dark ? 'LIGHT' : 'DARK'}
      </span>
    </button>
  );
}

export function Shell({ children }: { children: React.ReactNode }) {
  const { session, signOut, can } = useSession();
  const pathname = usePathname();

  const nav = [
    { href: '/', label: 'Portfolio' },
    { href: '/execution', label: 'Execution' },
    ...(can('admin')
      ? [
          { href: '/admin/users', label: 'Users' },
          { href: '/admin/audit', label: 'Audit' },
        ]
      : []),
  ];

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-20 border-b border-rule bg-surface/95 backdrop-blur">
        <div className="mx-auto flex max-w-[1400px] flex-wrap items-center gap-x-6 gap-y-2 px-4 py-2.5">
          <Link href="/" className="flex items-baseline gap-2">
            <span className="font-mono text-sm font-semibold tracking-tight text-accent">IPTT</span>
            <span className="hidden text-xs text-muted sm:inline">
              Infrastructure Project Tracking
            </span>
          </Link>

          <nav className="flex gap-1" aria-label="Main">
            {nav.map((item) => {
              const active =
                item.href === '/' ? pathname === '/' : pathname.startsWith(item.href);
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  aria-current={active ? 'page' : undefined}
                  className={classNames(
                    'rounded px-2.5 py-1 text-sm transition-colors',
                    active
                      ? 'bg-accent-soft font-medium text-accent'
                      : 'text-muted hover:bg-raised hover:text-ink',
                  )}
                >
                  {item.label}
                </Link>
              );
            })}
          </nav>

          <div className="ml-auto flex items-center gap-3">
            <ThemeToggle />
            {session ? (
              <>
                <Link
                  href="/account"
                  className="hidden text-xs text-muted hover:text-accent sm:inline"
                >
                  {session.username}
                  <span className="ml-1.5 font-mono uppercase text-faint">{session.role}</span>
                </Link>
                <button type="button" onClick={() => void signOut()} className="btn-ghost !py-1 !text-xs">
                  Sign out
                </button>
              </>
            ) : null}
          </div>
        </div>

        {session?.must_change_password ? (
          <div className="border-t border-warn/30 bg-warn/10 px-4 py-1.5 text-center text-xs text-warn">
            This account still uses the password it was issued with.{' '}
            <Link href="/account" className="font-medium underline">
              Change it now
            </Link>
            .
          </div>
        ) : null}
      </header>

      <main className="mx-auto max-w-[1400px] px-4 py-6">{children}</main>
    </div>
  );
}
