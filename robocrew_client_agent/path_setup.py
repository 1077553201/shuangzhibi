"""为当前 XLeRobot 工作目录设置本地源码导入路径。"""

from __future__ import annotations

import sys
from pathlib import Path


WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
LEROBOT_SRC = WORKSPACE_ROOT / "lerobot" / "src"

if str(LEROBOT_SRC) not in sys.path:
    sys.path.insert(0, str(LEROBOT_SRC))
