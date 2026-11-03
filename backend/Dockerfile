FROM python:3.11-slim AS base

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# ======== 构建阶段：pip 安装生产依赖到 /install ========
FROM base AS builder

# pip 源。默认留空走官方 PyPI；国内直连 pypi.org 常被限速到 10 kB/s 量级
# （实测 pyyaml 806 kB 下 92 秒），整个依赖树要几十分钟，因此支持构建时覆盖：
#   docker compose build --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple backend
ARG PIP_INDEX_URL=

# 不装 build-essential：本项目全部依赖都提供 manylinux 预编译 wheel
# （cryptography / pydantic-core / uvloop / greenlet / httptools 都不需要本地编译）。
# 这样构建完全绕开 apt，既不受 Debian 源在国内 100-250 kB/s 的限速影响，
# 也避免了 apt-get update 间歇性 exit 100 导致的整体构建失败。
COPY pyproject.toml ./
RUN pip install --upgrade pip ${PIP_INDEX_URL:+--index-url "$PIP_INDEX_URL"} \
 && pip install --prefix=/install ${PIP_INDEX_URL:+--index-url "$PIP_INDEX_URL"} .

# ======== 运行阶段：只带运行时依赖 + 代码 ========
FROM base AS runtime

# python:3.11-slim 已内置 ca-certificates 与 tzdata，无需 apt 安装。
# 健康检查改用 python 标准库，避免为了 curl 再拉一次 apt。
COPY --from=builder /install /usr/local

# 代码 + 迁移 + 入口脚本
COPY app          ./app
COPY alembic      ./alembic
COPY alembic.ini  ./
COPY entrypoint.sh ./entrypoint.sh

RUN chmod +x ./entrypoint.sh

EXPOSE 8082

HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=10 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8082/health', timeout=4).status == 200 else 1)"

ENTRYPOINT ["./entrypoint.sh"]
