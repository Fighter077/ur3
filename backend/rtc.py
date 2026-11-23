"""WebRTC-Kameraeingänge und weitergeleitete Vorschauen (aiortc).

Zwei Aufgaben pro Frame:
  1. Frame in den gemeinsamen Puffer legen (frames.set_frame) -> die Inspektionssequenz greift
     am Haltepunkt das aktuelle Bild des Teils ab (für Label-Foto/OCR).
  2. MediaPipe-Hände + Gesten-Trigger auswerten: eine stabile Trigger-Geste meldet sich beim
     zugehörigen Frontend, das denselben Startpfad wie Button/Leertaste benutzt.

Die Analyse verarbeitet jeweils nur das neueste Frame in ihrer Queue.
"""
from __future__ import annotations

import asyncio
import threading
import time

from aiohttp import web
from aiortc import MediaStreamError, RTCConfiguration, RTCPeerConnection, RTCSessionDescription

import frames
import sim
from config import config
from perception import Detector, HandDetector
from perception.gesture_trigger import GestureTrigger
from perception.handover import HandoverObservation
from perception.handover_geometry import load_handover_geometry
from camera_sources import CameraSource, remote_camera, sources
from websocket_hub import get_ws, notify_camera_sources, send_to
from robot.inspection import get_cell

pcs = set()
_network_camera_ws: web.WebSocketResponse | None = None
_local_camera_owner: str | None = None
_perception_lock = asyncio.Lock()
_model_lock = threading.Lock()
_DETECTION_INTERVAL_S = 0.1
_DETECTION_MAX_SIDE = 640

# Optional (default aus): YOLO-Objekterkennung, z. B. "liegt ein Teil im Halter?".
_objects = Detector() if config.OBJECTS_ENABLED else None
# Hände -> Trigger-Geste.
_hands = HandDetector() if config.HANDS_ENABLED else None
_handover_geometry = load_handover_geometry(config.DATA_DIR)


def set_handover_geometry(geometry) -> None:
    global _handover_geometry
    _handover_geometry = geometry


def _latest_put(queue: asyncio.Queue, frame) -> None:
    if queue.full():
        queue.get_nowait()
    queue.put_nowait(frame)


async def _answer(pc: RTCPeerConnection):
    await pc.setLocalDescription(await pc.createAnswer())
    return web.json_response({"sdp": pc.localDescription.sdp, "type": pc.localDescription.type})


async def offer(request: web.Request):
    params = await request.json()
    client_id = params["clientId"]
    ws = get_ws(client_id)
    if ws is None or ws.closed:
        raise web.HTTPBadRequest(text="An active control connection is required.")
    pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))
    pcs.add(pc)
    publisher = None

    @pc.on("connectionstatechange")
    async def on_state():
        if pc.connectionState == "failed":
            await pc.close()
        elif pc.connectionState == "closed":
            pcs.discard(pc)

    @pc.on("track")
    def on_track(track):
        nonlocal publisher
        if track.kind == "video" and publisher is None:
            publisher = asyncio.create_task(_publish(client_id, ws, pc, CameraSource(track)))

    try:
        await pc.setRemoteDescription(RTCSessionDescription(sdp=params["sdp"], type=params["type"]))
        if publisher is None:
            raise web.HTTPBadRequest(text="A video track is required.")
        return await _answer(pc)
    except BaseException:
        await pc.close()
        raise


async def _publish(client_id, ws, pc, source: CameraSource):
    global _local_camera_owner
    relay_track = source.relay.subscribe(source.track, buffered=False)
    latest = asyncio.Queue(maxsize=1)
    processor = None
    try:
        while not source.closed and not ws.closed and get_ws(client_id) is ws:
            frame = await asyncio.wait_for(relay_track.recv(), timeout=10)
            if not source.closed and not ws.closed and get_ws(client_id) is ws:
                source.update(frame)
                if processor is None:
                    previous = sources.get(client_id)
                    if previous is not None:
                        previous.close()
                    sources[client_id] = source
                    if _local_camera_owner is None:
                        _local_camera_owner = client_id
                    processor = asyncio.create_task(_process_frames(client_id, latest, source))
                    await notify_camera_sources()
                _latest_put(latest, frame)
    except (MediaStreamError, TimeoutError, ConnectionError):
        pass
    finally:
        relay_track.stop()
        source.close()
        if sources.get(client_id) is source:
            sources.pop(client_id)
            if _local_camera_owner == client_id:
                _local_camera_owner = next(iter(sources), None)
                if not network_camera_active():
                    frames.clear()
            ws.pop("handover_observation", None)
            await notify_camera_sources()
        if processor is not None:
            processor.cancel()
            await asyncio.gather(processor, return_exceptions=True)
        await pc.close()
        pcs.discard(pc)


