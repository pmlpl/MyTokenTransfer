FROM python:3.11-slim AS base

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# ======== 构建阶段：装编译依赖 → pip 安装生产依赖到 /install ========
FROM base AS builder

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        build-essential pkg-config libssl-dev libffi-dev \
 && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
# 注意：pyproject 未定义 [standard] extra，直接装生产依赖组
RUN pip install --upgrade pip \
 && pip install --prefix=/install .

# ======== 运行阶段：只带运行时依赖 + 代码 ========
FROM base AS runtime

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates tzdata curl \
 && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local

# 代码 + 迁移 + 入口脚本
COPY app          ./app
COPY alembic      ./alembic
COPY alembic.ini  ./
COPY entrypoint.sh ./entrypoint.sh

RUN chmod +x ./entrypoint.sh

EXPOSE 8082

HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=10 \
    CMD curl -fsS http://127.0.0.1:8082/health || exit 1

ENTRYPOINT ["./entrypoint.sh"]
