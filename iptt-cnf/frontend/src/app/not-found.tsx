import Link from 'next/link';

export default function NotFound() {
  return (
    <main className="flex min-h-screen items-center justify-center px-4">
      <div className="text-center">
        <p className="font-mono text-xs uppercase tracking-widest text-accent">404</p>
        <h1 className="mt-2 text-xl font-semibold">That page does not exist</h1>
        <Link href="/" className="btn-ghost mt-4">Back to the portfolio</Link>
      </div>
    </main>
  );
}
