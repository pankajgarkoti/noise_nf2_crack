"""bleak-backed BLE transport for the Noise NF 2 (ZH/Zhapp 'Apricot' protocol).

Connects, enables notifications on the protobuf characteristics, negotiates MTU,
and provides send_message()/wait for a reassembled SEWear reply.

Char roles (com.zhapp.ble.utils.UuidUtils + BluetoothService):
  16186f02 = uplink   (phone -> watch commands)   <- we WRITE here
  16186f01 = downlink (watch -> phone responses)  <- we NOTIFY here
  16186f03 = bulk (sport/fitness history)
  16186f04 = OTA / large file upload
  16186f05 = voice / Alexa
"""
from __future__ import annotations

import asyncio
import logging

from bleak import BleakClient

from . import codec

log = logging.getLogger("nf2.transport")

SERVICE_PROTOBUF = "16186f00-0000-1000-8000-00807f9b34fb"
CHAR_F01 = "16186f01-0000-1000-8000-00807f9b34fb"  # downlink/responses
CHAR_F02 = "16186f02-0000-1000-8000-00807f9b34fb"  # uplink/commands
CHAR_F03 = "16186f03-0000-1000-8000-00807f9b34fb"  # bulk
CHAR_F04 = "16186f04-0000-1000-8000-00807f9b34fb"  # OTA
CHAR_F05 = "16186f05-0000-1000-8000-00807f9b34fb"  # voice

DEFAULT_MTU = 244  # f294311k default (full per-write size); payload = MTU-2


