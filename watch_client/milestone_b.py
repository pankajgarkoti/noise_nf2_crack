#!/usr/bin/env python3
"""Milestone B: exercise real commands on the (now bound) NF 2.

Demonstrates full device control: read battery, get watch-face list, set system
time, and physically buzz the watch (FIND_WEAR). All over our own client.

Usage:
  python -m watch_client.milestone_b --addr <UUID> [--buzz]
"""
import argparse
import asyncio
import logging
import time

from . import pb_loader as pb
from .client import NF2Client

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "pb"))
import MicroFunction_pb2  # noqa: E402
import SystemTime_pb2  # noqa: E402


def show(replies, label):
    print(f"\n=== {label} ===")
    for w in replies:
        if not hasattr(w, "id"):
            print(f"  raw: {w!r}")
            continue
        which = w.WhichOneof("payload")
        print(f"  id={w.id} ({pb.fn_name(w.id)}) payload={which}")
        if which and which not in ("error_code",):
            sub = getattr(w, which)
            txt = str(sub).strip().replace("\n", "\n    ")
            if txt:
                print(f"    {txt}")
        if w.HasField("error_code"):
            print(f"    error_code={w.error_code}")


async def run(addr, do_buzz, do_settime):
    async with NF2Client(addr) as c:
        # battery
        show(await c.request(c.new(pb.SEWear.GET_DEVICE_BATTERY_STATUS), timeout=6),
             "GET_DEVICE_BATTERY_STATUS (33)")

        # watch face list
        show(await c.request(c.new(pb.SEWear.GET_WATCH_FACE_LIST), timeout=8),
             "GET_WATCH_FACE_LIST (80)")

        # application list (what apps are on the watch)
        show(await c.request(c.new(pb.SEWear.GET_APPLICATION_LIST), timeout=8),
             "GET_APPLICATION_LIST (169)")

        if do_settime:
            w = c.new(pb.SEWear.SET_SYSTEM_TIME)
            ts = w.system_time.time_set
            ts.timestamp = int(time.time())
            ts.offset = -time.timezone if time.daylight == 0 else -time.altzone
            ts.time_format = True  # 24h
            show(await c.request(w, timeout=6), "SET_SYSTEM_TIME (48)")

        if do_buzz:
            print("\n[*] BUZZING the watch (FIND_WEAR). Watch should vibrate/ring...")
            w = c.new(pb.SEWear.FIND_WEAR)
            w.micro_function.find_phone_mode = MicroFunction_pb2.FIND_START
            show(await c.request(w, timeout=6), "FIND_WEAR start (161)")
            await asyncio.sleep(3)
            w2 = c.new(pb.SEWear.FIND_WEAR)
            w2.micro_function.find_phone_mode = MicroFunction_pb2.FIND_STOP
            show(await c.request(w2, timeout=6), "FIND_WEAR stop (161)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--addr", default="65DE7000-37BC-BEA2-347F-696ADCF6D742")
    ap.add_argument("--buzz", action="store_true", help="physically vibrate the watch")
    ap.add_argument("--settime", action="store_true", help="set the watch clock")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    asyncio.run(run(args.addr, args.buzz, args.settime))


if __name__ == "__main__":
    main()
