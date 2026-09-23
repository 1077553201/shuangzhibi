from __future__ import annotations

import argparse

from lerobot.motors import Motor, MotorNormMode
from lerobot.motors.feetech import FeetechMotorsBus


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="/dev/ttyACM0")
    parser.add_argument("--start-id", type=int, default=1)
    parser.add_argument("--end-id", type=int, default=8)
    parser.add_argument("--retry", type=int, default=5)
    args = parser.parse_args()

    motors = {
        f"id{motor_id}": Motor(motor_id, "sts3215", MotorNormMode.RANGE_M100_100)
        for motor_id in range(args.start_id, args.end_id + 1)
    }
    bus = FeetechMotorsBus(port=args.port, motors=motors)
    bus.connect()
    print(f"connected {args.port}")
    try:
        bus.enable_torque(num_retry=args.retry)
        print(f"enable_torque ok with retry={args.retry}")
        for name in bus.motors:
            lock = bus.read("Lock", name, normalize=False)
            torque = bus.read("Torque_Enable", name, normalize=False)
            print(name, {"Lock": lock, "Torque_Enable": torque})
    finally:
        bus.disconnect(False)


if __name__ == "__main__":
    main()
