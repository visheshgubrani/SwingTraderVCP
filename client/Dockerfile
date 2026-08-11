# syntax=docker/dockerfile:1

FROM node:22-bookworm-slim AS build

WORKDIR /app

RUN corepack enable && corepack prepare pnpm@10.33.2 --activate

COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

COPY index.html vite.config.ts tsconfig*.json components.json ./
COPY public ./public
COPY src ./src

ARG VITE_API_BASE_URL=http://127.0.0.1:8000/api/v1
ARG VITE_WS_URL=
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL} \
    VITE_WS_URL=${VITE_WS_URL}

RUN pnpm build


FROM nginx:1.27-alpine AS runtime

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD wget -qO- http://127.0.0.1/ >/dev/null || exit 1
