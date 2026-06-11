#!/usr/bin/env python3
"""Factory / diagnostic channel for the NF 2 (SEFactory payloads).

Exercises the developer/factory opcodes to pull internal log files and raw
sensor data:
  4101 PHONE_REQUEST_LOG_UPDATE       -> factory.log_respond
  4109 PHONE_REQUEST_FILE_INFO_UPDATE -> trigger file push
  4098 PHONE_SEND_HEART_RATE_SWITCH   -> factory.heart_rate_switch (raw HR)
  4107 PHONE_SEND_GSENSOR...          -> raw accel/geomagnetic
  4097 FILE_INFO_UPDATE (incoming)    -> factory.file SEFileInfo

Usage:
  python -m watch_client.factory --addr <UUID> --action logs|files|hr|gsensor
"""
import argparse
import asyncio
import logging
import os

from . import pb_loader as pb
from .client import NF2Client

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "pb"))
import Factory_pb2  # noqa: E402


def show(replies, label):
    print(f"\n=== {label} ===")
    for w in replies:
        if not hasattr(w, "id"):
            print(f"  raw {w!r}")
            continue
        which = w.WhichOneof("payload")
        extra = ""
        if which == "factory":
            fp = w.factory.WhichOneof("payload")
            extra = f" factory.{fp}"
            if fp == "file":
                fi = w.factory.file
                extra += f" name={fi.file_name!r} +{len(fi.data)}B state={fi.file_state if fi.HasField('file_state') else '-'}"
        print(f"  id={w.id} ({pb.fn_name(w.id)}) {which}{extra}")
        if w.HasField("error_code"):
            print(f"    error_code={w.error_code}")


async def collect_files(c, secs, outdir):
    os.makedirs(outdir, exist_ok=True)
    files = {}
    states = {}
    loop = asyncio.get_running_loop()
    end = loop.time() + secs
    while loop.time() < end:
        w = await c.recv(timeout=2.0)
        if w is None or not hasattr(w, "id"):
            continue
        if w.id == pb.SEWear.FILE_INFO_UPDATE and w.HasField("factory") \
                and w.factory.WhichOneof("payload") == "file":
            fi = w.factory.file
            buf = files.setdefault(fi.file_name, bytearray())
            buf.extend(fi.data)
            st = fi.file_state if fi.HasField("file_state") else -1
            states.setdefault(fi.file_name, []).append(st)
            print(f"  file={fi.file_name!r} +{len(fi.data)}B state={st} total={len(buf)}")
            # ack the file push so the watch advances
            ack = c.new(pb.SEWear.PHONE_REQUEST_FILE_INFO_UPDATE)
            ack.factory.log_respond = Factory_pb2.START_LOG_UPDATE
            await c.send(ack)
        else:
            show([w], "incoming")
    for name, buf in files.items():
        path = os.path.join(outdir, name.replace("/", "_"))
        with open(path, "wb") as fh:
            fh.write(buf)
        print(f"  saved {name}: {len(buf)}B states={states[name]} -> {path}")
        try:
            print("   ", buf[:120].decode("utf-8", "replace").replace("\n", " "))
        except Exception:
            pass


async def run(addr, action, secs, outdir):
    async with NF2Client(addr) as c:
        await c.request(c.new(pb.SEWear.GET_DEVICE_INFO), timeout=5)

        if action in ("logs", "files"):
            # request log/file update
            w = c.new(pb.SEWear.PHONE_REQUEST_LOG_UPDATE)
            w.factory.log_respond = Factory_pb2.START_LOG_UPDATE
            show(await c.request(w, timeout=5, drain=1.0), "PHONE_REQUEST_LOG_UPDATE")

            w2 = c.new(pb.SEWear.PHONE_REQUEST_FILE_INFO_UPDATE)
            w2.factory.log_respond = Factory_pb2.START_LOG_UPDATE
            await c.send(w2)
            print(f"\n[*] collecting files for {secs}s ...")
            await collect_files(c, secs, outdir)

        elif action == "hr":
            # turn on raw heart-rate sensor streaming
            w = c.new(pb.SEWear.PHONE_SEND_HEART_RATE_SWITCH)
            w.factory.heart_rate_switch = Factory_pb2.HEART_RATE_START
            show(await c.request(w, timeout=5), "HEART_RATE_START")
            print(f"\n[*] streaming raw HR for {secs}s ...")
            loop = asyncio.get_running_loop()
            end = loop.time() + secs
            while loop.time() < end:
                x = await c.recv(timeout=2.0)
                if x is not None and hasattr(x, "id"):
                    show([x], "hr-stream")
            w2 = c.new(pb.SEWear.PHONE_SEND_HEART_RATE_SWITCH)
            w2.factory.heart_rate_switch = Factory_pb2.HEART_RATE_STOP
            show(await c.request(w2, timeout=5), "HEART_RATE_STOP")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--addr", default="65DE7000-37BC-BEA2-347F-696ADCF6D742")
    ap.add_argument("--action", default="logs", choices=["logs", "files", "hr", "gsensor"])
    ap.add_argument("--secs", type=float, default=20)
    ap.add_argument("-o", "--out", default="dumps")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    asyncio.run(run(args.addr, args.action, args.secs, args.out))


if __name__ == "__main__":
    main()
