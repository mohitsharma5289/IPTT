import type { Metadata } from 'next';
import './globals.css';

import { SessionProvider } from '@/components/session';

export const metadata: Metadata = {
  title: 'IPTT — Infrastructure Project Tracking',
  description: 'Deployment tracking for network infrastructure projects',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
        <link
          rel="stylesheet"
          href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600;700&display=swap"
        />
        <script
          // Apply the stored theme before first paint so the page never flashes.
          dangerouslySetInnerHTML={{
            __html: `try{var t=localStorage.getItem('iptt_theme');var d=t==='dark'||(t===null&&matchMedia('(prefers-color-scheme: dark)').matches);document.documentElement.classList.toggle('dark',d)}catch(e){}`,
          }}
        />
      </head>
      <body>
        <SessionProvider>{children}</SessionProvider>
      </body>
    </html>
  );
}
