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
_latest: Optional[np.ndarray] = None       # aus dem WebRTC-Stream (komprimiert, niedrig aufgelöst)
_hires: Optional[np.ndarray] = None        # Full-Res-Standbild vom Client (unkomprimiert, scharf)


def clear() -> None:
    global _latest, _hires
    with _lock:
        _latest = _hires = None


def set_frame(frame_bgr: np.ndarray) -> None:
    global _latest
    with _lock:
        _latest = frame_bgr


def get_frame() -> Optional[np.ndarray]:
    with _lock:
        return None if _latest is None else _latest.copy()


def set_hires(frame_bgr: np.ndarray) -> None:
    global _hires
    with _lock:
        _hires = frame_bgr


def get_hires() -> Optional[np.ndarray]:
    with _lock:
        return None if _hires is None else _hires.copy()


def best_for_ocr() -> Optional[np.ndarray]:
    """Bevorzugt das scharfe Full-Res-Standbild; sonst das WebRTC-Frame."""
    with _lock:
        if _hires is not None:
            return _hires.copy()
        return None if _latest is None else _latest.copy()
