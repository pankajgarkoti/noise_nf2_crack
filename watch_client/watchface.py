#!/usr/bin/env python3
"""Watch-face control for the NF 2.

Supported ops (SEFunctionId / SEWatchFace):
  80 GET_WATCH_FACE_LIST         -> watch_face_list
  81 SET_WATCH_FACE              -> id (switch active face); reply setting_result
  82 REMOVE_WATCH_FACE           -> id
  83 PREPARE_INSTALL_WATCH_FACE  -> watch_facePrepare_info {id,target_size,...}
                                    reply watchFace_result_info.prepare_status,
                                    then binary streams over f04, ends install_result

This module does the safe, reversible op (switch active face) to prove UI control,
and a dry-run PREPARE_INSTALL to probe the install handshake (no binary sent).

Usage:
  python -m watch_client.watchface --addr <UUID> --list
  python -m watch_client.watchface --addr <UUID> --set <FACE_ID>
  python -m watch_client.watchface --addr <UUID> --probe-install <FACE_ID> --size 100000
"""
import argparse
import asyncio
import logging
import os

from . import pb_loader as pb
from .client import NF2Client

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "pb"))
import WatchFace_pb2  # noqa: E402


def show(replies, label):
    print(f"\n=== {label} ===")
    for w in replies:
        if not hasattr(w, "id"):
            print(f"  raw {w!r}")
            continue
        which = w.WhichOneof("payload")
        print(f"  id={w.id} ({pb.fn_name(w.id)}) {which}")
        if which == "watch_face":
            fp = w.watch_face.WhichOneof("payload")
            sub = getattr(w.watch_face, fp) if fp else None
            print(f"    watch_face.{fp}: {str(sub).strip().replace(chr(10), ' ')[:400]}")
        if w.HasField("error_code"):
            print(f"    error_code={w.error_code}")


async def run(addr, args):
    async with NF2Client(addr) as c:
        await c.request(c.new(pb.SEWear.GET_DEVICE_INFO), timeout=5)

        if args.list or not (args.set or args.probe_install):
            r = await c.request(c.new(pb.SEWear.GET_WATCH_FACE_LIST), timeout=8)
            show(r, "GET_WATCH_FACE_LIST")

        if args.set:
            w = c.new(pb.SEWear.SET_WATCH_FACE)
            w.watch_face.id = args.set
            show(await c.request(w, timeout=8), f"SET_WATCH_FACE id={args.set}")

        if args.probe_install:
            w = c.new(pb.SEWear.PREPARE_INSTALL_WATCH_FACE)
            info = w.watch_face.watch_facePrepare_info
            info.id = args.probe_install
            info.target_file_size = args.size
            info.transfer_file_size = args.size
            show(await c.request(w, timeout=8, drain=2.0),
                 f"PREPARE_INSTALL_WATCH_FACE id={args.probe_install} size={args.size}")
            print("[i] (dry run: not streaming a binary over f04)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--addr", default="65DE7000-37BC-BEA2-347F-696ADCF6D742")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--set", default=None, help="face id to make active")
    ap.add_argument("--probe-install", default=None, help="face id for prepare-install dry run")
    ap.add_argument("--size", type=int, default=100000)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    asyncio.run(run(args.addr, args))


if __name__ == "__main__":
    main()
