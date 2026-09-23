$ErrorActionPreference = "Stop"

# 官方 VR 遥控测试入口：只控制，不录制数据集。
# 适合先熟悉 VR 手柄映射、灵敏度和安全边界。
#
# 树莓派先启动：
#   cd ~/xlerobot-dev/lerobot
#   source .venv/bin/activate
#   python -m lerobot.robots.xlerobot.xlerobot_host

$env:PYTHONPATH = "E:\lerobot\lerobot\src"
$env:POLARS_SKIP_CPU_CHECK = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:XLEVR_PATH = "E:\lerobot\XLeRobot\XLeVR"

# 这里必须是 Windows 电脑在 VR 头显同一局域网里的 IP。
# 如果网页打不开，用 ipconfig 查看 Windows 的 IPv4 地址后改这里。
$env:XLEVR_HOST_IP = "192.168.1.239"

$python = "E:\lerobot\envs\lerobot-gpu\Scripts\python.exe"

Write-Host "[VR遥控] 只控制不录制。"
Write-Host "[VR遥控] 头显浏览器打开：https://$env:XLEVR_HOST_IP`:8443"
Write-Host "[VR遥控] 如果打不开，检查 Windows 防火墙是否放行 8443/8442。"
Write-Host "[VR遥控] 终端按 Ctrl+C 退出。"

& $python -m lerobot.scripts.lerobot_teleoperate `
  --robot.type=xlerobot_client `
  --robot.remote_ip=192.168.10.240 `
  --robot.id=my_xlerobot_pc `
  --robot.calibration_dir=E:\lerobot\calibration\robots `
  --robot.cameras="{ camera_0: {type: opencv, index_or_path: 'camera_0', width: 640, height: 480, fps: 20}, camera_2: {type: opencv, index_or_path: 'camera_2', width: 640, height: 480, fps: 20}, head: {type: opencv, index_or_path: 'head', width: 640, height: 480, fps: 20} }" `
  --teleop.type=xlerobot_vr `
  --teleop.xlevr_path=E:\lerobot\XLeRobot\XLeVR `
  --teleop.calibration_dir=E:\lerobot\calibration\teleoperators `
  --teleop.vr_connection_timeout=60 `
  --fps=20 `
  --display_data=false
