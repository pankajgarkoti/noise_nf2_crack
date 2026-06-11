"""High-level NF 2 client: send a SEWear command, collect SEWear replies."""
from __future__ import annotations

import asyncio
import logging

from . import pb_loader as pb
from .transport import NF2Transport

log = logging.getLogger("nf2.client")


class NF2Client:
    def __init__(self, address):
        self.t = NF2Transport(address)

    async def __aenter__(self):
        await self.t.connect()
        return self

    async def __aexit__(self, *a):
        await self.t.disconnect()

    async def send(self, sewear) -> None:
        """Send a built SEWear message object (or raw bytes)."""
        body = sewear.SerializeToString() if hasattr(sewear, "SerializeToString") else sewear
        log.info("-> id=%s (%s) body=%s",
                 getattr(sewear, "id", "?"),
                 pb.fn_name(getattr(sewear, "id", -1)) if hasattr(sewear, "id") else "raw",
                 body.hex())
        await self.t.send_body(body)

    async def recv(self, timeout: float = 8.0):
        """Receive one SEWear reply (parsed) or None."""
        raw = await self.t.recv_message(timeout=timeout)
        if raw is None:
            return None
        try:
            w = pb.parse_wear(raw)
            log.info("<- id=%s (%s) payload=%s",
                     w.id, pb.fn_name(w.id), w.WhichOneof("payload"))
            return w
        except Exception as e:
            log.warning("parse failed (%s): %s", e, raw.hex())
            return raw

    async def request(self, sewear, timeout: float = 8.0, drain: float = 1.5):
        """Send then collect all replies that arrive within the window."""
        await self.send(sewear)
        replies = []
        first = await self.recv(timeout=timeout)
        if first is not None:
            replies.append(first)
            while True:
                more = await self.recv(timeout=drain)
                if more is None:
                    break
                replies.append(more)
        return replies

    def new(self, fid: int):
        w = pb.SEWear()
        w.id = fid
        return w
