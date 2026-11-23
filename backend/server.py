"""Einstiegspunkt: aiohttp-App mit WebRTC-Offer, Steuer-/Telemetrie-WebSocket und Health.

Routen:
  POST /offer            WebRTC-SDP-Austausch (Kamera -> Backend)
  POST /network-camera   WebRTC-SDP-Austausch (Backend -> zweite Vorschau)
  GET  /ws?clientId=...  bidirektional: Telemetrie raus, Steuerbefehle rein
  GET  /health           Liveness + Roboterstatus
"""
from __future__ import annotations

import asyncio
from base64 import b64encode
import os
import threading

import aiohttp_cors
from aiohttp import ClientError, ClientSession, ClientTimeout
from aiohttp import web
import numpy as np

from config import config
from rtc import camera_is_selected, network_camera, network_camera_active, offer, on_shutdown
from websocket_hub import ws_handler, start_telemetry, stop_telemetry
from robot import MockController, get_controller
from robot.inspection import get_cell
from inventory import get_inventory


async def interface_qr(request):
    import cv2

    try:
        async with ClientSession(timeout=ClientTimeout(total=2)) as session:
            async with session.get("http://127.0.0.1:8444/interface-url", allow_redirects=False) as response:
                response.raise_for_status()
                url = (await response.text()).strip()
    except (ClientError, TimeoutError) as exc:
        raise web.HTTPServiceUnavailable(text="HTTPS service unavailable.") from exc

    modules = cv2.QRCodeEncoder_create().encode(url)
    bordered = cv2.copyMakeBorder(modules, 4, 4, 4, 4, cv2.BORDER_CONSTANT, value=255)
    image = cv2.resize(bordered, None, fx=8, fy=8, interpolation=cv2.INTER_NEAREST)
    ok, png = cv2.imencode(".png", image)
    if not ok:
        raise web.HTTPInternalServerError(text="Could not encode QR image.")
    return web.json_response({"url": url, "png": b64encode(png).decode("ascii")})


async def health(request):
    robot = get_controller()
    return web.json_response({
        "status": "ok",
        "robot_connected": robot.is_connected(),
        "mock": isinstance(robot, MockController),
    })


def _decode_jpeg(data: bytes):
    import cv2
    import numpy as _np
    if not data:
        return None
    return cv2.imdecode(_np.frombuffer(data, _np.uint8), cv2.IMREAD_COLOR)


async def snapshot(request):
    """Full-Res-Standbild (JPEG im Body) vom Client entgegennehmen und als Hi-Res-Frame für die
    OCR ablegen. Der Client schickt das regelmäßig, damit die Inspektion ein scharfes Bild hat."""
    data = await request.read()
    img = await asyncio.get_running_loop().run_in_executor(None, _decode_jpeg, data)
    if img is None:
        return web.json_response({"ok": False}, status=400)
    import frames
    client_id = request.query.get("clientId")
    selected = camera_is_selected(client_id, False) if client_id else not network_camera_active()
    if selected:
        frames.set_hires(img)
    return web.json_response({"ok": True, "shape": list(img.shape)})


async def debug_ocr(request):
    """Diagnose: aktuelles Kamerabild + Label-Ausschnitt als JPG speichern und OCR beider Varianten
    zurückgeben. Mit verbundener Kamera (Label vors Objektiv halten) aufrufen: GET /debug/ocr
    Die Bilder liegen danach unter data/debug/ (auch via /data/debug/*.jpg abrufbar)."""
    import cv2
    import frames
    from perception import get_reader
    from perception.ocr import label_roi

    loop = asyncio.get_running_loop()
    frame = frames.best_for_ocr()
    if frame is None:
        return web.json_response({"error": "kein Kamerabild – erst im Frontend verbinden"}, status=400)
    src = "hi-res-Standbild" if frames.get_hires() is not None else "WebRTC-Frame"

    dbg = os.path.join(config.DATA_DIR, "debug")
    os.makedirs(dbg, exist_ok=True)
    proc = await loop.run_in_executor(None, label_roi, frame)
    cv2.imwrite(os.path.join(dbg, "frame.jpg"), frame)
    cv2.imwrite(os.path.join(dbg, "proc.jpg"), proc)

    reader = await loop.run_in_executor(None, get_reader)
    parsed = await loop.run_in_executor(None, reader.read, frame)

    return web.json_response({
        "engine": reader.engine,
        "quelle": src,
        "frame_shape": list(frame.shape),
        "proc_shape": list(proc.shape),
        "parsed": {k: parsed.get(k) for k in
                   ("manufacturer", "model", "capacity", "speed", "status", "raw_text")},
        "images": {"frame": "/data/debug/frame.jpg", "processed": "/data/debug/proc.jpg"},
    })


async def bind_cell(app):
    # Die Inspektions-Zelle braucht den laufenden Event-Loop, um aus dem WS-Command-Thread
    # (oder von der Gesten-Erkennung) heraus Zyklen zu planen.
    get_cell().bind_loop(asyncio.get_running_loop())


async def warm_ocr(app):
    """OCR-Engine (EasyOCR/Torch) im Hintergrund-Thread laden + einmal durchlaufen lassen.
    So blockiert die mehrsekündige Initialisierung weder den Start noch – beim ersten Zyklus –
    den Event-Loop (sonst reißt WebRTC kurz ab)."""
    def _warm():
        try:
            from perception import get_reader
            reader = get_reader()
            # Lokale Modelle (EasyOCR/Torch) vorladen; bei OpenAI KEIN API-Call aufs Leerbild.
            if not getattr(reader, "_openai", False):
                reader.read(np.zeros((64, 200, 3), dtype=np.uint8))
            print("[ocr] vorgewaermt – bereit fuer den ersten Zyklus.")
        except Exception as e:
            print(f"[ocr] Warmup-Fehler (unkritisch): {e}")
    threading.Thread(target=_warm, name="ocr-warmup", daemon=True).start()


def create_app() -> web.Application:
    app = web.Application()

    app.on_startup.append(bind_cell)
    app.on_startup.append(warm_ocr)
    app.on_startup.append(start_telemetry)
    app.on_shutdown.append(stop_telemetry)
    app.on_shutdown.append(on_shutdown)

    cors = aiohttp_cors.setup(app, defaults={
        "*": aiohttp_cors.ResourceOptions(
            allow_credentials=True, expose_headers="*", allow_headers="*",
        )
    })
    cors.add(app.router.add_post("/offer", offer))
    cors.add(app.router.add_post("/network-camera", network_camera))
    cors.add(app.router.add_get("/ws", ws_handler))
    cors.add(app.router.add_get("/health", health))
    cors.add(app.router.add_get("/interface-qr", interface_qr))
    cors.add(app.router.add_post("/snapshot", snapshot))
    cors.add(app.router.add_get("/debug/ocr", debug_ocr))

    # Label-Bilder (Digital Product Passport) statisch ausliefern (<img>-Anzeige im Frontend).
    get_inventory()  # legt DATA_DIR/images an
    os.makedirs(os.path.join(config.DATA_DIR, "images"), exist_ok=True)
    app.router.add_static("/data/", config.DATA_DIR, show_index=False)
    return app


if __name__ == "__main__":
    # Roboter früh initialisieren. Bei Verbindungsfehlern startet die Simulation.
    get_controller()
    app = create_app()
    print(f"[server] läuft auf http://{config.HOST}:{config.PORT}")
    web.run_app(app, host=config.HOST, port=config.PORT)
