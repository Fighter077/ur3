"""Einstiegspunkt: aiohttp-App mit WebRTC-Offer, Steuer-/Telemetrie-WebSocket und Health.

Routen:
  POST /offer            WebRTC-SDP-Austausch (Kamera -> Backend)
  GET  /ws?clientId=...  bidirektional: Telemetrie raus, Steuerbefehle rein
  GET  /health           Liveness + Roboterstatus
"""
from __future__ import annotations

import asyncio
import os

import aiohttp_cors
from aiohttp import web

from config import config
from rtc import offer, on_shutdown
from websocket_hub import ws_handler, start_telemetry, stop_telemetry
from robot import get_controller
from robot.inspection import get_cell
from inventory import get_inventory


async def health(request):
    robot = get_controller()
    return web.json_response({
        "status": "ok",
        "robot_connected": robot.is_connected(),
        "mock": config.ROBOT_MOCK,
    })


async def bind_cell(app):
    # Die Inspektions-Zelle braucht den laufenden Event-Loop, um aus dem WS-Command-Thread
    # (oder von der Gesten-Erkennung) heraus Zyklen zu planen.
    get_cell().bind_loop(asyncio.get_running_loop())


def create_app() -> web.Application:
    app = web.Application()

    app.on_startup.append(bind_cell)
    app.on_startup.append(start_telemetry)
    app.on_shutdown.append(stop_telemetry)
    app.on_shutdown.append(on_shutdown)

    cors = aiohttp_cors.setup(app, defaults={
        "*": aiohttp_cors.ResourceOptions(
            allow_credentials=True, expose_headers="*", allow_headers="*",
        )
    })
    cors.add(app.router.add_post("/offer", offer))
    cors.add(app.router.add_get("/ws", ws_handler))
    cors.add(app.router.add_get("/health", health))

    # Label-Bilder (Digital Product Passport) statisch ausliefern (<img>-Anzeige im Frontend).
    get_inventory()  # legt DATA_DIR/images an
    os.makedirs(os.path.join(config.DATA_DIR, "images"), exist_ok=True)
    app.router.add_static("/data/", config.DATA_DIR, show_index=False)
    return app


if __name__ == "__main__":
    # Roboter früh initialisieren, damit Verbindungsfehler sofort sichtbar sind.
    get_controller()
    app = create_app()
    print(f"[server] läuft auf http://{config.HOST}:{config.PORT}")
    web.run_app(app, host=config.HOST, port=config.PORT)
