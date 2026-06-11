#!/usr/bin/env python3
"""Recover .proto files from jadx-decompiled generated protobuf Java.

protobuf-java embeds the serialized FileDescriptorProto as a Java string
literal passed to Descriptors.FileDescriptor.internalBuildGeneratedFileFrom(
    new String[]{"<descriptor bytes as a java string>"}, ...).

We:
  1. find that string literal,
  2. decode the Java-string escapes back to the original *bytes* (each char's
     low byte -> one byte; protoc emits the descriptor as ISO-8859-1 chars),
  3. parse it as descriptor_pb2.FileDescriptorProto,
  4. pretty-print it as a .proto file.

Usage:
  python tools/extract_protos.py <Foo Protos.java> [more.java ...] -o out_dir
  python tools/extract_protos.py --glob 'extracted/.../*.java' -o out_dir
"""
import argparse
import glob
import os
import re
import sys

from google.protobuf import descriptor_pb2

CALL_RE = re.compile(
    r"internalBuildGeneratedFileFrom\s*\(\s*new\s+String\s*\[\s*\]\s*\{",
    re.S,
)


def find_string_array(java: str):
    """Return list of decoded-byte blobs, one per internalBuildGeneratedFileFrom call."""
    blobs = []
    for m in CALL_RE.finditer(java):
        i = m.end()  # just after the '{'
        # Collect consecutive "..." string literals up to the closing '}'.
        parts = []
        while True:
            # skip whitespace and commas
            while i < len(java) and java[i] in " \t\r\n,":
                i += 1
            if i >= len(java) or java[i] != '"':
                break
            # parse one string literal
            i += 1
            buf = []
            while i < len(java):
                c = java[i]
                if c == "\\":
                    esc = java[i + 1]
                    if esc == "u":
                        code = int(java[i + 2 : i + 6], 16)
                        buf.append(code & 0xFF)
                        i += 6
                        continue
                    simple = {
                        "n": 0x0A,
                        "t": 0x09,
                        "r": 0x0D,
                        "b": 0x08,
                        "f": 0x0C,
                        '"': 0x22,
                        "'": 0x27,
                        "\\": 0x5C,
                        "0": 0x00,
                    }
                    if esc in simple:
                        buf.append(simple[esc])
                        i += 2
                        continue
                    # octal escape \ooo
                    if esc.isdigit():
                        j = i + 1
                        oct_digits = ""
                        while j < len(java) and len(oct_digits) < 3 and java[j] in "01234567":
                            oct_digits += java[j]
                            j += 1
                        buf.append(int(oct_digits, 8) & 0xFF)
                        i = j
                        continue
                    buf.append(ord(esc) & 0xFF)
                    i += 2
                    continue
                if c == '"':
                    i += 1
                    break
                # plain char -> low byte (chars >255 carry low byte; protoc uses
                # ISO-8859-1 so high chars shouldn't appear, but be safe)
                buf.append(ord(c) & 0xFF)
                i += 1
            parts.append(bytes(buf))
        if parts:
            blobs.append(b"".join(parts))
    return blobs


# ---- FileDescriptorProto -> .proto text -------------------------------------

TYPE_NAME = {
    1: "double", 2: "float", 3: "int64", 4: "uint64", 5: "int32",
    6: "fixed64", 7: "fixed32", 8: "bool", 9: "string", 10: "group",
    11: "message", 12: "bytes", 13: "uint32", 14: "enum", 15: "sfixed32",
    16: "sfixed64", 17: "sint32", 18: "sint64",
}
LABEL = {1: "optional", 2: "required", 3: "repeated"}


def type_str(f):
    if f.type in (11, 14):  # message / enum
        return f.type_name.lstrip(".")
    return TYPE_NAME.get(f.type, f"type{f.type}")


