"""Hand-/Finger-Landmarks via MediaPipe Hands (Tasks-API, mediapipe >= 1.x).

MediaPipe 1.x hat die alte `mp.solutions.hands`-API entfernt; wir nutzen den neuen
`HandLandmarker` (Tasks-API). Er braucht eine Modelldatei `hand_landmarker.task` (siehe
config.HANDS_MODEL_PATH; im Dockerfile vorab heruntergeladen, sonst Laufzeit-Download).

Pro erkannter Hand 21 Landmarks (Handgelenk + je 4 pro Finger), x/y normalisiert (0..1).
Ausgabe pro Hand:
  {handedness: "Left"|"Right", score, landmarks: [[x, y, z], ... 21], gesture: "open"|"fist"|"point"|"pinch"|"other"}

Die Gesten-Klassifikation ist bewusst einfach (gestreckte Finger + Pinch-Abstand) und steuert den
Zyklus-Trigger (siehe gesture_trigger.py). Leicht erweiterbar.

Landmark-Indizes: 0 Handgelenk | 1-4 Daumen | 5-8 Zeige | 9-12 Mittel | 13-16 Ring | 17-20 kl.
"""
from __future__ import annotations

import math
import os
import urllib.request
from typing import List

import cv2

from config import config

_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
    "hand_landmarker/float16/1/hand_landmarker.task"
)

_FINGERS = {
    "index": (5, 6, 7, 8),
    "middle": (9, 10, 11, 12),
    "ring": (13, 14, 15, 16),
    "pinky": (17, 18, 19, 20),
}
_THUMB = (1, 2, 3, 4)


def _dist(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _ensure_model(path: str) -> bool:
    if os.path.exists(path):
        return True
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        print(f"[perception] Lade Hand-Modell herunter -> {path}")
        urllib.request.urlretrieve(_MODEL_URL, path)
        return True
    except Exception as e:
        print(f"[perception] Hand-Modell-Download fehlgeschlagen: {e}")
        return False


class HandDetector:
    def __init__(self):
        self.enabled = True
        self.detector = None
        try:
            import mediapipe as mp
            from mediapipe.tasks import python as mp_python
            from mediapipe.tasks.python import vision
        except ImportError:
            print("[perception] mediapipe nicht installiert -> Finger-/Handerkennung deaktiviert.")
            self.enabled = False
            return

        if not _ensure_model(config.HANDS_MODEL_PATH):
            self.enabled = False
            return

        try:
            self._mp = mp
            options = vision.HandLandmarkerOptions(
                base_options=mp_python.BaseOptions(model_asset_path=config.HANDS_MODEL_PATH),
                running_mode=vision.RunningMode.IMAGE,
                num_hands=config.HANDS_MAX,
                min_hand_detection_confidence=config.HANDS_MIN_CONF,
                min_hand_presence_confidence=config.HANDS_MIN_CONF,
                min_tracking_confidence=config.HANDS_MIN_CONF,
            )
            self.detector = vision.HandLandmarker.create_from_options(options)
            print(f"[perception] MediaPipe HandLandmarker geladen (max={config.HANDS_MAX}).")
        except Exception as e:
            print(f"[perception] HandLandmarker konnte nicht geladen werden: {e} -> deaktiviert.")
            self.enabled = False

    def detect(self, frame_bgr) -> List[dict]:
        if not self.enabled or self.detector is None:
            return []
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        mp_image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
        try:
            result = self.detector.detect(mp_image)
        except Exception as e:
            print(f"[perception] Hand-Detect-Fehler: {e}")
            return []

        out: List[dict] = []
        hand_lms = result.hand_landmarks or []
        handed = result.handedness or []
        for idx, lms in enumerate(hand_lms):
            pts = [[lm.x, lm.y, lm.z] for lm in lms]
            label, score = "?", 0.0
            if idx < len(handed) and handed[idx]:
                c = handed[idx][0]
                label, score = c.category_name, float(c.score)
            out.append({
                "handedness": label,
                "score": score,
                "landmarks": pts,
                "gesture": self._gesture(pts),
            })
        return out

    @staticmethod
    def _gesture(pts: List[List[float]]) -> str:
        wrist = pts[0]
        extended = 0
        for mcp, pip, dip, tip in _FINGERS.values():
            if _dist(pts[tip], wrist) > _dist(pts[pip], wrist) * 1.05:
                extended += 1
        palm = pts[9]
        thumb_out = _dist(pts[_THUMB[3]], palm) > _dist(pts[_THUMB[2]], palm) * 1.05
        hand_scale = _dist(pts[5], pts[17]) + 1e-6
        pinch = _dist(pts[4], pts[8]) < 0.6 * hand_scale

        if pinch and extended <= 1:
            return "pinch"
        if extended == 0 and not thumb_out:
            return "fist"
        if extended >= 4:
            return "open"
        if extended == 1:
            return "point"
        return "other"
