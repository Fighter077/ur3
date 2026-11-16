"""Virtueller Roboter-Greifer fürs Kamera-Overlay + Übergabe-Interaktion.

Ohne Hand-Auge-Kalibrierung lässt sich der echte UR3e nicht exakt ins Kamerabild projizieren.
Dieses Modul hält stattdessen einen *schematischen* Greifer-Zustand im normalisierten Bildraum
(0..1), der zwei Quellen folgt:

  * Leerlauf (rtc._process_frames, pro Frame): der Greifer fährt Richtung Hand des Arbeiters
    ("komm, nimm das Teil"); hält der Arbeiter die offene Hand nahe am Greifer stabil, wird die
    Übergabe ausgelöst (greifen + Inspektionszyklus starten, source="handover").
  * Laufender Zyklus (robot/inspection.py): die State-Machine setzt Greiferposition/Aktion
    passend zum Schritt (greifen → vor Kamera → OCR → ablegen).

Der WebRTC-Loop broadcastet `snapshot()` pro Frame im `analysis`-Payload; das Frontend zeichnet
den Greifer daraus und interpoliert weich.
"""
from __future__ import annotations

import math
import threading

from config import config

_lock = threading.Lock()
_s = {
    "x": 0.5, "y": 0.16,     # Greiferposition (normalisiert, Bildkoordinaten)
    "opening": 1.0,          # 0 = geschlossen, 1 = offen
    "holding": False,        # Teil im Greifer?
    "mode": "ready",         # ready | handover | inspect
    "action": "bereit",      # Klartext fürs UI
}
_stable = 0

READY = (0.5, 0.16)


def snapshot() -> dict:
    with _lock:
        return dict(_s)


def set_state(**kw) -> None:
    with _lock:
        _s.update(kw)


def _ease(cur: float, target: float, k: float = 0.35) -> float:
    return cur + (target - cur) * k


def update_idle(hands) -> tuple[dict, bool, float]:
    """Pro Frame im Leerlauf aufrufen. Führt den Greifer zur Hand und erkennt die Übergabe.
    Rückgabe: (snapshot, triggered, progress 0..1)."""
    global _stable
    palm = None
    gesture = None
    if hands:
        lm = hands[0].get("landmarks") or []
        if len(lm) > 9:
            palm = lm[9]
        elif lm:
            palm = lm[0]
        gesture = hands[0].get("gesture")

    with _lock:
        if palm is not None:
            tx, ty = palm[0], palm[1]
            _s["x"] = _ease(_s["x"], tx)
            _s["y"] = _ease(_s["y"], max(0.04, ty - 0.05))  # knapp über der Hand
            _s["mode"] = "handover"
            _s["opening"] = 1.0
            _s["holding"] = False

            d = math.hypot(_s["x"] - tx, _s["y"] - ty)
            near = d < 0.13 and gesture in ("open", "pinch")
            _stable = _stable + 1 if near else max(0, _stable - 1)

            progress = min(1.0, _stable / max(1, config.TRIGGER_STABLE_FRAMES))
            triggered = _stable >= config.TRIGGER_STABLE_FRAMES
            if triggered:
                _s["holding"] = True
                _s["opening"] = 0.0
                _s["action"] = "übernimmt Teil"
                _stable = 0
            else:
                _s["action"] = ("greift…" if near else "Teil reichen – offene Hand halten")
            return dict(_s), triggered, progress

        # keine Hand -> zurück in die Ruheposition
        _s["x"] = _ease(_s["x"], READY[0], 0.2)
        _s["y"] = _ease(_s["y"], READY[1], 0.2)
        _s["opening"] = 1.0
        _s["holding"] = False
        _s["mode"] = "ready"
        _s["action"] = "bereit – Teil reichen oder Zyklus starten"
        _stable = 0
        return dict(_s), False, 0.0
