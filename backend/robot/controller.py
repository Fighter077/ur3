"""UR3e-Ansteuerung.

Abstrakte Basis `RobotController` mit zwei Implementierungen:
  - `MockController`:  simuliert den Roboter, keine Hardware nötig (Entwicklung / CI / Demo).
  - `RTDEController`:  echte Ansteuerung via ur_rtde (RTDE-Schnittstelle des UR3e).

Die Web-/WebRTC-Schicht kennt nur das abstrakte Interface -> Hardware ist austauschbar.
Alle Posen sind TCP-Posen [x, y, z, rx, ry, rz] (Meter + Rotationsvektor, UR-Konvention).
Alle Gelenkwinkel sind 6er-Listen in Radiant (base..wrist3).
"""
from __future__ import annotations

import math
import threading
import time
from abc import ABC, abstractmethod
from typing import List, Optional

from config import config


class RobotError(RuntimeError):
    pass


class RobotController(ABC):
    """Gemeinsames Interface. Thread-sicher halten die Implementierungen selbst."""

    # ---- Zustand ----
    @abstractmethod
    def get_state(self) -> dict:
        """TCP-Pose, Gelenke, TCP-Kraft, Betriebsmodus etc. als JSON-fähiges dict."""

    @abstractmethod
    def is_connected(self) -> bool: ...

    # ---- Bewegung ----
    @abstractmethod
    def move_tcp(self, pose: List[float], speed: Optional[float] = None,
                 accel: Optional[float] = None) -> None:
        """Lineare Bewegung (MoveL) zu absoluter TCP-Pose."""

    @abstractmethod
    def move_joints(self, q: List[float], speed: Optional[float] = None,
                    accel: Optional[float] = None) -> None:
        """Gelenkbewegung (MoveJ) zu absoluten Gelenkwinkeln."""

    @abstractmethod
    def jog_tcp(self, dx=0.0, dy=0.0, dz=0.0, drx=0.0, dry=0.0, drz=0.0,
                speed: Optional[float] = None) -> None:
        """Kontinuierliches Verfahren im TCP-Frame (Tool-Geschwindigkeit, speedL)."""

    @abstractmethod
    def stop(self) -> None:
        """Sofortiger, kontrollierter Stopp jeder Bewegung."""

    # ---- Kraftgeregelt (Kern des FlexDA-Use-Case) ----
    @abstractmethod
    def approach_until_contact(self, direction: List[float], max_distance: float,
                               force_threshold: Optional[float] = None) -> dict:
        """Langsam in `direction` (Einheitsvektor im Basis-Frame) fahren, bis die
        gemessene TCP-Kraft `force_threshold` [N] überschreitet oder `max_distance`
        [m] erreicht ist. Gibt zurück, ob Kontakt erkannt wurde."""

    # ---- Greifer (Platzhalter bis Endeffektor feststeht) ----
    @abstractmethod
    def set_gripper(self, closed: bool) -> None: ...

    def close(self) -> None:  # optional override
        pass


# --------------------------------------------------------------------------- #
# Mock
# --------------------------------------------------------------------------- #
class MockController(RobotController):
    """Simuliert den UR3e. Bewegungen werden interpoliert, Kräfte als Rauschen erzeugt."""

    HOME_POSE = [0.2, 0.0, 0.3, 0.0, math.pi, 0.0]
    HOME_Q = [0.0, -math.pi / 2, 0.0, -math.pi / 2, 0.0, 0.0]

    def __init__(self):
        self._lock = threading.Lock()
        self._pose = list(self.HOME_POSE)
        self._q = list(self.HOME_Q)
        self._gripper_closed = False
        self._moving = False
        self._jog = [0.0] * 6
        self._last = time.time()

    def _integrate(self):
        now = time.time()
        dt = now - self._last
        self._last = now
        # kontinuierliches Jog integrieren
        for i in range(6):
            self._pose[i] += self._jog[i] * dt

    def get_state(self) -> dict:
        with self._lock:
            self._integrate()
            # leichtes Kraftrauschen zur Visualisierung
            noise = (time.time() % 1.0) * 0.5
            return {
                "connected": True,
                "mock": True,
                "tcp_pose": [round(v, 4) for v in self._pose],
                "joints": [round(v, 4) for v in self._q],
                "tcp_force": [round(noise, 3), 0.0, 0.0, 0.0, 0.0, 0.0],
                "tcp_force_norm": round(noise, 3),
                "gripper_closed": self._gripper_closed,
                "moving": self._moving or any(abs(j) > 1e-9 for j in self._jog),
                "robot_mode": "RUNNING",
                "safety_mode": "NORMAL",
            }

    def is_connected(self) -> bool:
        return True

    def move_tcp(self, pose, speed=None, accel=None):
        with self._lock:
            self._pose = [float(v) for v in pose]
            self._jog = [0.0] * 6

    def move_joints(self, q, speed=None, accel=None):
        with self._lock:
            self._q = [float(v) for v in q]
            self._jog = [0.0] * 6

    def jog_tcp(self, dx=0.0, dy=0.0, dz=0.0, drx=0.0, dry=0.0, drz=0.0, speed=None):
        with self._lock:
            self._jog = [dx, dy, dz, drx, dry, drz]

    def stop(self):
        with self._lock:
            self._jog = [0.0] * 6
            self._moving = False

    def approach_until_contact(self, direction, max_distance, force_threshold=None):
        # simuliert Kontakt nach halber Strecke
        with self._lock:
            n = math.sqrt(sum(d * d for d in direction)) or 1.0
            step = max_distance * 0.5
            for i in range(3):
                self._pose[i] += direction[i] / n * step
            return {"contact": True, "distance": step, "force": force_threshold or config.ROBOT_FORCE_THRESHOLD}

    def set_gripper(self, closed: bool):
        with self._lock:
            self._gripper_closed = bool(closed)


