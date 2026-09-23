# 双智臂 · XLeRobot 双臂机器人遥操作与智能控制系统

> 版本 V1.0 · 2026-09

## 一、系统整体框架

本系统控制一台 XLeRobot 双臂移动机器人，由三部分组成：

```
┌──────────────────────────┐
│  Ubuntu 主机              │
│  ├─ 遥操作台 (8765)       │ ← 浏览器键盘/VR/手动控制
│  ├─ Web 控制台 (8000)     │ ← 文字/语音 LLM 任务
│  ├─ 客户端智能体          │ ← LLM 任务路由与执行
│  └─ 摄像头/键盘/VR 输入    │
└──────────┬───────────────┘
           │ WiFi + ZMQ (5555 cmd / 5556 obs)
           ▼
┌──────────────────────────┐
│  树莓派 (10.168.1.144)    │
│  └─ xlerobot_host         │ ← 接收指令，驱动硬件
└──────────┬───────────────┘
           │ USB 串口 (Feetech 舵机)
           ▼
┌──────────────────────────┐
│  XLeRobot 机械臂硬件       │
│  ├─ 左臂 (6 自由度)        │
│  ├─ 右臂 (6 自由度)        │
│  ├─ 头部 (2 自由度)        │
│  ├─ 底盘 (麦轮/2轮)        │
│  └─ 3 路摄像头             │
└──────────────────────────┘
```

**数据流**：
1. 用户在 Ubuntu 浏览器（8765 页面）按键
2. 前端发送指令到 web_server.py
3. web_server 通过 ZMQ (5555) 发给树莓派 host
4. host 解析指令，驱动 Feetech 舵机
5. 舵机状态通过 ZMQ (5556) 回传
6. 摄像头画面通过 HTTP 流回浏览器

## 二、启动入口

**完整启动流程（3 步）**：

### 第 1 步：树莓派启动 host

```bash
ssh mh@10.168.1.144
conda activate lerobot
cd ~/xlerobot-dev/lerobot
python -m lerobot.robots.xlerobot.xlerobot_host
# 提示 "Press ENTER to VALIDATE..." 时按回车
```

**这个终端保持打开，不要关。**

### 第 2 步：Ubuntu 启动遥操作台

```bash
cd /home/wen/xinlerbot/repos/local/robocrew_client_agent
/home/wen/xinlerbot/repos/upstream/lerobot/.venv/bin/python -c "
import sys, runpy
sys.path.insert(0, '.')
sys.path.insert(0, '/home/wen/xinlerbot/apps/robocrew_client_agent')
import config; config.ROBOT_IP = '10.168.1.144'
runpy.run_path('web_server.py', run_name='__main__')
"
```

### 第 3 步：浏览器打开

```
http://localhost:8765
```

**这就是"打开网页操控机械臂"的最终入口。**

**注意**：不要和 8000 端口的 webcontrol 同时运行（会抢 ZMQ）。

## 三、环境还原说明

> 本仓库无法上传完整的 30G 工作区（含环境、缓存、数据集）。从 GitHub 拉取后，需要按以下步骤还原环境。

### 3.1 前置软件

| 组件 | 版本要求 | 说明 |
|---|---|---|
| Ubuntu | 22.04+ | 主机操作系统 |
| Python | 3.12+ | Ubuntu 端 |
| Git | 任意 | 拉取代码 |
| conda | 任意 | 树莓派端环境（可选，可用 venv 替代） |

### 3.2 拉取代码

```bash
git clone https://github.com/1077553201/shuangzhibi.git
cd shuangzhibi
```

### 3.3 还原上游依赖

本仓库不包含两个上游开源仓库，需要单独 clone：

```bash
# 在 shuangzhibi 同级目录
cd ..
git clone https://github.com/huggingface/lerobot.git
git clone https://github.com/Vector-Wangel/XLeRobot.git
```

**目录布局要求**（关键）：
```
workspace/
├── shuangzhibi/              ← 本仓库
├── lerobot/                  ← 上游
└── XLeRobot/                 ← 上游
```

### 3.4 创建 Python 环境（Ubuntu 主机）

```bash
cd lerobot
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip

# 装 lerobot（可编辑模式）
pip install -e .

# 装遥操作台依赖
pip install fastapi "uvicorn[standard]" pyzmq pynput paramiko jinja2 aiofiles python-multipart

# 装 LLM Agent 依赖（可选，仅 LLM 任务需要）
pip install robocrew --no-deps langchain-core langchain langchain-openai openai
```

**注意**：
- `robocrew` 必须用 `--no-deps` 装，否则会降级 lerobot
- `pynput` 在 Linux 上可能需要 `pip install six python-xlib`

### 3.5 还原数据与配置

从网盘下载 `xinlerbot_backup_20260923.tar.gz`，解压到工作区：

```bash
tar -xzf xinlerbot_backup_20260923.tar.gz
```

