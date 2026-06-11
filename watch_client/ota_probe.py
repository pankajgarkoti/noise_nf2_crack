#!/usr/bin/env python3
"""OTA path PROBE for the NF 2 -- READ-ONLY. Never streams firmware.

SAFETY: This module only sends the PREPARE_OTA handshake and read-only state
queries. It deliberately does NOT send REQUEST_ONLINE/REQUEST_OFFLINE with a
real image, and never writes to the f04 OTA data channel. The goal is to map the
firmware-update handshake (what prepare_status the watch returns, breakpoint /
AGPS state) without any risk of flashing.

Opcodes:
  144 PREPARE_OTA                         -> large_file.prepare_ota_request
                                             reply large_file.prepare_ota_response
  147 REQUEST_AGPS_STATE                  (read-only)
  148 REQUEST_BREAKPOINT_CONTINUATION_STATE (read-only)

Usage:
  python -m watch_client.ota_probe --addr <UUID> [--fw 1.0.2] [--type ALL|ROM|RES]
"""
import argparse
import asyncio
import logging
import os

from . import pb_loader as pb
from .client import NF2Client

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "pb"))
import LargeFile_pb2  # noqa: E402
import Common_pb2  # noqa: E402


def status_name(s):
    try:
        return Common_pb2.SEPrepareStatus.Name(s)
    except Exception:
        return str(s)


def show(replies, label):
    print(f"\n=== {label} ===")
    for w in replies:
        if not hasattr(w, "id"):
            print(f"  raw {w!r}")
            continue
        which = w.WhichOneof("payload")
        print(f"  id={w.id} ({pb.fn_name(w.id)}) {which}")
        if which == "large_file":
            fp = w.large_file.WhichOneof("payload")
            sub = getattr(w.large_file, fp) if fp else None
            extra = ""
            if fp == "prepare_ota_response":
                extra = f" prepare_status={status_name(sub.prepare_status)}"
            print(f"    large_file.{fp}{extra}: {str(sub).strip().replace(chr(10), ' ')[:300]}")
        if w.HasField("error_code"):
            print(f"    error_code={w.error_code}")


async def run(addr, fw, ftype):
    async with NF2Client(addr) as c:
        info = await c.request(c.new(pb.SEWear.GET_DEVICE_INFO), timeout=5)
        show(info, "GET_DEVICE_INFO")

        # READ-ONLY: breakpoint continuation state
        show(await c.request(c.new(pb.SEWear.REQUEST_BREAKPOINT_CONTINUATION_STATE),
                             timeout=6),
             "REQUEST_BREAKPOINT_CONTINUATION_STATE (148)")

        # READ-ONLY: AGPS state
        show(await c.request(c.new(pb.SEWear.REQUEST_AGPS_STATE), timeout=6),
             "REQUEST_AGPS_STATE (147)")

        # PREPARE_OTA handshake (no force, current/declared version). The watch
        # only EVALUATES and replies a prepare_status; nothing is flashed.
        w = c.new(pb.SEWear.PREPARE_OTA)
        req = w.large_file.prepare_ota_request
        req.force = False
        req.type = getattr(LargeFile_pb2, ftype)
        req.firmware_version = fw
        show(await c.request(w, timeout=8, drain=2.0),
             f"PREPARE_OTA force=False type={ftype} fw={fw}")
        print("\n[i] PROBE ONLY: no firmware binary sent, no f04 writes performed.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--addr", default="65DE7000-37BC-BEA2-347F-696ADCF6D742")
    ap.add_argument("--fw", default="1.0.2")
    ap.add_argument("--type", default="ALL", choices=["ALL", "ROM", "RES"])
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    asyncio.run(run(args.addr, args.fw, args.type))


if __name__ == "__main__":
    main()
