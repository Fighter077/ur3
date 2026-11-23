"""WebSocket-Hub: pro Client eine Verbindung für
  - Telemetrie  (Server -> Client): Roboterzustand, Detektionen, Handover, Video-Analyse
  - Steuerung    (Client -> Server): Bewegungs-/Greifer-/Stopp-Befehle

Befehle werden an den Roboter-Controller dispatcht. Der Hub kennt den Controller nur
über das abstrakte Interface.
"""
from __future__ import annotations

import asyncio
import json
from typing import Dict

from aiohttp import web, WSMsgType

import sim
from config import config
from camera_sources import remote_camera
from robot import get_controller, set_controller, RobotError
from robot.inspection import get_cell
from inventory import get_inventory

_connections: Dict[str, web.WebSocketResponse] = {}


def get_ws(client_id: str):
    return _connections.get(client_id)


async def broadcast(message: dict):
    """An alle verbundenen Clients senden (z. B. Roboter-Telemetrie)."""
    if not _connections:
        return
    data = json.dumps(message)
    dead = []
    for cid, ws in list(_connections.items()):
        if ws.closed:
            dead.append((cid, ws))
            continue
        try:
            await ws.send_str(data)
        except Exception:
            dead.append((cid, ws))
    for cid, ws in dead:
        if _connections.get(cid) is ws:
            _connections.pop(cid, None)


async def send_to(client_id: str, message: dict):
    ws = _connections.get(client_id)
    if ws is not None and not ws.closed:
        await ws.send_str(json.dumps(message))


async def notify_camera_sources():
    from rtc import active_camera_id

    for client_id, ws in list(_connections.items()):
        if ws.closed:
            continue
        source = remote_camera(client_id)
        try:
            await ws.send_json({
                "type": "camera_source",
                "sourceId": source.id if source else None,
                "network": source is not None and source.id == active_camera_id(),
            })
        except ConnectionError:
            pass


# --------------------------------------------------------------------------- #
# Befehls-Dispatch
# --------------------------------------------------------------------------- #
def _handle_command(cmd: dict, locate_grasp=None) -> dict:
    """Blockierender Robotercall -> in Executor ausgeführt. Gibt ein ack-dict zurück."""
    robot = get_controller()
    action = cmd.get("action")

    try:
        if action in ("move_tcp", "move_joints", "jog", "approach_until_contact",
                      "set_robot", "teach_pose", "gripper") and get_cell().running:
            raise RobotError("Inspection is running; abort it before manual control.")
        if action == "move_tcp":
            robot.move_tcp(cmd["pose"], cmd.get("speed"), cmd.get("accel"))
        elif action == "move_joints":
            robot.move_joints(cmd["q"], cmd.get("speed"), cmd.get("accel"))
        elif action == "jog":
            robot.jog_tcp(
                dx=cmd.get("dx", 0.0), dy=cmd.get("dy", 0.0), dz=cmd.get("dz", 0.0),
                drx=cmd.get("drx", 0.0), dry=cmd.get("dry", 0.0), drz=cmd.get("drz", 0.0),
                speed=cmd.get("speed"),
            )
        elif action == "stop":
            get_cell().abort()
        elif action == "gripper":
            robot.set_gripper(bool(cmd.get("closed", False)))
        elif action == "get_gripper_status":
            return {"type": "ack", "action": action, "ok": True, "result": robot.get_gripper_status()}
        elif action == "approach_until_contact":
            res = robot.approach_until_contact(
                cmd.get("direction", [0, 0, -1]),
                float(cmd.get("max_distance", 0.05)),
                cmd.get("force_threshold"),
            )
            return {"type": "ack", "action": action, "ok": True, "result": res}
        # --- Inspektion / Inventar ---
        elif action == "start_inspection":
            source = cmd.get("source", "manual")
            if source not in ("manual", "spacebar", "gesture", "handover"):
                raise RobotError("Unknown inspection trigger source")
            if source == "handover":
                if sim.control_mode() != "handover" or locate_grasp is None:
                    raise RobotError("A fresh, stable 3D grasp estimate is required.")
                res = get_cell().start(
                    cmd.get("part_type"), source=source, locate_grasp=locate_grasp,
                )
            else:
                res = get_cell().start(cmd.get("part_type"), source=source)
            return {"type": "ack", "action": action, "ok": res.get("ok", False), "result": res}
        elif action == "abort_inspection":
            res = get_cell().abort()
            return {"type": "ack", "action": action, "ok": True, "result": res}
        elif action == "teach_pose":
            res = get_cell().teach_pose(cmd.get("name", ""))
            return {"type": "ack", "action": action, "ok": res.get("ok", False), "result": res}
        elif action == "set_control_mode":
            mode = cmd.get("mode", "gesture")
            if mode not in ("gesture", "spacebar", "handover"):
                raise RobotError("Unknown control mode.")
            sim.set_control_mode(mode)
            return {"type": "ack", "action": action, "ok": True, "result": {"mode": mode}}
        elif action == "set_robot":
            res = set_controller(bool(cmd.get("mock", True)), cmd.get("ip"))
            return {"type": "ack", "action": action, "ok": res.get("ok", False), "result": res}
        elif action == "list_inventory":
            return {"type": "inventory", "records": get_inventory().list_records()}
        elif action == "mark_reviewed":
            fields = {"reviewed": True}
            for k in ("manufacturer", "model", "capacity", "condition", "status"):
                if k in cmd:
                    fields[k] = cmd[k]
            get_inventory().update_record(cmd["id"], fields)
            return {"type": "inventory", "records": get_inventory().list_records()}
        else:
            return {"type": "ack", "action": action, "ok": False, "error": "unknown action"}
        return {"type": "ack", "action": action, "ok": True}
    except (RobotError, KeyError, Exception) as e:
        return {"type": "ack", "action": action, "ok": False, "error": str(e)}


