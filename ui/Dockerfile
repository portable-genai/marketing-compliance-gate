# `marketing-compliance-gate`: Marketing Compliance Gate console image.
#
# The console is mounted under a reverse-proxy sub-path when embedded in the portal
# (`NEXT_PUBLIC_BASE_PATH=/apps/marketing-compliance-gate`), and the base path is a BUILD-time input to Next.js: it
# rewrites every asset URL and the router's own prefix, so it cannot be supplied as a runtime
# environment variable. The same is true of NEXT_PUBLIC_API_BASE, which ui/lib/api.ts inlines and
# ui/lib/csp.mjs must agree with. Under the portal it is `/apps/marketing-compliance-gate/api`, the path the
# portal proxies to this system's API with the verified identity injected, so the console calls
# its API same-origin and needs no CORS. A build whose CSP does not name the origin its own
# default calls blocks itself, visible only in the browser console. Both are ARGs for that
# reason, and a deployment records the values it built with.
FROM node:22.22.0-alpine@sha256:e4bf2a82ad0a4037d28035ae71529873c069b13eb0455466ae0bc13363826e34 AS builder

ENV NEXT_TELEMETRY_DISABLED=1
WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

ARG NEXT_PUBLIC_BASE_PATH=""
ARG NEXT_PUBLIC_API_BASE=""
ENV NEXT_PUBLIC_BASE_PATH=${NEXT_PUBLIC_BASE_PATH} \
    NEXT_PUBLIC_API_BASE=${NEXT_PUBLIC_API_BASE}

# The build context is ui/, so only the console's own checks run here; the Python suite and the
# browser demo need the full checkout and run in CI instead. `assert-hydratable` is the one that
# matters most for a deployed console: it fails the build if any script tag would reach the
# browser without the CSP nonce, which renders as dead markup rather than an error.
RUN npm run lint \
 && npm run test \
 && npm run build \
 && npm run assert-hydratable

FROM node:22.22.0-alpine@sha256:e4bf2a82ad0a4037d28035ae71529873c069b13eb0455466ae0bc13363826e34 AS runtime

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0 \
    PORT=3000

WORKDIR /app

# A digest pin freezes the base image, and therefore also freezes its unpatched packages.
# Reproducible and patched are independent properties; the pin only buys the first.
RUN apk upgrade --no-cache

# Remove npm (and its bundled corepack and yarn) from the RUNTIME image. Standalone output ships
# its own minimal node_modules and starts with `node server.js`, so nothing here installs
# packages, and npm BUNDLES its dependencies, which is where a serving container's Node
# advisories come from. The package manager does not belong in a serving container.
RUN rm -rf /usr/local/lib/node_modules/npm \
           /usr/local/lib/node_modules/corepack \
           /usr/local/bin/npm /usr/local/bin/npx /usr/local/bin/corepack \
           /opt/yarn-* /usr/local/bin/yarn /usr/local/bin/yarnpkg

RUN addgroup -S mkt -g 10001 \
 && adduser -S mkt -u 10001 -G mkt

COPY --from=builder --chown=mkt:mkt /app/.next/standalone ./
COPY --from=builder --chown=mkt:mkt /app/.next/static ./.next/static
# No ./public: this console ships no static assets of its own, and COPYing a directory that does
# not exist fails the build rather than being skipped.

USER mkt
EXPOSE 3000

CMD ["node", "server.js"]
