# routebench-web — the standalone Next.js app (separate Fly app from the API).
#
# Built from the REPO ROOT context, because that is where
# `fly deploy -c fly.web.toml` runs (see DEPLOY.md). Every path is therefore
# web/-prefixed; a bare `COPY package.json` would look in the repo root, which
# is the Python project and has none, and the build would fail on the first
# COPY.
FROM node:22-slim AS builder
WORKDIR /app

COPY web/package.json web/package-lock.json ./
RUN npm ci

COPY web/ .

# Baked in at build time: NEXT_PUBLIC_* is inlined into the client bundle, so it
# cannot be supplied as a runtime secret. It is a public URL, not a secret.
ARG NEXT_PUBLIC_API_BASE="http://localhost:8000"
ENV NEXT_PUBLIC_API_BASE=${NEXT_PUBLIC_API_BASE}

RUN npm run build

FROM node:22-slim AS runtime
WORKDIR /app
ENV NODE_ENV=production
ENV PORT=3000

# Standalone bundles a minimal server plus only the traced dependencies.
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
# public/ is not traced into standalone, so it is copied explicitly. It always
# exists (a .gitkeep guarantees it) so this COPY cannot fail an otherwise-good
# build even when there are no static assets yet.
COPY --from=builder /app/public ./public

EXPOSE 3000
CMD ["node", "server.js"]
