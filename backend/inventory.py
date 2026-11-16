"""Inventar / Digital Product Passport (DPP).

Persistiert pro inspiziertem Bauteil einen Datensatz + die aufgenommenen Label-Bilder (beide
Seiten). Format bewusst einfach (JSON + JPGs in DATA_DIR), damit es ohne DB läuft und sich der
Ordner leicht sichern/ansehen lässt. Thread-sicher (Inspektion schreibt, WebSocket liest).

Datensatz-Felder (vgl. Poster "record 0001"):
  id, type (HDD/optical/RAM), manufacturer, model, capacity, condition,
  images {front, back} (URLs unter /data/...), ocr_text, status (identified|review),
  created_at, reviewed (bool)
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import List, Optional

import cv2

from config import config


class Inventory:
    def __init__(self, data_dir: str):
        self.dir = data_dir
        self.img_dir = os.path.join(data_dir, "images")
        os.makedirs(self.img_dir, exist_ok=True)
        self.file = os.path.join(data_dir, "inventory.json")
        self._lock = threading.Lock()
        self._records: List[dict] = self._load()
        self._seq = max((r.get("seq", 0) for r in self._records), default=0)

    def _load(self) -> List[dict]:
        try:
            with open(self.file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def _save(self) -> None:
        tmp = self.file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._records, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.file)

    def save_image(self, record_id: str, side: str, frame_bgr) -> Optional[str]:
        """Speichert ein Label-Bild und gibt den (web-)Pfad zurück, oder None ohne Frame."""
        if frame_bgr is None:
            return None
        name = f"{record_id}_{side}.jpg"
        cv2.imwrite(os.path.join(self.img_dir, name), frame_bgr)
        return f"/data/images/{name}"  # vom Backend statisch ausgeliefert

    def add_record(self, rec: dict) -> dict:
        with self._lock:
            self._seq += 1
            rec = dict(rec)
            rec["seq"] = self._seq
            rec.setdefault("id", f"{self._seq:04d}")
            rec.setdefault("created_at", time.time() * 1000)
            rec.setdefault("reviewed", False)
            rec.setdefault("status", "review")
            self._records.append(rec)
            self._save()
            return rec

    def update_record(self, rec_id: str, fields: dict) -> Optional[dict]:
        with self._lock:
            for r in self._records:
                if r["id"] == rec_id:
                    r.update(fields)
                    self._save()
                    return r
        return None

    def list_records(self) -> List[dict]:
        with self._lock:
            return list(reversed(self._records))  # neueste zuerst


_inv: Optional[Inventory] = None


def get_inventory() -> Inventory:
    global _inv
    if _inv is None:
        _inv = Inventory(config.DATA_DIR)
        print(f"[inventory] DPP-Speicher: {os.path.abspath(config.DATA_DIR)}")
    return _inv
