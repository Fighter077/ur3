"""Kleiner, thread-sicherer Puffer für das zuletzt empfangene Kamera-Frame.

Entkoppelt den WebRTC-Empfang (rtc.py, schreibt) von der Inspektionssequenz (robot/inspection.py,
liest), ohne Zirkelimport. Die stationäre Kamera schaut auf den Greifer/das Teil; die Inspektion
greift am jeweiligen Haltepunkt einfach das aktuelle Frame ab.
"""
from __future__ import annotations

import threading
from typing import Optional

import numpy as np

_lock = threading.Lock()
_latest: Optional[np.ndarray] = None


def set_frame(frame_bgr: np.ndarray) -> None:
    global _latest
    with _lock:
        _latest = frame_bgr


def get_frame() -> Optional[np.ndarray]:
    with _lock:
        return None if _latest is None else _latest.copy()
