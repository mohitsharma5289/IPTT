# syntax=docker/dockerfile:1
# Multi-stage. The runtime stage carries only the standalone server output,
# which is a fraction of a full node_modules tree.
FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci --omit=dev --ignore-scripts || npm install --omit=dev --ignore-scripts

FROM node:22-alpine AS builder
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci --ignore-scripts || npm install --ignore-scripts
COPY . .
# Next bakes rewrites into routes-manifest.json during the build, so the API
# origin has to be known HERE, not at container start. The default in
# next.config.mjs (http://iptt-api:8000) is correct for both compose and
# OpenShift; this ARG exists only for a deployment where it is not.
ARG API_ORIGIN
ENV API_ORIGIN=${API_ORIGIN}
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:22-alpine AS runtime
WORKDIR /app
# HOSTNAME=:: is the dual-stack wildcard the Next standalone server binds to.
# With net.ipv6.bindv6only=0 (the Linux default) it serves IPv4 clients too, as
# v4-mapped addresses. Override with -e HOSTNAME=0.0.0.0 where IPv6 is disabled.
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=::

COPY --from=builder --chown=1001:0 /app/.next/standalone ./
COPY --from=builder --chown=1001:0 /app/.next/static ./.next/static

# OpenShift runs containers as an arbitrary UID in group 0.
RUN chgrp -R 0 /app && chmod -R g=u /app
USER 1001

EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD node -e "fetch('http://localhost:3000/login').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

CMD ["node", "server.js"]
