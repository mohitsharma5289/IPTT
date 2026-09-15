/** @type {import('next').NextConfig} */

// IMPORTANT: rewrites are evaluated during `next build`, not at startup. Next
// serialises them into .next/routes-manifest.json and the standalone server
// reads that file, so API_ORIGIN is a BUILD-TIME value. Setting it as a runtime
// environment variable on the container has no effect whatsoever - the symptom
// is the web pod proxying to the default and failing with ECONNREFUSED.
//
// The default is therefore a hostname that resolves to the API in every
// environment we deploy to:
//   * OpenShift - Service/iptt-api in the same namespace
//   * compose    - the service is named iptt-api for exactly this reason
// so one image is correct everywhere and no build argument has to be
// remembered. Override at build time only if your API answers elsewhere:
//   docker build --build-arg API_ORIGIN=http://something-else:8000 frontend
// `||`, not `??`: an unset --build-arg still defines the variable as an empty
// string, and `??` would accept that and produce a destination with no host.
const API_ORIGIN = process.env.API_ORIGIN || 'http://iptt-api:8000';

const nextConfig = {
  output: 'standalone',          // small runtime image for OpenShift
  reactStrictMode: true,
  poweredByHeader: false,
  async rewrites() {
    // The browser only ever talks to this origin, so the session cookie stays
    // first-party and no CORS preflight is needed in the deployed topology.
    return [{ source: '/api/:path*', destination: `${API_ORIGIN}/api/:path*` }];
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
