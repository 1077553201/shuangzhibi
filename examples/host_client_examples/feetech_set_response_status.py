from __future__ import annotations

import argparse

from lerobot.motors import Motor, MotorNormMode
from lerobot.motors.feetech import FeetechMotorsBus


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="/dev/ttyACM0")
    parser.add_argument("--start-id", type=int, default=1)
    parser.add_argument("--end-id", type=int, default=8)
    parser.add_argument("--level", type=int, default=2)
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
            before = bus.read("Response_Status_Level", name, normalize=False)
            bus.write("Lock", name, 0, normalize=False, num_retry=3)
            bus.write("Response_Status_Level", name, args.level, normalize=False, num_retry=3)
            after = bus.read("Response_Status_Level", name, normalize=False)
            print(name, before, "->", after)
    finally:
        bus.disconnect(False)


if __name__ == "__main__":
    main()