async def ws_handler(request: web.Request):
    from rtc import active_camera_id

    client_id = request.query.get("clientId")
    if not client_id:
        return web.Response(status=400, text="Missing clientId")

    ws = web.WebSocketResponse(heartbeat=20)
    await ws.prepare(request)
    ws["client_id"] = client_id
    _connections[client_id] = ws
    print(f"[ws] verbunden: {client_id}")

    # Initialzustand an den neuen Client: Roboterkonfiguration, Inventar und Inspektionsstatus.
    try:
        remote = remote_camera(client_id)
        await ws.send_str(json.dumps({
            "type": "robot_config",
            "default_ip": config.ROBOT_IP,
            "remote_camera_id": remote.id if remote else None,
            "remote_camera_selected": remote is not None and remote.id == active_camera_id(),
            "control_mode": sim.control_mode(),
        }))
        await ws.send_str(json.dumps({"type": "inventory", "records": get_inventory().list_records()}))
        await ws.send_str(json.dumps({"type": "inspection", **get_cell().state()}))
    except Exception:
        pass

    def locate_grasp():
        observation = ws.get("handover_observation")
        if ws.closed or _connections.get(client_id) is not ws or sim.control_mode() != "handover" or observation is None:
            return None
        return observation.grasp_candidate()

    loop = asyncio.get_running_loop()
    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                try:
                    cmd = json.loads(msg.data)
                except json.JSONDecodeError:
                    await ws.send_str(json.dumps({"type": "error", "error": "invalid json"}))
                    continue
                if cmd.get("action") == "select_camera":
                    from rtc import select_camera

                    network = cmd.get("network") is True
                    await select_camera(ws, network, use_local=not network)
                    remote = remote_camera(client_id)
                    await ws.send_json({
                        "type": "camera_selection",
                        "network": remote is not None and remote.id == active_camera_id(),
                    })
                    continue
                # Roboter-Call blockiert -> in Threadpool auslagern, Event-Loop frei halten
                ack = await loop.run_in_executor(None, _handle_command, cmd, locate_grasp)
                await ws.send_str(json.dumps(ack))
                if ack.get("action") == "set_control_mode" and ack.get("ok"):
                    mode = sim.control_mode()
                    for peer in _connections.values():
                        peer.pop("handover_observation", None)
                    await broadcast({"type": "control_mode", "mode": mode})
            elif msg.type == WSMsgType.ERROR:
                print(f"[ws] Fehler: {ws.exception()}")
    finally:
        if _connections.get(client_id) is ws:
            _connections.pop(client_id, None)
        print(f"[ws] getrennt: {client_id}")
    return ws


# --------------------------------------------------------------------------- #
# Telemetrie-Schleife: periodisch Roboterzustand an alle Clients
# --------------------------------------------------------------------------- #
async def telemetry_loop(app):
    loop = asyncio.get_running_loop()
    try:
        while True:
            # Controller jedes Mal frisch holen -> ein Laufzeit-Wechsel (Mock<->echt) greift sofort.
            state = await loop.run_in_executor(None, lambda: get_controller().get_state())
            await broadcast({"type": "robot_state", "state": state})
            await asyncio.sleep(0.1)  # 10 Hz
    except asyncio.CancelledError:
        pass


async def start_telemetry(app):
    app["telemetry_task"] = asyncio.create_task(telemetry_loop(app))


async def stop_telemetry(app):
    task = app.get("telemetry_task")
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
