# ===== 阶段一：构建前端产物 =====
FROM node:20-alpine AS builder

WORKDIR /app

# 先装依赖（利于缓存）
COPY package*.json ./
RUN npm ci || npm install

# 构建：VITE_API_BASE=/api 让生产走同域 Nginx 反代到后端
COPY . .
RUN VITE_API_BASE=/api npm run build

# ===== 阶段二：Nginx 托管 =====
FROM nginx:1.27-alpine AS runtime

# 删除默认站点与默认 mime.types 引用冲突：保留 mime.types 而覆盖 nginx.conf
RUN rm -f /etc/nginx/conf.d/default.conf

COPY --from=builder /app/dist        /usr/share/nginx/html
COPY             nginx.conf          /etc/nginx/nginx.conf

EXPOSE 80

HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=5 \
    CMD wget -qO- http://127.0.0.1/ >/dev/null 2>&1 || exit 1