def _perceive(frame):
    """Run inference on a smaller frame. The detectors return normalized coordinates."""
    scale = min(1, _DETECTION_MAX_SIDE / max(frame.width, frame.height))
    img = frame.reformat(
        width=max(1, round(frame.width * scale)),
        height=max(1, round(frame.height * scale)),
        format="bgr24",
    ).to_ndarray()
    with _model_lock:
        hands = _hands.detect(img) if _hands is not None else []
        objects = _objects.detect(img, track=False) if _objects is not None else []
        geometry = _handover_geometry
        marker = (geometry, geometry.detect(img, (frame.width, frame.height))) if (
            geometry is not None and sim.control_mode() == "handover"
        ) else None
    return hands, objects, marker


async def _detect_latest(source: CameraSource):
    # Local and relayed views share inference on the same physical camera.
    async with _perception_lock:
        frame = source.latest
        samples = source.detection
        if frame is None or (samples and (frame is samples[0] or time.monotonic() < source.next_detection_at)):
            return samples
        captured_at = source.updated_at
        hands, objects, marker = await asyncio.get_running_loop().run_in_executor(None, _perceive, frame)
        source.detection = (frame, captured_at, hands, objects, marker)
        source.next_detection_at = time.monotonic() + _DETECTION_INTERVAL_S
        return source.detection


async def _observe_handover(client_id: str, network: bool, hands: list, objects: list, captured_at: float, marker):
    ws = get_ws(client_id)
    if ws is None or ws.closed:
        return
    geometry = _handover_geometry
    if ws.get("handover_observation") is None or ws.get("handover_geometry") is not geometry:
        ws["handover_geometry"] = geometry
        ws["handover_observation"] = HandoverObservation(geometry)
    current_marker = marker[1] if marker is not None and marker[0] is geometry else None
    observation = ws["handover_observation"].update(network, objects, captured_at=captured_at, marker=current_marker)
    await send_to(client_id, {
        "type": "handover_observation", "network_camera": network, "hands": hands, **observation,
    })


async def _process_frames(client_id: str, latest: asyncio.Queue, source: CameraSource, network_camera: bool = False):
    loop = asyncio.get_running_loop()
    counter = 0
    last_ts = 0.0
    last_hands, last_objects = [], []
    last_detection = None
    errors = 0
    trigger = GestureTrigger()
    while True:
        try:
            frame = await latest.get()
            if frame is None:
                break
            selected = camera_is_selected(client_id, network_camera)
            observe = sim.control_mode() == "handover"
            if not selected and not observe:
                trigger = GestureTrigger()
                continue
            if selected:
                img = await loop.run_in_executor(None, lambda: frame.to_ndarray(format="bgr24"))
                if camera_is_selected(client_id, network_camera):
                    frames.set_frame(img)
            now = time.time() * 1000

            gesture_triggered = False
            detect = counter % config.DETECT_EVERY_N == 0
            counter += 1
            if detect:
                detection = await _detect_latest(source)
                detect = detection is not None and detection is not last_detection
                if detect:
                    last_detection = detection
                    _, captured_at, last_hands, last_objects, marker = detection
                    if observe and sim.control_mode() == "handover":
                        await _observe_handover(client_id, network_camera, last_hands, last_objects, captured_at, marker)
            if not camera_is_selected(client_id, network_camera):
                trigger = GestureTrigger()
                continue
            if detect:
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
                    await send_to(client_id, {"type": "gesture_trigger", "network_camera": network_camera})

            await send_to(client_id, {
                "type": "analysis",
                "network_camera": network_camera,
                "timestamp": now,
                "fps": (1000 / (now - last_ts)) if last_ts else 0,
                "hands": hands,
                "objects": objects,
                "sim": sim_state,
                "handover": handover,
            })

            last_ts = now
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


