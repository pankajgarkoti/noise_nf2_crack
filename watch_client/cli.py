#!/usr/bin/env python3
"""Interactive CLI for the NF 2 (ZH/Zhapp 'Apricot' protocol).

Connect to the watch and fire any SEWear command by name or number, with a few
built-in convenience commands. Replies are parsed and printed.

Usage:
  python -m watch_client.cli --addr <UUID>

At the prompt:
  help                      list built-in commands
  ops [substr]              list SEFunctionId opcodes (optionally filtered)
  send <NAME|num> [hex]     send opcode; optional raw payload appended as hex body
  info                      GET_DEVICE_INFO
  battery                   GET_DEVICE_BATTERY_STATUS
  faces                     GET_WATCH_FACE_LIST
  setface <id>              SET_WATCH_FACE
  buzz                      FIND_WEAR start (3s) then stop
  bindstatus                INQUIRY_BINDING_STATUS
  raw <hex>                 write raw bytes as a framed body to f02
  quit
"""
import argparse
import asyncio
import logging

from . import pb_loader as pb
from .client import NF2Client

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "pb"))
import MicroFunction_pb2  # noqa: E402


def list_ops(substr=None):
    enum = pb.SEWear.SEFunctionId
    out = []
    for name in enum.keys():
        if substr and substr.lower() not in name.lower():
            continue
        out.append((enum.Value(name), name))
    return sorted(out)


def show(replies):
    if not replies:
        print("  (no reply)")
        return
    for w in replies:
        if not hasattr(w, "id"):
            print(f"  raw {w!r}")
            continue
        which = w.WhichOneof("payload")
        print(f"  <- id={w.id} ({pb.fn_name(w.id)}) payload={which}")
        if which:
            sub = getattr(w, which)
            txt = str(sub).strip().replace("\n", "\n     ")
            if txt:
                print(f"     {txt[:800]}")


async def repl(addr):
    async with NF2Client(addr) as c:
        print(f"[+] connected to {addr}. Type 'help'.")
        loop = asyncio.get_running_loop()
        while True:
            line = await loop.run_in_executor(None, input, "nf2> ")
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            cmd = parts[0].lower()
            try:
                if cmd in ("quit", "exit", "q"):
                    break
                elif cmd == "help":
                    print(__doc__)
                elif cmd == "ops":
                    for num, name in list_ops(parts[1] if len(parts) > 1 else None):
                        print(f"  {num:5d}  {name}")
                elif cmd == "info":
                    show(await c.request(c.new(pb.SEWear.GET_DEVICE_INFO)))
                elif cmd == "battery":
                    show(await c.request(c.new(pb.SEWear.GET_DEVICE_BATTERY_STATUS)))
                elif cmd == "faces":
                    show(await c.request(c.new(pb.SEWear.GET_WATCH_FACE_LIST)))
                elif cmd == "setface" and len(parts) > 1:
                    w = c.new(pb.SEWear.SET_WATCH_FACE)
                    w.watch_face.id = parts[1]
                    show(await c.request(w))
                elif cmd == "bindstatus":
                    w = c.new(pb.SEWear.INQUIRY_BINDING_STATUS)
                    w.bind_account.request_binding_status = True
                    show(await c.request(w))
                elif cmd == "buzz":
                    w = c.new(pb.SEWear.FIND_WEAR)
                    w.micro_function.find_phone_mode = MicroFunction_pb2.FIND_START
                    show(await c.request(w))
                    await asyncio.sleep(3)
                    w2 = c.new(pb.SEWear.FIND_WEAR)
                    w2.micro_function.find_phone_mode = MicroFunction_pb2.FIND_STOP
                    show(await c.request(w2))
                elif cmd == "send" and len(parts) > 1:
                    key = parts[1]
                    fid = (pb.SEWear.SEFunctionId.Value(key)
                           if not key.isdigit() else int(key))
                    w = c.new(fid)
                    body = w.SerializeToString()
                    if len(parts) > 2:  # append raw hex to the body
                        body += bytes.fromhex(parts[2])
                    await c.t.send_body(body)
                    show([await c.recv() for _ in range(1)])
                elif cmd == "raw" and len(parts) > 1:
                    await c.t.send_body(bytes.fromhex(parts[1]))
                    show([await c.recv()])
                else:
                    print("  ? unknown command; 'help'")
            except Exception as e:
                print(f"  ! error: {e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--addr", default="65DE7000-37BC-BEA2-347F-696ADCF6D742")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.ERROR,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    asyncio.run(repl(args.addr))


if __name__ == "__main__":
    main()
