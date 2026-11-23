"""Two-view presence check for the simulated handover."""
from __future__ import annotations

from time import monotonic

_PHONE_CLASSES = {"cell phone", "smartphone", "mobile phone"}
_MAX_AGE_S = 1.5
_STABLE_SAMPLES = 3


class HandoverObservation:
    def __init__(self):
        self.views: dict[bool, tuple[float, int]] = {}

    def update(self, network: bool, hands: list, objects: list, *, captured_at: float | None = None) -> dict:
        now = monotonic() if captured_at is None else captured_at
        previous, count = self.views.get(network, (0, 0))
        if now - previous > _MAX_AGE_S:
            count = 0
        targets = [box for box in objects if box["class"].strip().lower() in _PHONE_CLASSES]
        paired = False
        for target in targets:
            x1, y1, x2, y2 = target["bbox"]
            margin_x = (x2 - x1) * 0.25
            margin_y = (y2 - y1) * 0.25
            if any(
                x1 - margin_x <= point[0] <= x2 + margin_x
                and y1 - margin_y <= point[1] <= y2 + margin_y
                for hand in hands for point in hand["landmarks"]
            ):
                paired = True
                break
        self.views[network] = (now, min(count + 1, _STABLE_SAMPLES) if paired else 0)
        return {"objects": targets, "ready": self.ready()}

    def ready(self) -> bool:
        now = monotonic()
        observations = tuple(self.views.values())
        return len(observations) == 2 and all(
            count >= _STABLE_SAMPLES and now - seen <= _MAX_AGE_S
            for seen, count in observations
        )
