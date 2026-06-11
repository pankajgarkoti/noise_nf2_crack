#!/usr/bin/env python3
"""Capture the SEFileInfo files the NF 2 pushes via FILE_INFO_UPDATE (id=4097).

The watch streams internal diagnostic/log files as SEWear{factory.file =
SEFileInfo{file_name, data, file_state}}. We collect them, group by file_name,
and write to disk. file_state likely encodes start/continue/end of a multi-chunk
file (observed values printed so we can decode it).

Usage:
  python -m watch_client.pull_files --addr <UUID> --secs 30 -o dumps/
"""
import argparse
import asyncio
import logging
import os

from . import pb_loader as pb
from .client import NF2Client


async def run(addr, secs, outdir):
    os.makedirs(outdir, exist_ok=True)
    files: dict[str, bytearray] = {}
    states: dict[str, list[int]] = {}

    async with NF2Client(addr) as c:
        # nudge the watch: ask for device info to get it talking, then just listen
        await c.send(c.new(pb.SEWear.GET_DEVICE_INFO))
        loop = asyncio.get_running_loop()
        end = loop.time() + secs
        seen = 0
        while loop.time() < end:
            w = await c.recv(timeout=2.0)
            if w is None or not hasattr(w, "id"):
                continue
            if w.id != pb.SEWear.FILE_INFO_UPDATE:
                continue
            if not w.HasField("factory") or w.factory.WhichOneof("payload") != "file":
                print(f"  factory payload = {w.factory.WhichOneof('payload')}")
                continue
            fi = w.factory.file
            seen += 1
            buf = files.setdefault(fi.file_name, bytearray())
            buf.extend(fi.data)
            st = fi.file_state if fi.HasField("file_state") else -1
            states.setdefault(fi.file_name, []).append(st)
            print(f"  [{seen}] file={fi.file_name!r} +{len(fi.data)}B "
                  f"state={st} total={len(buf)}B")

    print(f"\n=== captured {len(files)} file(s) ===")
    for name, buf in files.items():
        safe = name.replace("/", "_")
        path = os.path.join(outdir, safe)
        with open(path, "wb") as fh:
            fh.write(buf)
        print(f"  {name}: {len(buf)} bytes, states={states[name]} -> {path}")
        # preview
        try:
            txt = buf.decode("utf-8", "replace")
            print("    preview:", txt[:160].replace("\n", " "))
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--addr", default="65DE7000-37BC-BEA2-347F-696ADCF6D742")
    ap.add_argument("--secs", type=float, default=30)
    ap.add_argument("-o", "--out", default="dumps")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    asyncio.run(run(args.addr, args.secs, args.out))


if __name__ == "__main__":
    main()
