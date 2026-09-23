"""检查本地 LeRobot 数据集是否写入成功。"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

os.environ.setdefault("POLARS_SKIP_CPU_CHECK", "1")

import cv2
import numpy as np
import path_setup  # noqa: F401
import pyarrow.parquet as pq


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="检查本地 LeRobot 数据集元数据、帧数和图像预览。")
    parser.add_argument("root", help="数据集根目录，例如 E:\\lerobot\\datasets\\xxx")
    parser.add_argument("--save-preview", action="store_true", help="保存第一帧图像预览。")
    return parser.parse_args()


def to_image_array(value) -> np.ndarray | None:
    if isinstance(value, np.ndarray):
        arr = value
    elif hasattr(value, "as_py"):
        value = value.as_py()
        arr = np.array(value)
    else:
        arr = np.array(value)

    if arr.ndim == 3:
        return arr.astype(np.uint8)
    return None


def main() -> None:
    args = parse_args()
    root = Path(args.root)
    info_path = root / "meta" / "info.json"
    tasks_path = root / "meta" / "tasks.parquet"
    data_path = root / "data" / "chunk-000" / "file-000.parquet"

    if not info_path.exists():
        raise FileNotFoundError(f"找不到 info.json：{info_path}")
    if not data_path.exists():
        raise FileNotFoundError(f"找不到数据 parquet：{data_path}")

    info = json.loads(info_path.read_text(encoding="utf-8"))
    table = pq.read_table(data_path)
    columns = table.column_names
    frame_count = table.num_rows

    print(f"[数据集] {root}")
    print(f"[repo_id] {info.get('repo_id')}")
    print(f"[robot_type] {info.get('robot_type')}")
    print(f"[fps] {info.get('fps')}")
    print(f"[帧数] {frame_count}")
    print("[parquet字段]")
    for name in columns:
        print(f"  - {name}")

    print("[feature字段]")
    for name, feature in info.get("features", {}).items():
        print(f"  - {name}: dtype={feature.get('dtype')}, shape={feature.get('shape')}")

    if frame_count == 0:
        print("[提示] 数据集中没有帧。")
        return

    first = table.slice(0, 1).to_pydict()
    task = ""
    if tasks_path.exists():
        tasks = pq.read_table(tasks_path).to_pydict()
        task_list = tasks.get("task", [])
        if task_list:
            task = task_list[0]
    if not task:
        task = first.get("task", [""])[0]
    print(f"[任务] {task}")

    action = first.get("action")
    state = first.get("observation.state")
    if action:
        print(f"[action维度] {len(action[0])} -> {action[0]}")
    if state:
        print(f"[state维度] {len(state[0])} -> {state[0]}")

    image_columns = [name for name in columns if name.startswith("observation.images.")]
    image_features = [
        name
        for name, feature in info.get("features", {}).items()
        if name.startswith("observation.images.") and feature.get("dtype") in {"image", "video"}
    ]
    if not image_columns:
        print("[parquet图像] 未发现内嵌 observation.images.* 字段。")
    if not image_features:
        print("[feature图像] 未发现 observation.images.* feature。")
        return

    print("[图像字段]")
    for name in image_columns:
        image = to_image_array(first[name][0])
        shape = None if image is None else image.shape
        print(f"  - {name}: shape={shape}")

        if args.save_preview and image is not None:
            output_name = name.replace("observation.images.", "").replace(".", "_")
            output_path = root / f"preview_{output_name}.jpg"
            cv2.imwrite(str(output_path), cv2.cvtColor(image, cv2.COLOR_RGB2BGR))
            print(f"    saved: {output_path}")

    for name in image_features:
        if name in image_columns:
            continue
        video_path = root / "videos" / name / "chunk-000" / "file-000.mp4"
        if not video_path.exists():
            print(f"  - {name}: 未找到视频文件 {video_path}")
            continue

        capture = cv2.VideoCapture(str(video_path))
        ok, frame = capture.read()
        capture.release()
        if not ok:
            print(f"  - {name}: 视频存在但读取第一帧失败：{video_path}")
            continue

        print(f"  - {name}: video={video_path}, first_frame_shape={frame.shape}")
        if args.save_preview:
            output_name = name.replace("observation.images.", "").replace(".", "_")
            output_path = root / f"preview_{output_name}.jpg"
            cv2.imwrite(str(output_path), frame)
            print(f"    saved: {output_path}")


if __name__ == "__main__":
    main()
