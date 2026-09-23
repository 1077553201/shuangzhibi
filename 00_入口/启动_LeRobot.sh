#!/usr/bin/env bash
# 第 6 步生成：进入 lerobot 仓库，不做任何修改，由使用者自行跑原命令。
set -euo pipefail
export HF_HOME="${HF_HOME:-/home/wen/xinlerbot/cache/hf_cache}"
export HF_HUB_CACHE="${HF_HUB_CACHE:-/home/wen/xinlerbot/cache/hf_cache/hub}"
cd /home/wen/xinlerbot/repos/upstream/lerobot
exec bash -i
