"""Gesten-Trigger: löst den Inspektionszyklus aus, wenn der Arbeiter eine bestimmte Hand-Geste
stabil hält (Poster: "gesture / button", Gestenerkennung ist standalone).

Ersetzt die alte Handover-Analyse. Arbeitet auf den MediaPipe-Händen (hands.py). Liefert pro
Verarbeitungs-Frame einen Zustand und ein EINMALIGES `triggered`-Flag (Flanke): nach dem Auslösen
muss die Geste erst wieder losgelassen werden, bevor erneut getriggert wird (kein Dauerfeuer).
"""
from __future__ import annotations

from typing import List

from config import config


class GestureTrigger:
    def __init__(self):
        self._stable = 0
        self._armed = True  # darf gerade auslösen?

    def update(self, hands: List[dict]) -> dict:
        gesture = hands[0].get("gesture") if hands else None
        holding = gesture == config.TRIGGER_GESTURE

        if holding:
            self._stable += 1
        else:
            self._stable = 0
            self._armed = True  # Geste losgelassen -> wieder scharf

        triggered = False
        if holding and self._armed and self._stable >= config.TRIGGER_STABLE_FRAMES:
            triggered = True
            self._armed = False  # erst nach Loslassen erneut

        return {
            "gesture": gesture,
            "trigger_gesture": config.TRIGGER_GESTURE,
            "stable_frames": self._stable,
            "progress": min(1.0, self._stable / max(1, config.TRIGGER_STABLE_FRAMES)),
            "triggered": triggered,
        }
