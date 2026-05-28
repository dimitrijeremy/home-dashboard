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
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
