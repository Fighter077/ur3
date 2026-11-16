"""Label-OCR: liest Hersteller / Modell / Kapazität vom Bauteil-Label.

Backend: Tesseract (pytesseract) – "ein existierendes OCR-Modell" (Poster). Bewusst als dünne,
austauschbare Schicht: `read(frame) -> dict`. Wer lieber EasyOCR / ein Vision-Modell nutzt,
ersetzt nur diese Klasse.

Die Feld-Extraktion ist heuristisch (Herstellerliste + Kapazitäts-Regex). Reicht fürs Konzept;
unsichere Fälle bekommen status="review" und landen in der manuellen Nachkontrolle.
"""
from __future__ import annotations

import re
from typing import Optional

import cv2
import numpy as np

from config import config

# Bekannte Hersteller von HDDs / optischen Laufwerken / RAM.
_MANUFACTURERS = [
    "Seagate", "Western Digital", "WD", "Samsung", "Toshiba", "Hitachi", "HGST",
    "Kingston", "Crucial", "Corsair", "Micron", "SanDisk", "Intel", "SK Hynix",
    "Hynix", "G.Skill", "GSkill", "ADATA", "Transcend", "Maxtor", "Fujitsu",
    "LG", "LiteOn", "Lite-On", "Pioneer", "ASUS", "Patriot", "Team",
]
# Kapazität/Takt: 500GB, 1 TB, 2TB, 8 GB, 3200 MHz, DDR4-3200, PC4-25600 ...
_CAP_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s?(TB|GB|MB)\b", re.IGNORECASE)
_SPEED_RE = re.compile(r"\b(DDR[2-5][- ]?\d{3,4}|PC[2-5][- ]?\d{3,5}|\d{3,4}\s?MHz)\b", re.IGNORECASE)
_MODEL_RE = re.compile(r"\b([A-Z]{2,}[A-Z0-9][A-Z0-9\-]{3,})\b")


class LabelReader:
    def __init__(self):
        self.enabled = config.OCR_ENABLED
        self._tess = None
        if not self.enabled:
            return
        try:
            import pytesseract
            self._tess = pytesseract
            # Prüfen, ob die Tesseract-Binary da ist (apt-Paket tesseract-ocr).
            _ = pytesseract.get_tesseract_version()
            print("[ocr] Tesseract bereit.")
        except Exception as e:
            print(f"[ocr] Tesseract nicht verfügbar ({e}) -> OCR liefert nur Rohstatus 'review'.")
            self._tess = None

    @staticmethod
    def _variants(frame_bgr: np.ndarray):
        """Mehrere Vorverarbeitungen – Labels sind je nach Licht/Glanz mal so, mal so lesbar."""
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape[:2]
        if max(h, w) < 1400:  # Hochskalieren hilft Tesseract bei kleiner Schrift
            scale = 1400 / max(h, w)
            gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        gray = cv2.bilateralFilter(gray, 7, 50, 50)
        otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
        adap = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                     cv2.THRESH_BINARY, 35, 11)
        return [gray, otsu, adap]

    @staticmethod
    def _score(fields: dict) -> int:
        """Wie „gut" ist ein OCR-Ergebnis? Hersteller + Kapazität zählen am meisten."""
        s = 0
        if fields.get("manufacturer"): s += 5
        if fields.get("capacity"): s += 3
        if fields.get("speed"): s += 2
        if fields.get("model"): s += 2
        s += min(3, len((fields.get("raw_text") or "").split()) // 4)  # etwas Text ist besser als keiner
        return s

    def read(self, frame_bgr: Optional[np.ndarray]) -> dict:
        """Bestes Ergebnis über mehrere Vorverarbeitungen + Segmentierungsmodi (PSM)."""
        empty = {"raw_text": "", "manufacturer": None, "model": None,
                 "capacity": None, "speed": None, "status": "review"}
        if frame_bgr is None or self._tess is None:
            return empty

        best, best_score = empty, -1
        for img in self._variants(frame_bgr):
            for psm in (6, 11, 4):  # 6=Block, 11=verstreuter Text, 4=Spalten
                try:
                    text = self._tess.image_to_string(img, lang=config.OCR_LANG, config=f"--psm {psm}")
                except Exception as e:
                    print(f"[ocr] Lesefehler: {e}")
                    continue
                fields = self._parse(text)
                fields["raw_text"] = text.strip()
                sc = self._score(fields)
                if sc > best_score:
                    best, best_score = fields, sc

        identified = bool(best.get("manufacturer")) or (best.get("model") and best.get("capacity"))
        best["status"] = "identified" if identified else "review"
        return best

    @staticmethod
    def _parse(text: str) -> dict:
        out = {"manufacturer": None, "model": None, "capacity": None, "speed": None}
        low = text.lower()
        for m in _MANUFACTURERS:
            if m.lower() in low:
                out["manufacturer"] = "Western Digital" if m == "WD" else m
                break
        cap = _CAP_RE.search(text)
        if cap:
            out["capacity"] = f"{cap.group(1)}{cap.group(2).upper()}"
        spd = _SPEED_RE.search(text)
        if spd:
            out["speed"] = spd.group(1).upper().replace(" ", "")
        # Modell: längster "code-artiger" Token, der nicht die Kapazität ist.
        candidates = [t for t in _MODEL_RE.findall(text) if not _CAP_RE.fullmatch(t)]
        if candidates:
            out["model"] = max(candidates, key=len)
        return out


_reader: Optional[LabelReader] = None


def get_reader() -> LabelReader:
    global _reader
    if _reader is None:
        _reader = LabelReader()
    return _reader
