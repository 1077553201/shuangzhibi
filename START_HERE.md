# START_HERE — xinlerbot 项目总入口

> 第 6 步生成。整理已完成，旧路径保留为软链接，新旧路径均可使用。

## 1. 我要做什么

| 我要做什么 | 真实位置（新） | 旧路径（软链，仍可用） | 启动什么 |
|---|---|---|---|
| 启动 Web 控制台 | apps/webcontrol | lerobot/webcontrol | app.py |
| 启动客户端智能体 | repos/local/robocrew_client_agent | lerobot/robocrew_client_agent | run_robocrew_client_agent.py |
| 跑 LeRobot | repos/upstream/lerobot | lerobot/lerobot | 原命令 |
| 跑 XLeRobot 示例 | repos/upstream/XLeRobot/software/examples | lerobot/XLeRobot/software/examples | 对应示例 |
| 看数据集 | data/datasets | lerobot/datasets | 32 个数据集 |
| 看训练结果 | artifacts/outputs、artifacts/reports | lerobot/outputs、lerobot/reports | 产物和报告 |
| 看日志 | artifacts/logs | lerobot/logs | 运行日志 |
| 看文档资料 | docs/ | — | pptx / pdf / 报告 / 截图 |
| 看旧备份 | archive/ | — | 已归档，不参与运行 |

## 2. 一键启动

- 00_入口/启动_Web控制台.sh
- 00_入口/启动_客户端智能体.sh
- 00_入口/启动_LeRobot.sh
- 00_入口/启动_XLeRobot示例.sh

## 3. 环境提醒（来自第 1 步基线）

- `python` 不在 PATH，请使用 `python3` 或先激活虚拟环境（`lerobot/.venv` 或 `lerobot/envs/lerobot-gpu`）。
- `HF_HOME` / `HF_HUB_CACHE` 当前为空；启动脚本已显式设置为：
  - `HF_HOME=/home/wen/xinlerbot/cache/hf_cache`
  - `HF_HUB_CACHE=/home/wen/xinlerbot/cache/hf_cache/hub`
- 两个 git 仓库（`repos/upstream/lerobot`、`repos/upstream/XLeRobot`）有 1145 个 CRLF 假脏文件。禁止执行 `git checkout .` / `git reset --hard` / `git stash`。
- 顶层 `/home/wen/xinlerbot/lerobot/.git` 已归档到 `archive/empty_git_dirs/`；`lwy/` 已整体归档到 `archive/lwy_旧工作副本/`。

## 4. 当前整理进度

- [x] 第 1 步：冻结与基线（只读）
- [x] 第 2 步：建骨架 + 入口（只新增）
- [x] 第 3 步：归档旧物
- [x] 第 4 步：纯资料进 docs
- [x] 第 5 步：整体迁移运行单元 + 软链接（15/15 完成）
- [x] 第 6 步：验收 + 收尾

## 5. 待定项（原位保留，未归档）

- `lerobot/xlerobot_remote_keyboard_client.py`：疑似键盘遥控客户端脚本，与已归档的 `archive/old_scripts/xlerobot_remote_keyboard_client——01.py` 为兄弟版本。现行版待确认，暂原位保留。
- `xlerobot/` 空目录壳：第 2 步建的空导航骨架，尚未填软链。暂原位保留。
- `lerobot/envs/`、`lerobot/uv-site/`、`lerobot/.venv/`、`lerobot/.vscode/`、`lerobot/.agents/`：按最小改动原则，原地未动。
- `docs/图片`、`docs/视频`、`docs/项目报告`：暂无素材来源，留空占位。

## 6. 结构速览

- 真实运行单元：`repos/`、`apps/`、`data/`、`cache/`、`artifacts/`、`examples/`
- 旧路径兼容层：`lerobot/`（15 个软链 + 8 个受保护真实目录 + 1 个保留文件）
- 归档区：`archive/`（lwy、备份、压缩包、空 git、旧脚本）
- 文档区：`docs/`
- 入口区：`00_入口/`、`START_HERE.md`
