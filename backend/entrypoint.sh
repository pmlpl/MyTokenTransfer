#!/bin/sh
set -eu

# ============================================================
#  backend 容器入口：等 DB → 跑迁移 → 启 uvicorn
# ============================================================

# —— 等待 MySQL 可达（用 python 探 TCP，避免 sh /dev/tcp 在双栈 hostname 下只试 IPv6 而卡死）——
echo "[entrypoint] Waiting for MySQL to become reachable ..."
MAX_TRIES=60
I=0
while [ $I -lt $MAX_TRIES ]; do
    if python3 - <<'PY'
import socket, os
url = os.environ.get("DATABASE_URL", "")
# 极简解析：从 mysql+aiomysql://user:pass@host:port/db 里抓 host:port
# 只解析到 @ 之后 / 之前的 netloc 部分
try:
    _, rest = url.split("://", 1)
except ValueError:
    raise SystemExit(1)
at = rest.rfind("@")
if at < 0:
    raise SystemExit(1)
netloc_and_path = rest[at+1:]
slash = netloc_and_path.find("/")
netloc = netloc_and_path[:slash] if slash >= 0 else netloc_and_path
# 兼容 IPv6 [::1]:3306 与 host:port
if netloc.startswith("["):
    cb = netloc.find("]")
    if cb < 0: raise SystemExit(1)
    host = netloc[1:cb]
    port_s = netloc[cb+2:] if netloc.startswith(":", cb+1) else "3306"
else:
    if ":" in netloc:
        host, _, port_s = netloc.rpartition(":")
    else:
        host, port_s = netloc, "3306"
port = int(port_s)
s = socket.create_connection((host, port), timeout=2)
s.close()
raise SystemExit(0)
PY
    then
        echo "[entrypoint] MySQL is reachable (attempt $((I+1))/$MAX_TRIES)"
        break
    fi
    I=$((I+1))
    sleep 1
done
if [ $I -eq $MAX_TRIES ]; then
    echo "ERROR: MySQL not reachable in time" >&2
    exit 1
fi

python - <<'PY'
import os, re, sys, asyncio, aiomysql

url = os.environ["DATABASE_URL"]
m = re.match(r"mysql\+aiomysql://([^:]+):([^@]+)@([^:/]+)(?::(\d+))?/(.+)", url)
if not m:
    print("ERROR: cannot parse DATABASE_URL", url, file=sys.stderr)
    sys.exit(2)
user, pw, host, port, db = m.group(1), m.group(2), m.group(3), int(m.group(4) or 3306), m.group(5)

async def wait():
    for i in range(90):
        try:
            conn = await aiomysql.connect(host=host, port=port,
                                          user=user, password=pw, db=db,
                                          connect_timeout=5)
            await conn.ensure_closed() if hasattr(conn,"ensure_closed") else conn.close()
            print(f"  MySQL OK after {i*2}s")
            return
        except Exception as e:
            await asyncio.sleep(2)
    print("ERROR: MySQL not reachable in time", file=sys.stderr)
    sys.exit(1)

asyncio.run(wait())
PY

echo "[entrypoint] Running Alembic migrations ..."
alembic upgrade head

echo "[entrypoint] Starting uvicorn (0.0.0.0:8082) ..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8082 --proxy-headers
