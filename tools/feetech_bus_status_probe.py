from __future__ import annotations

import argparse

from lerobot.motors import Motor, MotorNormMode
from lerobot.motors.feetech import FeetechMotorsBus


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="/dev/ttyACM0")
    parser.add_argument("--start-id", type=int, default=1)
    parser.add_argument("--end-id", type=int, default=8)
    args = parser.parse_args()

    motors = {
        f"id{motor_id}": Motor(motor_id, "sts3215", MotorNormMode.RANGE_M100_100)
        for motor_id in range(args.start_id, args.end_id + 1)
    }
    bus = FeetechMotorsBus(port=args.port, motors=motors)
    bus.connect()
    print(f"connected {args.port}")
    try:
        for name in bus.motors:
            vals = {}
            for reg in [
                "Response_Status_Level",
                "Lock",
                "Torque_Enable",
                "Operating_Mode",
                "Present_Position",
            ]:
                try:
                    vals[reg] = bus.read(reg, name, normalize=False)
                except Exception as exc:
                    vals[reg] = f"ERR: {exc}"
            print(name, vals)
    finally:
        bus.disconnect(False)


if __name__ == "__main__":
    main()
