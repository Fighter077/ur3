"""WebRTC-Empfang (aiortc): nimmt den Stream der stationären Kamera an.

Zwei Aufgaben pro Frame:
  1. Frame in den gemeinsamen Puffer legen (frames.set_frame) -> die Inspektionssequenz greift
     am Haltepunkt das aktuelle Bild des Teils ab (für Label-Foto/OCR).
  2. MediaPipe-Hände + Gesten-Trigger auswerten: eine stabile Trigger-Geste meldet sich beim
     zugehörigen Frontend, das denselben Startpfad wie Button/Leertaste benutzt.

Struktur nahe der trafficSigns-Referenz (frame_reader + Queue der Größe 1 = immer nur das
neueste Frame verarbeiten).
"""
from __future__ import annotations

import asyncio
import time

from aiohttp import web
from aiortc import RTCPeerConnection, RTCSessionDescription, MediaStreamError
from aiortc.contrib.media import MediaRelay

import frames
import sim
from config import config
from perception import Detector, HandDetector
from perception.gesture_trigger import GestureTrigger
from websocket_hub import send_to
from robot.inspection import get_cell

pcs = set()

# Optional (default aus): YOLO-Objekterkennung, z. B. "liegt ein Teil im Halter?".
_objects = Detector() if config.ROBOT_ENABLED and config.OBJECTS_ENABLED else None
# Hände -> Trigger-Geste.
_hands = HandDetector() if config.ROBOT_ENABLED and config.HANDS_ENABLED else None


async def offer(request: web.Request):
    params = await request.json()
    client_id = params["clientId"]
    desc = RTCSessionDescription(sdp=params["sdp"], type=params["type"])

    pc = RTCPeerConnection()
    pcs.add(pc)
    relay = MediaRelay()

    @pc.on("connectionstatechange")
    async def on_state():
        print(f"[rtc] {client_id} state={pc.connectionState}")
        if pc.connectionState in ("failed", "closed"):
            await pc.close()
            pcs.discard(pc)

    @pc.on("track")
    def on_track(track):
        print(f"[rtc] Track empfangen: {track.kind}")
        if track.kind != "video":
            return
        local = relay.subscribe(track)
        latest = asyncio.Queue(maxsize=1)
        asyncio.ensure_future(_frame_reader(local, latest))
        asyncio.ensure_future(_process_frames(client_id, latest))

    await pc.setRemoteDescription(desc)
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)
    return web.json_response({"sdp": pc.localDescription.sdp, "type": pc.localDescription.type})


async def _frame_reader(track, latest: asyncio.Queue):
    while True:
        try:
            frame = await track.recv()
            if latest.full():
                try:
                    latest.get_nowait()
                except Exception:
                    pass
            await latest.put(frame)
        except Exception as e:
            print(f"[rtc] frame reader beendet: {e}")
            break


def _perceive(img):
    """Blockierend (im Executor): Hände (+ optional Objekte) auswerten."""
    hands = _hands.detect(img) if _hands is not None else []
    objects = _objects.detect(img) if _objects is not None else []
    return hands, objects


async def _process_frames(client_id: str, latest: asyncio.Queue):
    loop = asyncio.get_running_loop()
    counter = 0
    last_ts = 0.0
    last_hands, last_objects = [], []
    errors = 0
    trigger = GestureTrigger()
    while True:
        try:
            frame = await latest.get()
            img = frame.to_ndarray(format="bgr24")
            frames.set_frame(img)  # für die Inspektionssequenz
            if not config.ROBOT_ENABLED:
                continue
            now = time.time() * 1000

            gesture_triggered = False
            if counter % config.DETECT_EVERY_N == 0:
                last_hands, last_objects = await loop.run_in_executor(None, _perceive, img)
                gesture_triggered = trigger.update(
                    last_hands if sim.gesture_enabled() else []
                )["triggered"]
            hands, objects = last_hands, last_objects

            # Virtueller Greifer: im Leerlauf der Hand folgen + Übergabe erkennen; während eines
            # Zyklus steuert die State-Machine (robot/inspection.py) den Greifer -> hier nur lesen.
            if get_cell().running:
                sim_state = sim.snapshot()
                handover = {"active": False, "progress": 1.0}
            else:
                sim_state, _triggered, progress = sim.update_idle(hands)
                handover = {"active": bool(hands), "progress": progress}
                if gesture_triggered:
                    # Gleicher Frontend-Startpfad wie Button/Leertaste, inklusive Teiletyp.
                    await send_to(client_id, {"type": "gesture_trigger"})

            await send_to(client_id, {
                "type": "analysis",
                "timestamp": now,
                "fps": (1000 / (now - last_ts)) if last_ts else 0,
                "hands": hands,
                "objects": objects,
                "sim": sim_state,
                "handover": handover,
            })

            last_ts = now
            counter += 1
            errors = 0
        except MediaStreamError:
            print("[rtc] Stream normal beendet.")
            break
        except Exception as e:
            errors += 1
            if errors <= 3 or errors % 50 == 0:
                print(f"[rtc] Frame übersprungen ({errors}): {e}")
            if errors > 300:
                print("[rtc] Zu viele Fehler in Folge -> Verarbeitung beendet.")
                break


async def on_shutdown(app):
    await asyncio.gather(*[pc.close() for pc in pcs], return_exceptions=True)
    pcs.clear()
