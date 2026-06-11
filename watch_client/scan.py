#!/usr/bin/env python3
"""BLE scan + GATT enumeration for the NoiseFit watch.

Step 0 of the crack plan: find the device, dump every service/characteristic,
identify the write + notify characteristics, and classify the stack.

Usage:
    python watch_client/scan.py                 # scan only, list nearby devices
    python watch_client/scan.py --name "NF 2"   # connect to first match, dump GATT
    python watch_client/scan.py --addr <UUID>   # connect by macOS CoreBluetooth UUID
"""
import argparse
import asyncio
import sys

from bleak import BleakClient, BleakScanner

# Default BLE advertised name (from device_info video: "NF 2_165A").
DEFAULT_NAME = "NF 2"

# Characteristic property -> short flag.
PROP_FLAGS = [
    "broadcast",
    "read",
    "write-without-response",
    "write",
    "notify",
    "indicate",
    "authenticated-signed-writes",
    "extended-properties",
]

# Known stack fingerprints (HANDOFF.md / FINDINGS.md §11).
KNOWN_SERVICE_HINTS = {
    "fe78": "Realtek / RealSil (maybe Creek)",
    "1530": "Nordic DFU",
    "1531": "Nordic DFU",
    "1532": "Nordic DFU",
    "ffd3": "JieLi",
    "ffd4": "JieLi",
    "ffd5": "JieLi",
}


def short_uuid(uuid: str) -> str:
    """Return the 16-bit short form if this is a standard Bluetooth UUID."""
    u = uuid.lower()
    if u.endswith("-0000-1000-8000-00805f9b34fb") and u.startswith("0000"):
        return u[4:8]
    return ""


async def scan(timeout: float):
    print(f"[*] Scanning {timeout:.0f}s for BLE devices...\n")
    devices = await BleakScanner.discover(timeout=timeout, return_adv=True)
    rows = []
    for addr, (dev, adv) in devices.items():
        name = adv.local_name or dev.name or "(no name)"
        rows.append((adv.rssi if adv.rssi is not None else -999, name, addr, adv))
    rows.sort(reverse=True)
    print(f"{'RSSI':>5}  {'NAME':<28}  ADDRESS")
    print("-" * 80)
    for rssi, name, addr, adv in rows:
        print(f"{rssi:>5}  {name:<28}  {addr}")
        if adv.service_uuids:
            for su in adv.service_uuids:
                hint = KNOWN_SERVICE_HINTS.get(short_uuid(su), "")
                tag = f"   <- {hint}" if hint else ""
                print(f"         adv svc: {su}{tag}")
        if adv.manufacturer_data:
            for cid, data in adv.manufacturer_data.items():
                print(f"         mfg 0x{cid:04x}: {data.hex()}")
    return rows


async def find_device(name: str, addr: str, timeout: float):
    if addr:
        print(f"[*] Looking for device by address {addr}...")
        dev = await BleakScanner.find_device_by_address(addr, timeout=timeout)
        return dev
    print(f"[*] Looking for device whose name contains '{name}'...")

    def match(d, adv):
        n = (adv.local_name or d.name or "")
        return name.lower() in n.lower()

    dev = await BleakScanner.find_device_by_filter(match, timeout=timeout)
    return dev


def classify(services) -> str:
    """Best-effort stack classification from the GATT table."""
    notes = []
    for svc in services:
        su = short_uuid(svc.uuid)
        if su in KNOWN_SERVICE_HINTS:
            notes.append(f"service {svc.uuid} => {KNOWN_SERVICE_HINTS[su]}")

    # PersimWear: a custom (128-bit) service with one write char + one notify char.
    for svc in services:
        if short_uuid(svc.uuid):
            continue  # standard service, skip
        has_write = False
        has_notify = False
        for ch in svc.characteristics:
            props = set(ch.properties)
            if {"write", "write-without-response"} & props:
                has_write = True
            if {"notify", "indicate"} & props:
                has_notify = True
        if has_write and has_notify:
            notes.append(
                f"custom UART-like service {svc.uuid} (write + notify) "
                "=> matches PersimWear MCF transport"
            )
    return "\n".join(notes) if notes else "no known stack fingerprint matched"


async def dump_gatt(dev, timeout: float):
    print(f"\n[+] Found: {dev}")
    print(f"[*] Connecting...")
    async with BleakClient(dev, timeout=timeout) as client:
        print(f"[+] Connected: {client.is_connected}")
        try:
            mtu = client.mtu_size
            print(f"[*] ATT MTU: {mtu}")
        except Exception:
            pass

        services = list(client.services)
        print(f"\n[*] {len(services)} GATT services:\n")
        write_chars = []
        notify_chars = []
        for svc in services:
            su = short_uuid(svc.uuid)
            su_tag = f" (0x{su})" if su else ""
            print(f"SERVICE {svc.uuid}{su_tag}")
            for ch in svc.characteristics:
                props = [p for p in PROP_FLAGS if p in ch.properties]
                cu = short_uuid(ch.uuid)
                cu_tag = f" (0x{cu})" if cu else ""
                print(f"  CHAR {ch.uuid}{cu_tag}  handle={ch.handle}")
                print(f"       props: {', '.join(props)}")
                if {"write", "write-without-response"} & set(ch.properties):
                    write_chars.append(ch)
                if {"notify", "indicate"} & set(ch.properties):
                    notify_chars.append(ch)
                for d in ch.descriptors:
                    du = short_uuid(d.uuid)
                    du_tag = f" (0x{du})" if du else ""
                    print(f"       descriptor {d.uuid}{du_tag} handle={d.handle}")
            print()

        print("=" * 80)
        print("SUMMARY")
        print("=" * 80)
        print("\nWrite-capable characteristics:")
        for ch in write_chars:
            print(f"  {ch.uuid}  ({', '.join(p for p in PROP_FLAGS if p in ch.properties)})")
        print("\nNotify/indicate characteristics:")
        for ch in notify_chars:
            print(f"  {ch.uuid}  ({', '.join(p for p in PROP_FLAGS if p in ch.properties)})")
        print("\nStack classification:")
        print(classify(services))


async def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--name", default=None, help="substring of device name to connect to")
    ap.add_argument("--addr", default=None, help="device address / CoreBluetooth UUID")
    ap.add_argument("--timeout", type=float, default=10.0, help="scan/connect timeout (s)")
    ap.add_argument("--scan-only", action="store_true", help="just list devices")
    args = ap.parse_args()

    if args.scan_only or (not args.name and not args.addr):
        rows = await scan(args.timeout)
        if not args.scan_only:
            print("\n[i] Re-run with --name \"NF 2\" (or --addr) to dump GATT.")
        return

    dev = await find_device(args.name or DEFAULT_NAME, args.addr, args.timeout)
    if dev is None:
        print("[!] Device not found. Is the watch on the pairing screen / advertising?")
        print("[!] Note: if already paired to the phone, it may not advertise.")
        sys.exit(1)
    await dump_gatt(dev, args.timeout)


if __name__ == "__main__":
    asyncio.run(main())