# --------------------------------------------------------------------------- #
# RTDE (echte Hardware)
# --------------------------------------------------------------------------- #
class RTDEController(RobotController):
    """Echte Ansteuerung via `ur_rtde`. Wird nur importiert, wenn tatsächlich genutzt."""

    def __init__(self, ip: str):
        try:
            import rtde_control
            import rtde_receive
        except ImportError as e:  # pragma: no cover
            raise RobotError(
                "ur_rtde ist nicht installiert. `pip install ur_rtde` oder ROBOT_MOCK=true setzen."
            ) from e

        self._ip = ip
        self._lock = threading.Lock()
        try:
            self._rtde_c = rtde_control.RTDEControlInterface(ip)
            self._rtde_r = rtde_receive.RTDEReceiveInterface(ip)
        except Exception as e:  # pragma: no cover
            raise RobotError(f"Verbindung zum UR3e {ip} fehlgeschlagen: {e}") from e
        self._gripper_closed = False

    def is_connected(self) -> bool:
        try:
            return self._rtde_r.isConnected() and self._rtde_c.isConnected()
        except Exception:
            return False

    def get_state(self) -> dict:
        with self._lock:
            r = self._rtde_r
            force = r.getActualTCPForce()
            fnorm = math.sqrt(sum(f * f for f in force[:3]))
            return {
                "connected": self.is_connected(),
                "mock": False,
                "tcp_pose": [round(v, 4) for v in r.getActualTCPPose()],
                "joints": [round(v, 4) for v in r.getActualQ()],
                "tcp_force": [round(v, 3) for v in force],
                "tcp_force_norm": round(fnorm, 3),
                "gripper_closed": self._gripper_closed,
                "moving": r.getRobotStatus() is not None and bool(r.getActualQd() and any(abs(v) > 1e-3 for v in r.getActualQd())),
                "robot_mode": int(r.getRobotMode()),
                "safety_mode": int(r.getSafetyMode()),
            }

    def move_tcp(self, pose, speed=None, accel=None):
        with self._lock:
            self._rtde_c.moveL(list(pose),
                               speed or config.ROBOT_TCP_SPEED,
                               accel or config.ROBOT_TCP_ACCEL)

    def move_joints(self, q, speed=None, accel=None):
        with self._lock:
            self._rtde_c.moveJ(list(q),
                               speed or config.ROBOT_JOINT_SPEED,
                               accel or config.ROBOT_JOINT_ACCEL)

    def jog_tcp(self, dx=0.0, dy=0.0, dz=0.0, drx=0.0, dry=0.0, drz=0.0, speed=None):
        with self._lock:
            v = [dx, dy, dz, drx, dry, drz]
            # speedL: Tool-Geschwindigkeitsvektor, Beschleunigung
            self._rtde_c.speedL(v, config.ROBOT_TCP_ACCEL)

    def stop(self):
        with self._lock:
            self._rtde_c.speedStop()
            self._rtde_c.stopL(2.0)

    def approach_until_contact(self, direction, max_distance, force_threshold=None):
        thr = force_threshold or config.ROBOT_FORCE_THRESHOLD
        n = math.sqrt(sum(d * d for d in direction)) or 1.0
        unit = [d / n for d in direction]
        start = self._rtde_r.getActualTCPPose()
        with self._lock:
            self._rtde_c.speedL([unit[0] * 0.02, unit[1] * 0.02, unit[2] * 0.02, 0, 0, 0],
                                config.ROBOT_TCP_ACCEL)
        contact = False
        traveled = 0.0
        while traveled < max_distance:
            time.sleep(0.01)
            force = self._rtde_r.getActualTCPForce()
            fnorm = math.sqrt(sum(f * f for f in force[:3]))
            cur = self._rtde_r.getActualTCPPose()
            traveled = math.sqrt(sum((cur[i] - start[i]) ** 2 for i in range(3)))
            if fnorm >= thr:
                contact = True
                break
        self.stop()
        return {"contact": contact, "distance": round(traveled, 4), "force": round(fnorm, 2)}

    def set_gripper(self, closed: bool):
        """Greifer ansteuern. Je nach config.GRIPPER_TYPE:
          none    -> nur Statusflag (kein Endeffektor angebunden)
          digital -> UR-Digital-Ausgang schalten (Pneumatik/Relais/einfacher Greifer)
          robotiq -> Robotiq-2F über das URCap-Socket (Port 63352)
        """
        closed = bool(closed)
        gtype = config.GRIPPER_TYPE
        try:
            if gtype == "digital":
                level = closed if config.GRIPPER_DO_CLOSED else (not closed)
                with self._lock:
                    self._rtde_c.setStandardDigitalOut(config.GRIPPER_DO_PIN, bool(level))
            elif gtype == "robotiq":
                self._robotiq(closed)
        except Exception as e:  # Greiferfehler darf den Ablauf nicht hart abbrechen
            print(f"[robot] Greifer-Fehler ({gtype}): {e}")
        self._gripper_closed = closed

    def _robotiq(self, closed: bool):
        """Robotiq-2F über das URCap-Socket (Port 63352). Setzt Zielposition (0=offen, 255=zu).
        Voraussetzung: Robotiq-URCap auf dem UR installiert/aktiviert."""
        import socket
        pos = 255 if closed else 0
        cmds = [b"SET ACT 1\n", b"SET GTO 1\n", b"SET SPE 255\n", b"SET FOR 150\n",
                f"SET POS {pos}\n".encode()]
        with socket.create_connection((self._ip, 63352), timeout=2.0) as s:
            for c in cmds:
                s.sendall(c)
                s.recv(64)

    def close(self):
        try:
            self._rtde_c.stopScript()
            self._rtde_c.disconnect()
            self._rtde_r.disconnect()
        except Exception:
            pass


