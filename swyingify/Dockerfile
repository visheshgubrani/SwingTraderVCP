# syntax=docker/dockerfile:1

FROM node:22-bookworm-slim AS deps

WORKDIR /app

RUN corepack enable && corepack prepare pnpm@10.33.2 --activate

COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile


FROM node:22-bookworm-slim AS build

WORKDIR /app

RUN corepack enable && corepack prepare pnpm@10.33.2 --activate

COPY --from=deps /app/node_modules ./node_modules
COPY . .

ENV NEXT_TELEMETRY_DISABLED=1
RUN pnpm build


FROM node:22-bookworm-slim AS runtime

WORKDIR /app

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

RUN useradd --create-home --uid 10001 nextjs

COPY --from=build --chown=nextjs:nextjs /app/public ./public
COPY --from=build --chown=nextjs:nextjs /app/.next/standalone ./
COPY --from=build --chown=nextjs:nextjs /app/.next/static ./.next/static

USER nextjs

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD node -e "fetch('http://127.0.0.1:3000').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

CMD ["node", "server.js"]
