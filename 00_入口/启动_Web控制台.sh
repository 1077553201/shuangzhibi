#!/usr/bin/env bash
# 第 7 步生成：启动 webcontrol（uvicorn 方式，端口 8000）。
set -euo pipefail
export HF_HOME="${HF_HOME:-/home/wen/xinlerbot/cache/hf_cache}"
export HF_HUB_CACHE="${HF_HUB_CACHE:-/home/wen/xinlerbot/cache/hf_cache/hub}"
cd /home/wen/xinlerbot/apps/webcontrol
exec python3 -m uvicorn app:app --host 0.0.0.0 --port 8000 "$@"