# --------------------------------------------------------------------------- #
_singleton: Optional[RobotController] = None


_current_ip = config.ROBOT_IP


def get_controller() -> RobotController:
    """Prozessweiter Singleton-Controller gemäß Config (Mock vs. echt)."""
    global _singleton
    if _singleton is None:
        if config.ROBOT_MOCK:
            print("[robot] MockController aktiv (ROBOT_MOCK=true).")
            _singleton = MockController()
        else:
            print(f"[robot] Verbinde mit UR3e {config.ROBOT_IP} ...")
            _singleton = RTDEController(config.ROBOT_IP)
            print("[robot] Verbunden.")
    return _singleton


def set_controller(mock: bool, ip: Optional[str] = None) -> dict:
    """Controller zur Laufzeit umschalten (Mock <-> echter UR3e). Blockiert beim RTDE-Verbindungs-
    aufbau -> aus dem Executor aufrufen. Bei Fehler Fallback auf Mock, Fehlermeldung im Ergebnis."""
    global _singleton, _current_ip
    ip = (ip or _current_ip or config.ROBOT_IP).strip()
    old = _singleton
    try:
        if mock:
            new = MockController()
            print("[robot] Umgeschaltet auf MockController.")
            result = {"ok": True, "mock": True, "ip": ip, "connected": True}
        else:
            print(f"[robot] Verbinde mit echtem UR3e {ip} ...")
            new = RTDEController(ip)
            result = {"ok": True, "mock": False, "ip": ip, "connected": new.is_connected()}
            print("[robot] Verbunden.")
    except Exception as e:
        print(f"[robot] Verbindung zu {ip} fehlgeschlagen ({e}) -> Fallback Mock.")
        new = MockController()
        result = {"ok": False, "mock": True, "ip": ip, "connected": True, "error": str(e)}

    _singleton = new
    _current_ip = ip
    if old is not None and old is not new:
        try:
            old.close()
        except Exception:
            pass
    return result
