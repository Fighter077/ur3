"""Live WebRTC camera tracks, keyed by the browser that publishes them."""
from __future__ import annotations

import asyncio
from time import monotonic
from uuid import uuid4

from aiortc.contrib.media import MediaRelay


class CameraSource:
    def __init__(self, track):
        self.id = uuid4().hex
        self.track = track
        self.relay = MediaRelay()
        self.latest = None
        self.updated_at = 0.0
        self.detection = None
        self.next_detection_at = 0.0
        self.closed = False
        self.changed = asyncio.Event()

    def update(self, frame) -> None:
        self.latest = frame
        self.updated_at = monotonic()
        self.changed.set()
        self.changed = asyncio.Event()

    def close(self) -> None:
        self.closed = True
        self.update(None)

    async def next_frame(self, previous):
        while self.latest is previous and not self.closed:
            await self.changed.wait()
        return self.latest


sources: dict[str, CameraSource] = {}


def remote_camera(client_id: str) -> CameraSource | None:
    return next((source for owner, source in sources.items() if owner != client_id), None)
