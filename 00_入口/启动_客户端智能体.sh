#!/usr/bin/env bash
# 第 6 步生成：启动 robocrew_client_agent。
set -euo pipefail
export HF_HOME="${HF_HOME:-/home/wen/xinlerbot/cache/hf_cache}"
export HF_HUB_CACHE="${HF_HUB_CACHE:-/home/wen/xinlerbot/cache/hf_cache/hub}"
cd /home/wen/xinlerbot/repos/local/robocrew_client_agent
exec python3 run_robocrew_client_agent.py "$@"
