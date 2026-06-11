#!/usr/bin/env python3
"""Properly parse a .dex string pool and recover protobuf FileDescriptorProtos.

dex layout: header has string_ids_size / string_ids_off. Each string_id is a
4-byte offset to a string_data_item: uleb128 utf16_size, then MUTF-8 bytes,
then a 0x00 terminator. We decode MUTF-8 -> the original descriptor bytes
(latin-1 round-trip, since protoc emits ISO-8859-1) and try ParseFromString as
FileDescriptorProto.

Usage: python tools/dex_protos.py classes7.dex -o out_dir [--want Device.proto ...]
"""
import argparse
import os
import struct
import sys

from google.protobuf import descriptor_pb2

sys.path.insert(0, os.path.dirname(__file__))
from extract_protos import fdp_to_proto  # noqa: E402


def read_uleb128(data, off):
    result = 0
    shift = 0
    while True:
        b = data[off]
        off += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            break
        shift += 7
    return result, off


def mutf8_to_bytes(data, off):
    """Decode a MUTF-8 string_data_item body back into the original byte sequence.
    Returns (raw_bytes, next_off). We map each decoded code point's low 8 bits
    to a byte (descriptors are ISO-8859-1, so code points are 0..255)."""
    utf16_size, off = read_uleb128(data, off)
    out = bytearray()
    while True:
        b = data[off]
        if b == 0x00:
            off += 1
            break
        if b < 0x80:
            out.append(b)
            off += 1
        elif (b & 0xE0) == 0xC0:
            b2 = data[off + 1]
            cp = ((b & 0x1F) << 6) | (b2 & 0x3F)
            out.append(cp & 0xFF)
            off += 2
        elif (b & 0xF0) == 0xE0:
            b2 = data[off + 1]
            b3 = data[off + 2]
            cp = ((b & 0x0F) << 12) | ((b2 & 0x3F) << 6) | (b3 & 0x3F)
            out.append(cp & 0xFF)
            off += 3
        else:
            out.append(b)
            off += 1
    return bytes(out), off


def iter_dex_strings(data):
    magic = data[:8]
    if magic[:4] != b"dex\n":
        raise ValueError("not a dex file")
    string_ids_size = struct.unpack_from("<I", data, 0x38)[0]
    string_ids_off = struct.unpack_from("<I", data, 0x3C)[0]
    for i in range(string_ids_size):
        data_off = struct.unpack_from("<I", data, string_ids_off + i * 4)[0]
        try:
            raw, _ = mutf8_to_bytes(data, data_off)
        except Exception:
            continue
        yield raw


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dex", nargs="+")
    ap.add_argument("-o", "--out", default="protos")
    ap.add_argument("--want", nargs="*", default=None)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    seen = {}
    for path in args.dex:
        with open(path, "rb") as fh:
            data = fh.read()
        for raw in iter_dex_strings(data):
            if len(raw) < 12 or raw[0] != 0x0A:
                continue
            # quick check: looks like it starts with name field
            fdp = descriptor_pb2.FileDescriptorProto()
            try:
                fdp.ParseFromString(raw)
            except Exception:
                continue
            if not fdp.name.endswith(".proto"):
                continue
            if not (fdp.message_type or fdp.enum_type):
                continue
            if args.want and fdp.name not in args.want:
                continue
            n = len(fdp.message_type) + len(fdp.enum_type)
            if fdp.name in seen and seen[fdp.name][0] >= n:
                continue
            seen[fdp.name] = (n, fdp, os.path.basename(path))

    for name, (n, fdp, src) in sorted(seen.items()):
        out_path = os.path.join(args.out, os.path.basename(name))
        with open(out_path, "w") as fh:
            fh.write(fdp_to_proto(fdp))
        print(f"[+] {name:26s} <- {src:14s} pkg={fdp.package or '-':28s} "
              f"({len(fdp.message_type)} msgs, {len(fdp.enum_type)} enums)")
    print(f"\n[=] wrote {len(seen)} proto(s) to {args.out}/")


if __name__ == "__main__":
    main()
