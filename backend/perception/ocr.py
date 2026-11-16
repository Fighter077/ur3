"""Label-OCR: liest Hersteller / Modell / Kapazität vom Bauteil-Label.

Zwei Engines (config.OCR_ENGINE):
  * "easyocr"   – neuronales OCR (robust bei Foto-Text/Geräteschildern). Lädt beim 1. Start
                  ~100 MB Modelle nach config.EASYOCR_DIR (gemountetes models/ -> persistent).
  * "tesseract" – leichtgewichtig, als Fallback. Gut bei sauberem, geradem Text.

Vorverarbeitung (config.LABEL_CROP): das helle Label im Bild wird gesucht, entzerrt, zugeschnitten
und hochskaliert, BEVOR die OCR läuft -> deutlich bessere effektive Auflösung. Greift für beide
Engines. Schlägt die Label-Suche fehl, wird das ganze (hochskalierte) Bild genommen.

Die Feld-Extraktion ist heuristisch (Herstellerliste + Regex); unsichere Fälle -> status="review"
(Human-in-the-Loop).
"""
from __future__ import annotations

import re
from typing import List, Optional

import cv2
import numpy as np

from config import config
from part_types import infer_part_type

_MANUFACTURERS = [
    "Seagate", "Western Digital", "WD", "Samsung", "Toshiba", "Hitachi", "HGST",
    "Kingston", "Crucial", "Corsair", "Micron", "SanDisk", "Intel", "SK Hynix",
    "Hynix", "G.Skill", "GSkill", "ADATA", "Transcend", "Maxtor", "Fujitsu",
    "LG", "LiteOn", "Lite-On", "Pioneer", "ASUS", "Patriot", "Team",
]
_CAP_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s?(TB|GB|MB)\b", re.IGNORECASE)
_SPEED_RE = re.compile(r"\b(DDR[2-5][- ]?\d{3,4}|PC[2-5][- ]?\d{3,5}|\d{3,4}\s?MHz)\b", re.IGNORECASE)
_MODEL_RE = re.compile(r"\b([A-Z]{2,}[A-Z0-9][A-Z0-9\-]{3,})\b")


# --------------------------------------------------------------------------- #
# Vorverarbeitung: Label finden + entzerren + hochskalieren
# --------------------------------------------------------------------------- #
def _upscale(img: np.ndarray, target: int = 1800) -> np.ndarray:
    """Auf mind. `target` px (lange Seite) hochskalieren – hilft EasyOCR bei kleiner Schrift.
    Nur hoch, nie runter."""
    h, w = img.shape[:2]
    m = max(h, w)
    if 0 < m < target:
        s = target / m
        img = cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_CUBIC)
    return img


def label_roi(frame_bgr: np.ndarray) -> np.ndarray:
    """Vorverarbeitung vor der OCR.

    Früher wurde hier das „hellste Rechteck" als Label ausgeschnitten – das griff bei hellem
    Hintergrund (Wand/Decke) daneben und lieferte Unsinn. EasyOCRs eigener Detektor (CRAFT) findet
    Textregionen im ganzen Bild selbst zuverlässiger; wir skalieren das Bild daher nur noch hoch
    (für mehr Pixel pro Zeichen) und schärfen leicht nach. Kein Zuschneiden mehr.
    """
    img = _upscale(frame_bgr)
    # leichtes Unsharp-Masking hebt Kanten kleiner Schrift an
    blur = cv2.GaussianBlur(img, (0, 0), 1.0)
    return cv2.addWeighted(img, 1.5, blur, -0.5, 0)


