"""Loads the generated protobuf modules (which import each other by bare name)."""
import os
import sys

_PB_DIR = os.path.join(os.path.dirname(__file__), "pb")
if _PB_DIR not in sys.path:
    sys.path.insert(0, _PB_DIR)

import Wear_pb2  # noqa: E402
import Device_pb2  # noqa: E402
import BindAccount_pb2  # noqa: E402
import Common_pb2  # noqa: E402

SEWear = Wear_pb2.SEWear
FunctionId = Wear_pb2.SEWear  # enum lives on SEWear


def fn_name(fid: int) -> str:
    try:
        return Wear_pb2.SEWear.SEFunctionId.Name(fid)
    except Exception:
        return f"UNKNOWN({fid})"


def parse_wear(body: bytes) -> "Wear_pb2.SEWear":
    w = Wear_pb2.SEWear()
    w.ParseFromString(body)
    return w


def build(fid: int, **fields) -> bytes:
    """Build a SEWear with id=fid and optional payload submessage fields."""
    w = Wear_pb2.SEWear()
    w.id = fid
    for k, v in fields.items():
        getattr(w, k).CopyFrom(v) if hasattr(v, "DESCRIPTOR") else setattr(w, k, v)
    return w.SerializeToString()
