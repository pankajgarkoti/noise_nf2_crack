#!/usr/bin/env python3
"""Health / fitness time-series sync for the NF 2 (SEFitness protos).

Flow (from SEFunctionId + SEFitness):
  112 GET_FITNESS_TYPE_ID_LIST    -> watch returns fitness_type_id_list
  113 REQUEST_FITNESS_TYPE_ID     -> request one type; watch streams data msgs
  115 CONFIRM_FITNESS_TYPE_ID     -> ack a type as received
  123 FITNESS_DATA_RECEPTION_STATUS

Usage:
  python -m watch_client.health --addr <UUID>
"""
import argparse
import asyncio
import logging
import os

from . import pb_loader as pb
from .client import NF2Client

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "pb"))
import Fitness_pb2  # noqa: E402


def show(replies, label):
    print(f"\n=== {label} ===")
    for w in replies:
        if not hasattr(w, "id"):
            print(f"  raw {w!r}")
            continue
        which = w.WhichOneof("payload")
        line = f"  id={w.id} ({pb.fn_name(w.id)}) {which}"
        if which == "fitness":
            fp = w.fitness.WhichOneof("payload")
            line += f" fitness.{fp}"
        print(line)
        if which == "fitness":
            fp = w.fitness.WhichOneof("payload")
            sub = getattr(w.fitness, fp) if fp else None
            if sub is not None:
                txt = str(sub).strip().replace("\n", "\n      ")
                if txt:
                    print(f"      {txt[:600]}")
        if w.HasField("error_code"):
            print(f"    error_code={w.error_code}")


async def run(addr):
    async with NF2Client(addr) as c:
        await c.request(c.new(pb.SEWear.GET_DEVICE_INFO), timeout=5)

        # 1. get the list of fitness data types the watch holds
        replies = await c.request(c.new(pb.SEWear.GET_FITNESS_TYPE_ID_LIST),
                                  timeout=8, drain=2.0)
        show(replies, "GET_FITNESS_TYPE_ID_LIST (112)")

        types = []
        for w in replies:
            if hasattr(w, "HasField") and w.HasField("fitness") and \
                    w.fitness.WhichOneof("payload") == "fitness_type_id_list":
                for t in w.fitness.fitness_type_id_list.list:
                    types.append(t)
        print(f"\n[i] watch reports {len(types)} fitness type(s)")
        for t in types:
            print("   ", str(t).replace("\n", " "))

        # 2. request each type's data
        for t in types:
            req = c.new(pb.SEWear.REQUEST_FITNESS_TYPE_ID)
            req.fitness.fitness_type_id.CopyFrom(t)
            r = await c.request(req, timeout=8, drain=2.0)
            ftype = t.fitness_function_type if t.HasField("fitness_function_type") else "?"
            show(r, f"REQUEST_FITNESS_TYPE_ID type={ftype}")
            # confirm receipt
            cf = c.new(pb.SEWear.CONFIRM_FITNESS_TYPE_ID)
            cf.fitness.fitness_type_id.CopyFrom(t)
            await c.send(cf)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--addr", default="65DE7000-37BC-BEA2-347F-696ADCF6D742")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    asyncio.run(run(args.addr))


if __name__ == "__main__":
    main()
