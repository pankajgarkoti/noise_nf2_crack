"""ZH/Zhapp ("Apricot") BLE framing codec for the Noise NF 2.

Wire format (reconstructed from com.zhapp.ble.BluetoothService):

  A logical message = one serialized WearProtos.SEWear protobuf ("body").
  The body is split across BLE packets of (MTU-2) bytes. Each packet is:

      [2-byte little-endian packet index][payload bytes]

  Packet indices are 1-based. Index 0 is reserved for 6-byte control frames.

  To SEND a multi-packet body the phone first announces the packet count with a
  start/control frame, then streams the indexed packets, then waits for ACK.

  Control frames (6 bytes), index 0:
      00 00 00 00 NN 00   -> "I will send NN packets"  (phone announce; build_announce)
      00 00 01 01 00 00   -> begin / send-now         (device->phone)
      00 00 01 02 00 00   -> device busy
      00 00 01 03 00 00   -> ack / done
      00 00 01 05 nn 00   -> packet nn lost, resend
      00 00 00 00 NN 00   -> device announces NN packets it will send (downlink)

  Downlink reassembly: device sends a start frame (00 00 00 00 total 00) then
  packets [idx_LE][payload]; phone tracks a received bitmask and reassembles in
  index order, then protobuf-parses the concatenation.

No CRC at packet level; integrity via index + retransmit. No encryption.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field


def u16le(n: int) -> bytes:
    return struct.pack("<H", n)


def from_u16le(b: bytes, off: int = 0) -> int:
    return struct.unpack_from("<H", b, off)[0]


# ---- control frame constants ------------------------------------------------

CTRL_SUBTYPE = 0x01  # bytes[2] for device flow-control frames
CTRL_BEGIN = 0x01    # send now
CTRL_BUSY = 0x02
CTRL_ACK = 0x03
CTRL_RESEND = 0x05


# Downlink replies the phone sends back on f01 (mirror of uplink ctrl frames):
#   m218049d() = "ready/go" after device announce  -> 00 00 01 01 00 00 (begin)
#   m217958a() = final ACK after full message      -> 00 00 01 03 00 00 (ack)
DOWNLINK_GO = bytes([0x00, 0x00, CTRL_SUBTYPE, CTRL_BEGIN, 0x00, 0x00])
DOWNLINK_ACK = bytes([0x00, 0x00, CTRL_SUBTYPE, CTRL_ACK, 0x00, 0x00])


def build_resend_request(seq: int) -> bytes:
    return bytes([0x00, 0x00, CTRL_SUBTYPE, CTRL_RESEND]) + u16le(seq)


def build_announce(total_packets: int) -> bytes:
    """Phone -> device: '{0,0,0,0,N,0}' I will send N packets.

    Mirrors BluetoothService.m217493a(i) = {0,0,0,0,(byte)i,0}.
    """
    return bytes([0x00, 0x00, 0x00, 0x00, total_packets & 0xFF, (total_packets >> 8) & 0xFF])


def build_packet(index: int, payload: bytes) -> bytes:
    """[2-byte LE 1-based index][payload]. Mirrors AbstractC29000a.m218026a."""
    return u16le(index) + payload


def split_body(body: bytes, mtu: int) -> list[bytes]:
    """Split a protobuf body into indexed BLE packets (chunk = mtu-2)."""
    chunk = mtu - 2
    if chunk <= 0:
        raise ValueError("mtu too small")
    packets = []
    n = (len(body) + chunk - 1) // chunk or 1
    for i in range(n):
        seg = body[i * chunk : (i + 1) * chunk]
        packets.append(build_packet(i + 1, seg))
    return packets


def encode_message(body: bytes, mtu: int) -> list[bytes]:
    """Full outgoing wire sequence for one logical message: announce + packets.

    Returns a list of byte buffers to write (in order) to char 16186f02.
    """
    packets = split_body(body, mtu)
    return [build_announce(len(packets))] + packets


# ---- control-frame classification (incoming on f01/f02) ---------------------

@dataclass
class CtrlFrame:
    kind: str           # 'announce' | 'begin' | 'busy' | 'ack' | 'resend' | 'unknown'
    total: int = 0      # for 'announce'
    seq: int = 0        # for 'resend'
    raw: bytes = b""


def parse_ctrl(frame: bytes) -> CtrlFrame | None:
    """Classify a 6-byte index-0 control frame. Returns None if not a ctrl frame."""
    if len(frame) < 6:
        return None
    if frame[0] != 0x00 or frame[1] != 0x00:
        return None
    b2, b3 = frame[2], frame[3]
    val = from_u16le(frame, 4)
    if b2 == 0x00 and b3 == 0x00:
        return CtrlFrame("announce", total=val, raw=frame)
    if b2 == CTRL_SUBTYPE:
        if b3 == CTRL_BEGIN:
            return CtrlFrame("begin", raw=frame)
        if b3 == CTRL_BUSY:
            return CtrlFrame("busy", raw=frame)
        if b3 == CTRL_ACK:
            return CtrlFrame("ack", raw=frame)
        if b3 == CTRL_RESEND:
            return CtrlFrame("resend", seq=val, raw=frame)
    return CtrlFrame("unknown", raw=frame)


# ---- downlink reassembler ---------------------------------------------------

@dataclass
class Reassembler:
    """Reassembles a multi-packet downlink message from char 16186f01.

    Feed every notification through .feed(); when a full message is assembled it
    returns the concatenated body bytes (ready for SEWear.ParseFromString),
    otherwise None. Also surfaces control frames via the on_ctrl callback path:
    callers should check parse_ctrl() themselves for f02 flow control.
    """
    total: int = 0
    packets: dict[int, bytes] = field(default_factory=dict)

    def reset(self):
        self.total = 0
        self.packets = {}

    def feed(self, data: bytes):
        """Returns (event, value):
        ('ctrl', CtrlFrame) for control frames,
        ('message', bytes) when a full message is reassembled,
        ('partial', (have, total)) while collecting,
        ('data', bytes) for a lone single packet when no announce was seen.
        """
        if len(data) < 2:
            return ("ignore", data)

        idx = from_u16le(data, 0)
        payload = data[2:]

        if idx == 0:
            ctrl = parse_ctrl(data)
            if ctrl and ctrl.kind == "announce":
                self.total = ctrl.total
                self.packets = {}
                return ("ctrl", ctrl)
            return ("ctrl", ctrl)

        # data packet
        self.packets[idx] = payload
        if self.total and len(self.packets) >= self.total:
            body = b"".join(self.packets[i] for i in sorted(self.packets))
            self.reset()
            return ("message", body)
        if not self.total:
            # no announce seen; treat single packet as a complete small message
            return ("data", payload)
        return ("partial", (len(self.packets), self.total))

    def missing(self) -> list[int]:
        if not self.total:
            return []
        return [i for i in range(1, self.total + 1) if i not in self.packets]