async def network_camera(request: web.Request):
    """Negotiate a receive-only preview of another browser's camera."""
    params = await request.json()
    client_id = params["clientId"]
    ws = get_ws(client_id)
    source = remote_camera(client_id)
    if ws is None or ws.closed:
        raise web.HTTPBadRequest(text="An active control connection is required.")
    if source is None or source.id != params.get("sourceId"):
        raise web.HTTPNotFound(text="Camera is no longer available.")
    previous = ws.get("network_camera_peer")
    if previous is not None:
        await previous.close()

    pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))
    pcs.add(pc)
    ws["network_camera_peer"] = pc
    ws["network_camera_ready"] = False
    ws["network_camera_source"] = source.id
    proxy = source.relay.subscribe(source.track, buffered=False)
    observer = None

    @pc.on("connectionstatechange")
    async def on_state():
        nonlocal observer
        if pc.connectionState == "connected" and observer is None:
            ws["network_camera_ready"] = True
            observer = asyncio.create_task(_observe_remote(client_id, ws, pc, source))
        elif pc.connectionState == "failed":
            await pc.close()
        elif pc.connectionState == "closed":
            proxy.stop()
            if ws.get("network_camera_peer") is pc:
                ws.pop("network_camera_peer", None)
                ws["network_camera_ready"] = False
                ws.pop("handover_observation", None)
                await select_camera(ws, False)
                if get_ws(client_id) is ws and not ws.closed:
                    await send_to(client_id, {"type": "network_camera_ended", "sourceId": source.id})
            pcs.discard(pc)

    try:
        await pc.setRemoteDescription(RTCSessionDescription(sdp=params["sdp"], type=params["type"]))
        pc.addTrack(proxy)
        return await _answer(pc)
    except BaseException:
        await pc.close()
        raise


async def _observe_remote(client_id, ws, pc, source):
    latest = asyncio.Queue(maxsize=1)
    processor = asyncio.create_task(_process_frames(client_id, latest, source, network_camera=True))
    frame = None
    try:
        while pc.connectionState == "connected" and not ws.closed and get_ws(client_id) is ws:
            frame = await asyncio.wait_for(source.next_frame(frame), timeout=10)
            if frame is None or pc.connectionState != "connected":
                break
            if _network_camera_ws is ws or sim.control_mode() == "handover":
                _latest_put(latest, frame)
    except TimeoutError:
        pass
    finally:
        processor.cancel()
        await asyncio.gather(processor, return_exceptions=True)
        await pc.close()


def network_camera_active() -> bool:
    ws = _network_camera_ws
    return ws is not None and not ws.closed


def active_camera_id() -> str | None:
    ws = _network_camera_ws
    if ws is not None and not ws.closed:
        return ws.get("network_camera_source")
    source = sources.get(_local_camera_owner)
    return source.id if source else None


def camera_is_selected(client_id: str, network_camera: bool) -> bool:
    if network_camera:
        return network_camera_active() and _network_camera_ws is get_ws(client_id)
    return not network_camera_active() and _local_camera_owner == client_id


async def select_camera(ws: web.WebSocketResponse, network: bool, *, use_local: bool = False) -> None:
    global _network_camera_ws, _local_camera_owner
    previous = _network_camera_ws
    if network and ws.get("network_camera_ready", False):
        if previous is not ws:
            _network_camera_ws = ws
            frames.clear()
            await notify_camera_sources()
        return
    if previous is ws or (use_local and ws["client_id"] in sources):
        active_before = active_camera_id()
        _network_camera_ws = None
        if ws["client_id"] in sources:
            _local_camera_owner = ws["client_id"]
        if active_camera_id() != active_before or previous is not None:
            frames.clear()
            await notify_camera_sources()


async def on_shutdown(app):
    await asyncio.gather(*[pc.close() for pc in pcs], return_exceptions=True)
    pcs.clear()