# --------------------------------------------------------------------------- #
# LabelReader
# --------------------------------------------------------------------------- #
class LabelReader:
    def __init__(self):
        self.enabled = config.OCR_ENABLED
        self.engine = config.OCR_ENGINE
        self._easy = None
        self._tess = None
        self._openai = False
        if not self.enabled:
            return

        if self.engine == "openai":
            if config.OPENAI_API_KEY:
                self._openai = True
                print(f"[ocr] OpenAI-Vision aktiv (model={config.OPENAI_MODEL}).")
                return
            print("[ocr] OCR_ENGINE=openai, aber kein OPENAI_API_KEY -> Fallback EasyOCR.")
            self.engine = "easyocr"

        if self.engine == "easyocr":
            try:
                import easyocr
                gpu = config.YOLO_DEVICE not in ("cpu", "", None)
                self._easy = easyocr.Reader(
                    ["en"], gpu=gpu,
                    model_storage_directory=config.EASYOCR_DIR,
                    download_enabled=True, verbose=False,
                )
                print(f"[ocr] EasyOCR bereit (gpu={gpu}).")
                return
            except Exception as e:
                print(f"[ocr] EasyOCR nicht verfügbar ({e}) -> Fallback Tesseract.")
                self.engine = "tesseract"

        # Tesseract (Fallback / explizit gewählt)
        try:
            import pytesseract
            self._tess = pytesseract
            _ = pytesseract.get_tesseract_version()
            print("[ocr] Tesseract bereit.")
        except Exception as e:
            print(f"[ocr] Tesseract nicht verfügbar ({e}) -> OCR liefert nur status 'review'.")
            self._tess = None

    def read(self, frame_bgr: Optional[np.ndarray]) -> dict:
        empty = {"raw_text": "", "manufacturer": None, "model": None,
                 "capacity": None, "speed": None, "type": None, "status": "review"}
        if frame_bgr is None or not self.enabled:
            return empty

        if self._openai:
            return self._openai_fields(frame_bgr)

        roi = label_roi(frame_bgr) if config.LABEL_CROP else frame_bgr

        if self._easy is not None:
            text = self._easy_text(roi)
        elif self._tess is not None:
            text = self._tess_text(roi)
        else:
            return empty

        fields = self._parse(text)
        fields["raw_text"] = text.strip()
        identified = bool(fields["manufacturer"]) or (fields["model"] and fields["capacity"])
        fields["status"] = "identified" if identified else "review"
        return fields

    def engine_text(self, img: np.ndarray) -> str:
        """Reine Textausgabe der aktiven Engine (für Debug/Vergleich crop vs. full)."""
        if self._easy is not None:
            return self._easy_text(img)
        if self._tess is not None:
            return self._tess_text(img)
        return ""

    # ---- OpenAI Vision (liest + parst in einem) ----
    _OPENAI_PROMPT = (
        "Du liest das Etikett eines ausgebauten PC-Bauteils (HDD, optisches Laufwerk oder RAM). "
        "Gib NUR ein JSON-Objekt zurueck mit den Feldern: manufacturer (Hersteller/Marke), "
        "model (Modell-/Teilenummer), capacity (z.B. '1TB' oder '16GB'), "
        "speed (z.B. 'PC4-3200AA' oder '7200rpm', sonst null), type ('HDD'|'optical'|'RAM'|null), "
        "raw_text (der gesamte lesbare Text auf dem Etikett). Unbekannte Felder = null. "
        "Antworte ausschliesslich mit dem JSON, ohne Erklaerung. Überprüfe, ob die Werte plausibel sind, vergleiche insbesondere Hersteller und Modellnummer, und korrigiere offensichtliche OCR-Fehler (z.B. 'O' vs. '0', 'I' vs. '1')."
    )

    def _openai_fields(self, img: np.ndarray) -> dict:
        import base64
        import json
        import requests

        empty = {"raw_text": "", "manufacturer": None, "model": None,
                 "capacity": None, "speed": None, "type": None, "status": "review"}
        ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
        if not ok:
            return empty
        b64 = base64.b64encode(buf.tobytes()).decode("ascii")
        payload = {
            "model": config.OPENAI_MODEL,
            "temperature": 0,
            "max_tokens": 500,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": self._OPENAI_PROMPT},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "high"}},
            ]}],
        }
        try:
            r = requests.post(
                f"{config.OPENAI_BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}"},
                json=payload, timeout=60,
            )
            if r.status_code != 200:
                print(f"[ocr] OpenAI HTTP {r.status_code}: {r.text[:200]}")
                return empty
            data = json.loads(r.json()["choices"][0]["message"]["content"])
        except Exception as e:
            print(f"[ocr] OpenAI-Fehler: {type(e).__name__}: {e}")
            return empty

        fields = {
            "manufacturer": data.get("manufacturer") or None,
            "model": data.get("model") or None,
            "capacity": (data.get("capacity") or data.get("speed")) or None,
            "speed": data.get("speed") or None,
            "type": data.get("type") or None,
            "raw_text": (data.get("raw_text") or "").strip(),
        }
        fields["type"] = infer_part_type(fields)
        identified = bool(fields["manufacturer"]) or (fields["model"] and fields["capacity"])
        fields["status"] = "identified" if identified else "review"
        return fields

    # ---- EasyOCR ----
    def _easy_text(self, img: np.ndarray) -> str:
        try:
            # Parameter auf kleine/kontrastarme Label-Schrift getrimmt:
            #   mag_ratio>1  -> internes Hochskalieren für die Text-Detektion
            #   low_text/text_threshold niedriger -> auch schwache/kleine Zeichen erfassen
            #   contrast_ths/adjust_contrast -> kontrastarme Bereiche nachschärfen
            results = self._easy.readtext(
                img, detail=1, paragraph=False,
                mag_ratio=2.0, text_threshold=0.5, low_text=0.3,
                contrast_ths=0.1, adjust_contrast=0.7,
            )
        except Exception as e:
            print(f"[ocr] EasyOCR-Fehler: {e}")
            return ""
        lines = [txt for (_box, txt, conf) in results if conf >= config.OCR_MIN_CONF]
        return "\n".join(lines)

    # ---- Tesseract (mehrere Vorverarbeitungen + Segmentierungsmodi) ----
    def _tess_text(self, img: np.ndarray) -> str:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
        gray = cv2.bilateralFilter(gray, 7, 50, 50)
        otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
        adap = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                     cv2.THRESH_BINARY, 35, 11)
        best, best_len = "", -1
        for variant in (gray, otsu, adap):
            for psm in (6, 11, 4):
                try:
                    t = self._tess.image_to_string(variant, lang=config.OCR_LANG, config=f"--psm {psm}")
                except Exception:
                    continue
                if len(t.strip()) > best_len:
                    best, best_len = t, len(t.strip())
        return best

    # ---- Feld-Extraktion ----
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
        candidates = [t for t in _MODEL_RE.findall(text) if not _CAP_RE.fullmatch(t)]
        if candidates:
            out["model"] = max(candidates, key=len)
        out["type"] = infer_part_type({**out, "raw_text": text})
        return out


import threading as _threading

_reader: Optional[LabelReader] = None
_reader_lock = _threading.Lock()


def get_reader() -> LabelReader:
    """Thread-sicherer Singleton. ACHTUNG: Die erste Konstruktion lädt EasyOCR/Torch (mehrere
    Sekunden) -> NICHT auf dem asyncio-Event-Loop aufrufen, sondern im Executor / beim Warmup,
    sonst friert WebRTC kurz ein."""
    global _reader
    with _reader_lock:
        if _reader is None:
            _reader = LabelReader()
        return _reader
