#!/usr/bin/env bash
# 一键启动：安装依赖（如需要）→ 构建前端 → 启动后端
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/pip install -q fastapi uvicorn pytest httpx
fi

if [ ! -d frontend/dist ]; then
  (cd frontend && npm install && npm run build)
fi

cd backend
exec ../.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
