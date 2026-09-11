/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',          // small runtime image for OpenShift
  reactStrictMode: true,
  poweredByHeader: false,
  async rewrites() {
    // The browser only ever talks to this origin, so the session cookie stays
    // first-party and no CORS preflight is needed in the deployed topology.
    return [
      { source: '/api/:path*', destination: `${process.env.API_ORIGIN ?? 'http://localhost:8000'}/api/:path*` },
    ];
  },
  async headers() {
    return [{
      source: '/:path*',
      headers: [
        { key: 'X-Frame-Options', value: 'DENY' },
        { key: 'X-Content-Type-Options', value: 'nosniff' },
        { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
      ],
    }];
  },
};
export default nextConfig;
