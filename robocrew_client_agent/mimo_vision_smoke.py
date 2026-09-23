"""小米 MiMo OpenAI 兼容视觉接口最小验证脚本。

用途：
1. 使用当前 client 摄像头适配器抓取一帧图像。
2. 尝试多种 OpenAI-compatible 常见视觉消息格式。
3. 找到 MiMo 当前接口真正接受的图片字段格式。
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
import urllib.request

from client_camera_adapter import ClientRobotCamera
from client_servo_adapter import ClientServoControler
from config import LLM_MODEL, MAIN_CAMERA_KEY, OPENAI_COMPATIBLE_API_BASE, OPENAI_COMPATIBLE_API_KEY, ROBOT_ID, ROBOT_IP


@dataclass
class PayloadVariant:
    name: str
    content: object


def build_variants(image_data_url: str) -> list[PayloadVariant]:
    text = "请用一句中文简短描述这张图片里最明显的内容。如果你没有收到图片，请明确说没有收到图片。"
    return [
        PayloadVariant(
            name="openai_nested_image_url",
            content=[
                {"type": "image_url", "image_url": {"url": image_data_url}},
                {"type": "text", "text": text},
            ],
        ),
        PayloadVariant(
            name="openai_flat_image_url",
            content=[
                {"type": "text", "text": text},
                {"type": "image_url", "image_url": image_data_url},
            ],
        ),
        PayloadVariant(
            name="input_image",
            content=[
                {"type": "input_text", "text": text},
                {"type": "input_image", "image_url": image_data_url},
            ],
        ),
        PayloadVariant(
            name="image_url_text_first",
            content=[
                {"type": "image_url", "image_url": {"url": image_data_url}},
                {"type": "text", "text": text},
            ],
        ),
    ]


def request_mimo(model: str, content: object) -> str:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "temperature": 0,
    }

    url = OPENAI_COMPATIBLE_API_BASE.rstrip("/") + "/chat/completions"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {OPENAI_COMPATIBLE_API_KEY}",
        },
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=60) as response:
        data = json.loads(response.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"]


def main() -> None:
    model = LLM_MODEL.removeprefix("openai:")
    servo_controller = ClientServoControler(remote_ip=ROBOT_IP, robot_id=ROBOT_ID)

    try:
        servo_controller.connect()
        camera = ClientRobotCamera(servo_controller=servo_controller, camera_key=MAIN_CAMERA_KEY)
        image_bytes = camera.capture_image()
        image_b64 = base64.b64encode(image_bytes).decode("utf-8")
        image_data_url = f"data:image/jpeg;base64,{image_b64}"

        print("[摄像头]", camera.last_shape)
        print("[模型]", model)
        for variant in build_variants(image_data_url):
            print(f"\n[测试格式] {variant.name}")
            try:
                print(request_mimo(model, variant.content))
            except Exception as exc:
                print(f"请求失败：{exc}")
    finally:
        servo_controller.disconnect()


if __name__ == "__main__":
    main()
