#!/usr/bin/env python3
"""Milestone A: connect to the NF 2 and send GET_DEVICE_INFO (id=32).

This validates the whole stack: BLE transport, MCF-style framing, protobuf
encode/decode. GET_DEVICE_INFO works pre-bind, so it's the safest first probe.

Usage:
  python -m watch_client.milestone_a --addr 65DE7000-37BC-BEA2-347F-696ADCF6D742
  python -m watch_client.milestone_a            # auto-scan for "NF 2"
"""
import argparse
import asyncio
import logging

from bleak import BleakScanner

from . import pb_loader as pb
from .transport import NF2Transport

DEFAULT_ADDR = "65DE7000-37BC-BEA2-347F-696ADCF6D742"


async def find_addr(name="NF 2", timeout=12.0):
    def match(d, adv):
        n = adv.local_name or d.name or ""
        return name.lower() in n.lower()
    dev = await BleakScanner.find_device_by_filter(match, timeout=timeout)
    return dev.address if dev else None


def describe_device_info(w) -> str:
    """Pretty-print a SEWear carrying a device payload."""
    lines = [f"id={w.id} ({pb.fn_name(w.id)})  payload={w.WhichOneof('payload')}"]
    if w.HasField("device"):
        dev = w.device
        which = dev.WhichOneof("payload")
        lines.append(f"  device.{which}:")
        sub = getattr(dev, which) if which else None
        if sub is not None:
            lines.append("    " + str(sub).replace("\n", "\n    ").rstrip())
    if w.HasField("error_code"):
        lines.append(f"  error_code = {w.error_code}")
    return "\n".join(lines)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--addr", default=None)
    ap.add_argument("--timeout", type=float, default=8.0)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    addr = args.addr or DEFAULT_ADDR
    if addr is None:
        print("[*] scanning for NF 2 ...")
        addr = await find_addr()
        if not addr:
            print("[!] NF 2 not found")
            return

    body = pb.build(pb.SEWear.GET_DEVICE_INFO)
    print(f"[*] GET_DEVICE_INFO body = {body.hex()}")

    async with NF2Transport(addr) as t:
        await t.send_body(body)
        print("[*] waiting for reply ...")
        reply = await t.recv_message(timeout=args.timeout)
        if reply is None:
            print("[!] no reply within timeout")
            print("[i] raw notifications seen:")
            for ch, b in t.drain_raw():
                print(f"    {ch} {b.hex()}")
            return
        print(f"[+] reply body = {reply.hex()}")
        try:
            w = pb.parse_wear(reply)
            print("[+] parsed SEWear:")
            print(describe_device_info(w))
        except Exception as e:
            print(f"[!] parse failed: {e}")

        # drain any extra frames (battery sub-status, etc.)
        extra = await t.recv_message(timeout=2.0)
        if extra:
            print(f"[+] extra reply = {extra.hex()}")
            try:
                print(describe_device_info(pb.parse_wear(extra)))
            except Exception:
                pass


if __name__ == "__main__":
    asyncio.run(main())