class NF2Transport:
    def __init__(self, address, name_hint: str = "NF 2"):
        self.address = address
        self.name_hint = name_hint
        self.client: BleakClient | None = None
        self.mtu = DEFAULT_MTU  # full per-write size; chunk = mtu-2
        self._reasm = codec.Reassembler()
        # queues of fully-assembled downlink message bodies (bytes)
        self._messages: asyncio.Queue[bytes] = asyncio.Queue()
        # flow-control events from the watch (on f02)
        self._begin = asyncio.Event()
        self._ack = asyncio.Event()
        self._resend: asyncio.Queue[int] = asyncio.Queue()
        # raw notifications per char, for debugging / other channels
        self._raw_log: list[tuple[str, bytes]] = []
        self._last_outgoing_packets: list[bytes] = []

    # ---- connection lifecycle ----

    async def connect(self):
        log.info("connecting to %s", self.address)
        self._loop = asyncio.get_running_loop()
        self.client = BleakClient(self.address, timeout=20.0)
        await self.client.__aenter__()
        log.info("connected=%s mtu=%s", self.client.is_connected, self.client.mtu_size)
        try:
            # full ATT MTU minus the 3-byte ATT write header
            self.mtu = max(23, self.client.mtu_size) - 3
        except Exception:
            self.mtu = DEFAULT_MTU
        # enable notifications on the protobuf chars we care about
        await self.client.start_notify(CHAR_F01, self._on_f01)
        await self.client.start_notify(CHAR_F02, self._on_f02)
        log.info("notifications enabled on f01,f02; per-write=%d chunk=%d",
                 self.mtu, self.mtu - 2)

    async def disconnect(self):
        if self.client:
            try:
                await self.client.__aexit__(None, None, None)
            except Exception:
                pass
            self.client = None

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *a):
        await self.disconnect()

    # ---- notification handlers ----

    def _write_f01_soon(self, payload: bytes):
        """Schedule a control write on f01 from within a sync notify callback."""
        async def _w():
            try:
                await self.client.write_gatt_char(CHAR_F01, payload, response=False)
                log.debug("f01 -> %s", payload.hex())
            except Exception as e:
                log.warning("f01 write failed: %s", e)
        self._loop.create_task(_w())

    def _on_f01(self, _char, data: bytearray):
        b = bytes(data)
        self._raw_log.append(("f01", b))
        log.debug("f01 <- %s", b.hex())
        evt, val = self._reasm.feed(b)
        if evt == "message":
            log.info("f01 reassembled message (%d bytes): %s", len(val), val.hex())
            self._write_f01_soon(codec.DOWNLINK_ACK)  # final ack (m217958a)
            self._messages.put_nowait(val)
        elif evt == "data":
            log.info("f01 single packet (%d bytes): %s", len(val), val.hex())
            self._write_f01_soon(codec.DOWNLINK_ACK)
            self._messages.put_nowait(val)
        elif evt == "ctrl":
            log.info("f01 ctrl: %s", val)
            if val and val.kind == "announce":
                # tell the watch we're ready to receive (m218049d "go")
                self._write_f01_soon(codec.DOWNLINK_GO)
        elif evt == "partial":
            log.debug("f01 partial %s/%s", *val)

    def _on_f02(self, _char, data: bytearray):
        b = bytes(data)
        self._raw_log.append(("f02", b))
        log.debug("f02 <- %s", b.hex())
        ctrl = codec.parse_ctrl(b)
        if ctrl:
            log.info("f02 ctrl: %s", ctrl)
            if ctrl.kind == "begin":
                self._begin.set()
            elif ctrl.kind == "ack":
                self._ack.set()
            elif ctrl.kind == "resend":
                self._resend.put_nowait(ctrl.seq)
            elif ctrl.kind == "busy":
                log.warning("device busy")
        else:
            # f02 can also carry a reassembled response on some flows
            evt, val = self._reasm.feed(b)
            if evt in ("message", "data"):
                log.info("f02 message (%d bytes): %s", len(val), val.hex())
                self._messages.put_nowait(val)

    # ---- send / receive ----

    async def send_body(self, body: bytes, wait_begin: float = 2.0):
        """Frame a protobuf body and write it using the watch's flow-control.

        Sequence (matches BluetoothService): write announce -> wait for device
        'begin' (00 00 01 01) -> stream indexed data packets -> (device ACKs).
        If 'begin' isn't seen quickly we stream anyway (some firmwares accept it).
        """
        data_packets = codec.split_body(body, self.mtu)  # [idx][chunk]...
        announce = codec.build_announce(len(data_packets))
        self._begin.clear()
        self._ack.clear()
        log.info("send_body %d bytes -> %d data packets (announce then stream)",
                 len(body), len(data_packets))

        # 1. announce
        log.debug("f02 -> announce %s", announce.hex())
        await self.client.write_gatt_char(CHAR_F02, announce, response=False)

        # 2. wait for 'begin'
        try:
            await asyncio.wait_for(self._begin.wait(), timeout=wait_begin)
            log.debug("got 'begin' from device")
        except asyncio.TimeoutError:
            log.warning("no 'begin' within %.1fs; streaming anyway", wait_begin)

        # 3. stream data packets
        await self._stream_packets(data_packets)

    async def _stream_packets(self, data_packets):
        self._last_outgoing_packets = data_packets
        for i, pkt in enumerate(data_packets, start=1):
            log.debug("f02 -> [%d] %s", i, pkt.hex())
            await self.client.write_gatt_char(CHAR_F02, pkt, response=False)
            await asyncio.sleep(0.015)
        # handle resend requests for a short window
        try:
            while True:
                seq = await asyncio.wait_for(self._resend.get(), timeout=0.3)
                if 1 <= seq <= len(data_packets):
                    log.info("resending packet %d", seq)
                    await self.client.write_gatt_char(
                        CHAR_F02, data_packets[seq - 1], response=False)
        except asyncio.TimeoutError:
            pass

    async def recv_message(self, timeout: float = 8.0) -> bytes | None:
        try:
            return await asyncio.wait_for(self._messages.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    def drain_raw(self) -> list[tuple[str, bytes]]:
        out = self._raw_log[:]
        self._raw_log.clear()
        return out
