FROM node:22-alpine AS frontend
WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci
COPY index.html tsconfig.json vite.config.ts ./
COPY src ./src
RUN npm run build

FROM caddy:2-alpine AS web
COPY deploy/Caddyfile /etc/caddy/Caddyfile
COPY --from=frontend /build/dist /srv/mojing

FROM python:3.11-slim AS api
WORKDIR /app
COPY server/requirements.txt server/requirements-parsers.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-parsers.txt \
    && useradd --system --create-home mojing \
    && install -d -o mojing -g mojing /data
COPY server/app ./app
USER mojing
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