def render_field(f, indent, oneof_names):
    parts = [" " * indent]
    if f.HasField("oneof_index"):
        # handled by caller grouping; but if reached directly just inline
        pass
    label = LABEL.get(f.label, "")
    if label and label != "optional":
        parts.append(label + " ")
    elif f.label == 1:
        parts.append("optional ")
    parts.append(f"{type_str(f)} {f.name} = {f.number}")
    if f.HasField("default_value"):
        parts.append(f" [default = {f.default_value}]")
    parts.append(";")
    return "".join(parts)


def render_enum(e, indent):
    out = [" " * indent + f"enum {e.name} {{"]
    for v in e.value:
        out.append(" " * (indent + 2) + f"{v.name} = {v.number};")
    out.append(" " * indent + "}")
    return out


def render_message(m, indent):
    out = [" " * indent + f"message {m.name} {{"]
    ind = indent + 2
    for ne in m.enum_type:
        out += render_enum(ne, ind)
    for nm in m.nested_type:
        if nm.options.map_entry:
            continue  # rendered inline via map<>
        out += render_message(nm, ind)
    # group fields by oneof
    oneofs = {i: [] for i in range(len(m.oneof_decl))}
    plain = []
    for f in m.field:
        if f.HasField("oneof_index"):
            oneofs[f.oneof_index].append(f)
        else:
            plain.append(f)
    for f in plain:
        out.append(render_field(f, ind, None))
    for idx, decl in enumerate(m.oneof_decl):
        out.append(" " * ind + f"oneof {decl.name} {{")
        for f in oneofs[idx]:
            out.append(" " * (ind + 2) + f"{type_str(f)} {f.name} = {f.number};")
        out.append(" " * ind + "}")
    out.append(" " * indent + "}")
    return out


def fdp_to_proto(fdp: descriptor_pb2.FileDescriptorProto) -> str:
    out = ['syntax = "proto2";']
    out.append("")
    if fdp.package:
        out.append(f"package {fdp.package};")
    for dep in fdp.dependency:
        out.append(f'import "{dep}";')
    if fdp.options.java_package or fdp.options.java_outer_classname:
        out.append("")
        if fdp.options.java_package:
            out.append(f'option java_package = "{fdp.options.java_package}";')
        if fdp.options.java_outer_classname:
            out.append(f'option java_outer_classname = "{fdp.options.java_outer_classname}";')
    out.append("")
    for e in fdp.enum_type:
        out += render_enum(e, 0)
        out.append("")
    for m in fdp.message_type:
        out += render_message(m, 0)
        out.append("")
    return "\n".join(out)


def process(path, out_dir):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        java = fh.read()
    blobs = find_string_array(java)
    if not blobs:
        print(f"[ ] no descriptor literal in {os.path.basename(path)}")
        return []
    results = []
    for blob in blobs:
        fdp = descriptor_pb2.FileDescriptorProto()
        try:
            fdp.ParseFromString(blob)
        except Exception as e:
            print(f"[!] parse failed for {os.path.basename(path)}: {e} ({len(blob)} bytes)")
            continue
        name = fdp.name or (os.path.splitext(os.path.basename(path))[0] + ".proto")
        text = fdp_to_proto(fdp)
        out_path = os.path.join(out_dir, os.path.basename(name))
        with open(out_path, "w") as fh:
            fh.write(text)
        nmsg = len(fdp.message_type)
        nenum = len(fdp.enum_type)
        print(f"[+] {os.path.basename(path):28s} -> {os.path.basename(name):24s} "
              f"({nmsg} msgs, {nenum} enums, {len(blob)} desc bytes)")
        results.append(out_path)
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*")
    ap.add_argument("--glob", default=None)
    ap.add_argument("-o", "--out", default="protos")
    args = ap.parse_args()
    files = list(args.files)
    if args.glob:
        files += glob.glob(args.glob, recursive=True)
    if not files:
        print("no input files")
        sys.exit(1)
    os.makedirs(args.out, exist_ok=True)
    total = 0
    for f in files:
        total += len(process(f, args.out))
    print(f"\n[=] wrote {total} .proto file(s) to {args.out}/")


if __name__ == "__main__":
    main()
