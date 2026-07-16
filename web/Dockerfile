# routebench-web — the standalone Next.js app (separate Fly app from the API).
FROM node:22-slim AS builder
WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

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
COPY --from=builder /app/public ./public

EXPOSE 3000
CMD ["node", "server.js"]
