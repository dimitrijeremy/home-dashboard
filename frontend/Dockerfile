# ── Stage 1: build static assets ──────────────────────────────────────────
FROM node:18 AS builder
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install --legacy-peer-deps
COPY . .
# VITE_BACKEND_URL kosong → frontend pakai relative URL /api/* yang di-proxy Nginx
RUN npm run build

# ── Stage 2: serve via Nginx ───────────────────────────────────────────────
FROM nginx:1.27-alpine
# openssl: entrypoint membuat self-signed sementara bila volume tls kosong
RUN apk add --no-cache openssl
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY nginx-locations.inc /etc/nginx/includes/locations.inc
COPY 15-ensure-tls.sh /docker-entrypoint.d/15-ensure-tls.sh
RUN chmod +x /docker-entrypoint.d/15-ensure-tls.sh
EXPOSE 80 443
CMD ["nginx", "-g", "daemon off;"]
