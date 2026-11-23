"""Kleiner, thread-sicherer Puffer für das zuletzt empfangene Kamera-Frame.

Entkoppelt den WebRTC-Empfang (rtc.py, schreibt) von der Inspektionssequenz (robot/inspection.py,
liest), ohne Zirkelimport. Die stationäre Kamera schaut auf den Greifer/das Teil; die Inspektion
greift am jeweiligen Haltepunkt einfach das aktuelle Frame ab.
"""
from __future__ import annotations

import threading
from time import monotonic
from typing import Optional

import numpy as np

_lock = threading.Lock()
_latest: Optional[np.ndarray] = None       # aus dem WebRTC-Stream (komprimiert, niedrig aufgelöst)
_hires: Optional[np.ndarray] = None        # Full-Res-Standbild vom Client (unkomprimiert, scharf)
_latest_at = _hires_at = 0.0


def set_frame(frame_bgr: np.ndarray) -> None:
    global _latest, _latest_at
    with _lock:
        _latest = frame_bgr
        _latest_at = monotonic()


def get_frame() -> Optional[np.ndarray]:
    with _lock:
        return None if _latest is None else _latest.copy()


def set_hires(frame_bgr: np.ndarray) -> None:
    global _hires, _hires_at
    with _lock:
        _hires = frame_bgr
        _hires_at = monotonic()


def get_hires() -> Optional[np.ndarray]:
    with _lock:
        return None if _hires is None else _hires.copy()


def best_for_ocr(*, since: float = 0.0) -> Optional[np.ndarray]:
    """Bevorzugt das scharfe Full-Res-Standbild; sonst das WebRTC-Frame."""
    with _lock:
        if _hires is not None and _hires_at >= since:
            return _hires.copy()
        return _latest.copy() if _latest is not None and _latest_at >= since else None
