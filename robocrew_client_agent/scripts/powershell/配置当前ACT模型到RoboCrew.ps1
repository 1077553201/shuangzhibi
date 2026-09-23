$ErrorActionPreference = "Stop"

# 这个脚本只做配置，不启动机器人。
# 作用：把当前训练好的 ACT checkpoint 写入 RoboCrew 默认 VLA 工具配置。

$policyPath = "E:\lerobot\outputs\train\xlerobot_coke_can_to_shelf_act_smoke_20260503_204142\checkpoints\000200\pretrained_model"
$targetDir = Join-Path $env:USERPROFILE ".cache\robocrew\tools"
$targetFile = Join-Path $targetDir "vla_tools.json"

if (-not (Test-Path $policyPath)) {
    throw "没有找到 policy 路径：$policyPath"
}

New-Item -ItemType Directory -Force $targetDir | Out-Null

$toolConfig = @(
    @{
        tool_name = "Place_coke_can_on_shelf_right_arm"
        tool_description = "使用右臂把桌面上的红色可口可乐空罐放到机器人置物架上。"
        task_prompt = "Pick up the red Coca-Cola can from the table and place it on the robot shelf."
        server_address = "127.0.0.1:8080"
        policy_name = $policyPath
        policy_type = "act"
        policy_device = "cuda"
        arm_port = "/dev/arm_right"
        execution_time = 10
        active = $true
    }
)

$toolConfig |
    ConvertTo-Json -Depth 8 |
    Set-Content -Path $targetFile -Encoding UTF8

Write-Host "[完成] 已写入 RoboCrew VLA 工具配置：$targetFile"
Write-Host "[模型] $policyPath"
Write-Host "[提醒] 当前模型只训练到 200 step，只适合低风险观察趋势，不代表最终效果。"
