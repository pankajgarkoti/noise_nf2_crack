#!/usr/bin/env python3
"""Bind/pair flow for the NF 2 (Apricot protocol).

High-level order (from ZhConnectHandler):
  1. INQUIRY_BINDING_STATUS (16)  -> watch reports bound/unbound
  2. BINDING_CHECK (17)           -> watch replies SEBindCheck {device_verify,
                                     bind_random_key, mac, serial, fw, name,
                                     bind_check_result}; may prompt on screen
  3. (user confirms on watch)     -> watch sends BINDING_RESULT/SEBindCheck SUCCESS
  4. BINDING_RESULT (18) with bind_result.user_id  -> register the account token

The exact phone-side payload for step 2 is inside the non-decompiled
ControlBleTools, so we probe candidate payloads and watch the replies.

Usage:
  python -m watch_client.bind --addr <UUID> [--step probe|inquiry|check|full]
"""
import argparse
import asyncio
import logging
import random
import uuid

from . import pb_loader as pb
from .client import NF2Client

# proto submessage modules
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "pb"))
import BindAccount_pb2  # noqa: E402


def make_user_id(app_user_id: str = "0") -> str:
    """Mirror ZhConnectHandler user_id formula: substring(30) of
    randomUUID + randomInt(10..9999) + appUserId."""
    s = f"{uuid.uuid4()}{random.randint(10, 9999)}{app_user_id}"
    return s[30:]


def describe_bind_check(sbc) -> str:
    return (f"device_verify={sbc.device_verify} "
            f"result={BindAccount_pb2.SEBindCheck.SEBindCheckResult.Name(sbc.bind_check_result) if sbc.HasField('bind_check_result') else '-'} "
            f"random_key={sbc.bind_random_key!r} mac={sbc.mac!r} "
            f"serial={sbc.serial_number!r} dev_no={sbc.device_number!r} "
            f"fw={sbc.firmware_version!r} name={sbc.device_name!r}")


def dump(replies, label):
    print(f"\n=== {label}: {len(replies)} reply(ies) ===")
    for w in replies:
        if not hasattr(w, "id"):
            print(f"  raw: {w.hex() if isinstance(w, bytes) else w}")
            continue
        print(f"  id={w.id} ({pb.fn_name(w.id)}) payload={w.WhichOneof('payload')}")
        if w.HasField("bind_account"):
            ba = w.bind_account
            which = ba.WhichOneof("payload")
            print(f"    bind_account.{which}")
            if which == "bind_check":
                print("    " + describe_bind_check(ba.bind_check))
            elif which == "verify_result":
                print(f"    verify_result_type={ba.verify_result.verify_result_type} "
                      f"binding_status={ba.verify_result.binding_status}")
            else:
                print("    " + str(getattr(ba, which)).replace("\n", " "))
        if w.HasField("error_code"):
            print(f"    error_code={w.error_code}")


async def run(addr, step):
    async with NF2Client(addr) as c:
        # Always start with device info (matches SDK warmup, harmless)
        dev = await c.request(c.new(pb.SEWear.GET_DEVICE_INFO), timeout=6)
        dump(dev, "GET_DEVICE_INFO")

        if step in ("inquiry", "probe", "full"):
            # INQUIRY_BINDING_STATUS: try bind_account.request_binding_status=true
            w = c.new(pb.SEWear.INQUIRY_BINDING_STATUS)
            w.bind_account.request_binding_status = True
            r = await c.request(w, timeout=6)
            dump(r, "INQUIRY_BINDING_STATUS (request_binding_status=true)")

        if step in ("check", "probe"):
            # BINDING_CHECK with empty bind_account is the confirmed trigger.
            w = c.new(pb.SEWear.BINDING_CHECK)
            w.bind_account.SetInParent()
            r = await c.request(w, timeout=6, drain=2.0)
            dump(r, "BINDING_CHECK [empty bind_account]")
            print("\n[*] Look at the watch screen NOW. Holding 20s to observe any prompt...")
            for _ in range(20):
                extra = await c.recv(timeout=1.0)
                if extra is not None:
                    dump([extra], "async-during-hold")

        if step == "full":
            # 1. BINDING_CHECK -> get SEBindCheck (device identity + device_verify)
            chk = c.new(pb.SEWear.BINDING_CHECK)
            chk.bind_account.SetInParent()
            r = await c.request(chk, timeout=6, drain=2.0)
            dump(r, "BINDING_CHECK")

            # 2. BINDING_RESULT with our generated user_id -> should trigger the
            #    on-watch confirmation prompt.
            uid = make_user_id()
            print(f"\n[*] sending BINDING_RESULT user_id={uid!r}")
            res = c.new(pb.SEWear.BINDING_RESULT)
            res.bind_account.bind_result.user_id = uid
            res.bind_account.bind_result.bind_result_type = BindAccount_pb2.SEBindResult.SUCCESS
            res.bind_account.bind_result.phone_type = BindAccount_pb2.SEBindResult.ANDROID
            rr = await c.request(res, timeout=6, drain=2.0)
            dump(rr, "BINDING_RESULT sent")

            print("\n[*] ACCEPT the pairing on the watch now if it prompts.")
            print("[*] Watching 40s for confirmation / verify_result / re-check ...")
            bound = False
            for _ in range(40):
                w = await c.recv(timeout=1.0)
                if w is None:
                    continue
                if getattr(w, "id", None) == pb.SEWear.FILE_INFO_UPDATE:
                    continue  # noise
                dump([w], "async")
                if hasattr(w, "HasField") and w.HasField("bind_account"):
                    ba = w.bind_account
                    which = ba.WhichOneof("payload")
                    if which == "bind_check" and ba.bind_check.HasField("bind_check_result"):
                        if ba.bind_check.bind_check_result == BindAccount_pb2.SEBindCheck.SUCCESS:
                            print("[+] BIND SUCCESS (bind_check_result=SUCCESS)")
                            bound = True
                    if which == "verify_result" and ba.verify_result.verify_result_type:
                        print("[+] verify_result_type=True")

            # 3. confirm with INQUIRY_BINDING_STATUS
            q = c.new(pb.SEWear.INQUIRY_BINDING_STATUS)
            q.bind_account.request_binding_status = True
            qr = await c.request(q, timeout=6)
            dump(qr, "INQUIRY_BINDING_STATUS (final)")
            for w in qr:
                if hasattr(w, "HasField") and w.HasField("bind_account") and \
                        w.bind_account.WhichOneof("payload") == "request_binding_status":
                    print(f"[=] bound = {w.bind_account.request_binding_status}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--addr", default="65DE7000-37BC-BEA2-347F-696ADCF6D742")
    ap.add_argument("--step", default="probe",
                    choices=["inquiry", "check", "probe", "full"])
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    asyncio.run(run(args.addr, args.step))


if __name__ == "__main__":
    main()
