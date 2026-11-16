"""Inspektions-Zelle: die Roboter-Sequenz des Posters als asynchrone State-Machine.

Ablauf (Spalte "UR3e" im Poster):
  idle --(Trigger: Geste/Übergabe/Button)--> PICK (Teil greifen)
       --> INSPECT (feste Posen vor der stationären Kamera, beide Seiten fotografieren)
       --> READ   (OCR auf den Label-Bildern -> Datensatz)
       --> SORT   (in Collection-Area ablegen; "unidentified" -> Review)
       --> idle

Bewegungen sind blockierend -> laufen im Executor, der Event-Loop bleibt frei. Zustände werden
per WebSocket gebroadcastet; der virtuelle Greifer (sim.py) wird pro Schritt gesetzt.

Posen werden aus config.POSES_FILE (JSON) geladen; fehlt die Datei, gelten die Platzhalter unten.
Am echten Roboter teacht man die Posen über `teach_pose()` (WS-Command "teach_pose") statt den
Code zu ändern. Alle Posen sind TCP-Posen [x,y,z,rx,ry,rz] (UR-Konvention, Meter + Rotvektor).
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import threading
from typing import Optional

import frames
import sim
from config import config
from inventory import get_inventory
from perception import get_reader
from robot import get_controller

PI = math.pi

# Platzhalter-Posen (Mock / wenn keine poses.json existiert). Am realen Aufbau einteachen.
DEFAULT_POSES = {
    "home":       [0.20, 0.00, 0.30, 0.0, PI, 0.0],
    "holder":     [0.30, -0.20, 0.06, 0.0, PI, 0.0],   # Teil im 3D-Halter
    "holder_up":  [0.30, -0.20, 0.20, 0.0, PI, 0.0],   # nach dem Greifen anheben
    "front":      [0.20, 0.00, 0.35, 0.0, PI, 0.0],    # Vorderseite vor Kamera
    "back":       [0.20, 0.00, 0.35, 0.0, PI, PI],     # 180° gedreht -> Rückseite
    "collection": [0.10, 0.25, 0.10, 0.0, PI, 0.0],    # Ablage / Collection-Area
}
POSE_NAMES = list(DEFAULT_POSES.keys())


class InspectionCell:
    def __init__(self):
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._lock = threading.Lock()
        self._running = False
        self._state = {"state": "idle", "step": "", "part_type": None,
                       "progress": 0.0, "record_id": None, "error": None}
        self.poses = self._load_poses()

    # ---- Posen (Datei) ----
    def _load_poses(self) -> dict:
        poses = dict(DEFAULT_POSES)
        try:
            with open(config.POSES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            for k, v in data.items():
                if k in DEFAULT_POSES and isinstance(v, list) and len(v) == 6:
                    poses[k] = [float(x) for x in v]
            print(f"[inspection] Posen aus {config.POSES_FILE} geladen: {sorted(data.keys())}")
        except FileNotFoundError:
            print(f"[inspection] keine {config.POSES_FILE} -> Platzhalter-Posen (Mock).")
        except Exception as e:
            print(f"[inspection] Posen-Datei fehlerhaft ({e}) -> Platzhalter-Posen.")
        return poses

    def _save_poses(self) -> None:
        tmp = config.POSES_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.poses, f, indent=2)
        os.replace(tmp, config.POSES_FILE)

    def teach_pose(self, name: str) -> dict:
        """Aktuelle TCP-Pose des (echten oder Mock-)Roboters als `name` speichern."""
        if name not in DEFAULT_POSES:
            return {"ok": False, "error": f"unbekannte Pose '{name}'", "names": POSE_NAMES}
        pose = get_controller().get_state().get("tcp_pose")
        if not pose or len(pose) != 6:
            return {"ok": False, "error": "keine gültige TCP-Pose vom Roboter"}
        self.poses[name] = [float(x) for x in pose]
        self._save_poses()
        print(f"[inspection] Pose '{name}' geteacht: {self.poses[name]}")
        return {"ok": True, "name": name, "pose": self.poses[name]}

    # ---- Anbindung ----
    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def state(self) -> dict:
        with self._lock:
            return dict(self._state)

    @property
    def running(self) -> bool:
        with self._lock:
            return self._running

    def start(self, part_type: Optional[str] = None, source: str = "manual") -> dict:
        part_type = part_type or (config.PART_TYPES[0] if config.PART_TYPES else "HDD")
        with self._lock:
            if self._running:
                return {"ok": False, "error": "inspection already running"}
            if self._loop is None:
                return {"ok": False, "error": "cell not bound to event loop"}
            self._running = True
        asyncio.run_coroutine_threadsafe(self._run_cycle(part_type, source), self._loop)
        return {"ok": True, "part_type": part_type, "source": source}

    # ---- Capture + OCR über mehrere Frames im Zeitfenster ----
    async def _capture_side(self, loop, reader) -> tuple:
        best, best_frame, best_score = None, None, -1
        n = max(1, config.OCR_ATTEMPTS)
        for _ in range(n):
            await asyncio.sleep(config.INSPECT_SETTLE_S / n)
            fr = frames.get_frame()
            res = await loop.run_in_executor(None, reader.read, fr)
            score = (10 if res.get("status") == "identified" else 0) + len(res.get("raw_text", ""))
            if score > best_score:
                best, best_frame, best_score = res, fr, score
        return (best or reader.read(None)), best_frame

    # ---- Zyklus ----
    async def _run_cycle(self, part_type: str, source: str):
        loop = asyncio.get_running_loop()
        robot = get_controller()
        reader = get_reader()
        inv = get_inventory()

        def move(name):
            robot.move_tcp(self.poses[name])

        try:
            await self._set("PICK", "pick", part_type, 0.05)
            if source == "handover":
                sim.set_state(mode="inspect", holding=True, opening=0.0, action="Teil übernommen")
                await asyncio.sleep(config.STEP_PAUSE_S)
            else:
                sim.set_state(mode="inspect", x=0.75, y=0.62, holding=False, opening=1.0,
                              action="greift Teil aus Halter")
                await loop.run_in_executor(None, move, "holder")
                await loop.run_in_executor(None, robot.set_gripper, True)
                sim.set_state(holding=True, opening=0.0)
                await asyncio.sleep(config.STEP_PAUSE_S)
                await loop.run_in_executor(None, move, "holder_up")

            # --- Inspektion: Vorderseite ---
            await self._set("INSPECT", "front", part_type, 0.30)
            sim.set_state(mode="inspect", x=0.5, y=0.38, holding=True, opening=0.0,
                          action="Vorderseite vor Kamera – Label zeigen")
            await loop.run_in_executor(None, move, "front")
            res_front, front = await self._capture_side(loop, reader)

            # --- Inspektion: Rückseite ---
            await self._set("INSPECT", "back", part_type, 0.55)
            sim.set_state(action="Rückseite (180° gedreht) – Label zeigen")
            await loop.run_in_executor(None, move, "back")
            res_back, back = await self._capture_side(loop, reader)

            # --- OCR-Ergebnis / Datensatz ---
            await self._set("READ", "ocr", part_type, 0.75)
            sim.set_state(action="wertet Label aus (OCR)…")
            await asyncio.sleep(config.STEP_PAUSE_S)
            best = res_front if res_front.get("status") == "identified" else (
                res_back if res_back.get("status") == "identified" else res_front)

            rec = inv.add_record({
                "type": part_type,
                "manufacturer": best.get("manufacturer"),
                "model": best.get("model"),
                "capacity": best.get("capacity") or best.get("speed"),
                "condition": "unrated",
                "ocr_text": (res_front.get("raw_text", "") + "\n---\n" + res_back.get("raw_text", "")).strip(),
                "status": best.get("status", "review"),
                "source": source,
            })
            img_front = inv.save_image(rec["id"], "front", front)
            img_back = inv.save_image(rec["id"], "back", back)
            inv.update_record(rec["id"], {"images": {"front": img_front, "back": img_back}})
            await self._broadcast_inventory()
            with self._lock:
                self._state["record_id"] = rec["id"]

            # --- Sortieren / Ablage ---
            identified = best.get("status") == "identified"
            await self._set("SORT", "collection" if identified else "review",
                            part_type, 0.90, record_id=rec["id"])
            sim.set_state(mode="inspect", x=0.2 if identified else 0.8, y=0.72, holding=True,
                          action="sortiert ab" if identified else "Review-Ablage")
            await asyncio.sleep(config.STEP_PAUSE_S)
            await loop.run_in_executor(None, move, "collection")
            await loop.run_in_executor(None, robot.set_gripper, False)
            sim.set_state(holding=False, opening=1.0, action="abgelegt")
            await asyncio.sleep(config.STEP_PAUSE_S)
            await loop.run_in_executor(None, move, "home")

            await self._set("DONE", "done", part_type, 1.0, record_id=rec["id"])
            await asyncio.sleep(config.STEP_PAUSE_S)
        except Exception as e:
            await self._set("ERROR", "error", part_type, 0.0, error=str(e))
            try:
                await loop.run_in_executor(None, robot.stop)
            except Exception:
                pass
        finally:
            with self._lock:
                self._running = False
            sim.set_state(mode="ready", x=sim.READY[0], y=sim.READY[1], holding=False,
                          opening=1.0, action="bereit")
            await self._set("idle", "", None, 0.0)

    # ---- Zustand / Broadcast ----
    async def _set(self, state, step, part_type, progress, record_id=None, error=None):
        with self._lock:
            self._state.update({"state": state, "step": step, "part_type": part_type,
                                "progress": progress, "error": error})
            if record_id is not None:
                self._state["record_id"] = record_id
            snap = dict(self._state)
        from websocket_hub import broadcast  # lazy -> kein Zirkelimport
        await broadcast({"type": "inspection", **snap})

    async def _broadcast_inventory(self):
        from websocket_hub import broadcast
        await broadcast({"type": "inventory", "records": get_inventory().list_records()})


_cell: Optional[InspectionCell] = None


def get_cell() -> InspectionCell:
    global _cell
    if _cell is None:
        _cell = InspectionCell()
    return _cell