**备份包内容**：
| 路径 | 说明 |
|---|---|
| `data/calibration/` | 机械臂校准文件（必需，否则舵机无法归零） |
| `data/models/` | YOLO 权重等 |
| `data/datasets/` | 训练数据集（可选，66M） |
| `artifacts/reports/train/` | ACT 训练检查点（可选，2.9G） |
| `artifacts/outputs/train/` | ACT 最终模型（可选，4.0G） |
| `cache/hf_cache/` | HF 模型缓存（可选，8.6G，可重新下载） |
| `envs/` | 环境（可选，可重建） |

**最小还原**：只需 `data/calibration/` 和 `data/models/`。

### 3.6 树莓派环境还原

```bash
# SSH 到树莓派
ssh mh@10.168.1.144

# 用 conda 或 venv 创建环境
conda create -n lerobot python=3.12
conda activate lerobot

# 安装 lerobot
cd ~/xlerobot-dev/lerobot
pip install -e .
pip install pyzmq
```

### 3.7 配置 IP

**每次启动前，确认树莓派 IP 正确**。

当前树莓派 IP：`10.168.1.144`

如果 IP 变化，需要修改：
- `repos/local/robocrew_client_agent/config.py` 第 9 行 `ROBOT_IP`
- `repos/local/robocrew_client_agent/run_xlerobot_remote.py` 第 33 行 `HOST`

或启动时通过环境变量覆盖：
```bash
export ROBOT_IP=10.168.1.144
```

### 3.8 环境变量（可选）

```bash
export OPENAI_COMPATIBLE_API_KEY="你的 API Key"   # LLM 任务需要
export HF_HOME=/path/to/cache/hf_cache
export HF_HUB_CACHE=/path/to/cache/hf_cache/hub
```

### 3.9 验证环境

```bash
# 1. 检查 lerobot 能否 import
python -c "import lerobot; print(lerobot.__file__)"

# 2. 检查 zmq
python -c "import zmq; print('zmq OK')"

# 3. 检查 pynput（键盘控制需要）
python -c "from pynput import keyboard; print('pynput OK')"

# 4. 检查 web_server 依赖
python -c "import fastapi, uvicorn; print('web deps OK')"
```

全部通过即可启动。

## 四、备份包说明

因工作区总 33G（含缓存、环境、训练产物），无法上传 GitHub。备份包通过百度网盘分发：

- **文件名**：`xinlerbot_backup_20260923.tar.gz`
- **体积**：约 30G（完整版）/ 12G（精简版，去掉缓存和环境）
- **下载地址**：见仓库 Issues 或 Release 说明

**精简版内容**（推荐）：
- 代码：`repos/`、`apps/`、`00_入口/`、`docs/`、`demo/`
- 数据：`data/`（含校准、模型、数据集）
- 产物：`artifacts/`（含训练检查点）
- 归档：`archive/`

**不包含**（可重建）：
- `cache/hf_cache/`（8.6G，pip/HF 自动下载）
- `envs/`、`repos/upstream/lerobot/.venv/`（pip 重建）

## 五、常见问题

**Q1：启动 host 报"Failed to read Goal_Position"？**
A：舵机总线通信抖动。重跑一次通常能过。如果反复失败，检查 USB 线/供电。

**Q2：摄像头读不到（`/dev/video*` 找不到）？**
A：USB 供电不足。建议加有源 USB Hub。

**Q3：按键盘机械臂不动？**
A：检查三点：① 焦点是否在输入框；② 是否用了"按住"而非"单击"；③ host 是否在运行（`ss -ltnp | grep 5555`）。

**Q4：web_server 启动报"address already in use"？**
A：旧进程未退。`pkill -f web_server.py` 后重启。

**Q5：树莓派 host 自动启动失败？**
A：web_server 有 SSH 自动拉起 host 功能，但会卡在"Press ENTER"提示。必须先手动 SSH 启动 host，再启动 web_server。

## 六、目录结构速查

| 路径 | 内容 |
|---|---|
| `repos/upstream/lerobot/` | 上游 LeRobot 源码 |
| `repos/upstream/XLeRobot/` | 上游 XLeRobot 源码 |
| `repos/local/robocrew_client_agent/` | 自研核心 |
| `apps/webcontrol/` | Web 控制台（8000） |
| `data/` | 数据集、校准、模型 |
| `artifacts/` | 训练产物、报告、日志 |
| `cache/hf_cache/` | HuggingFace 缓存 |
| `docs/` | 文档 |
| `demo/` | 演示材料 |
| `archive/` | 历史归档 |
| `00_入口/` | 一键启动脚本 |
| `START_HERE.md` | 总入口文档 |

## 七、许可与致谢

- 本项目基于 HuggingFace LeRobot 和 XLeRobot 开源项目
- 自研部分（`robocrew_client_agent/`、`webcontrol/`）版权归作者所有
- 上游部分遵循各自的开源协议
