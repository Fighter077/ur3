"""UR3e-Ansteuerung.

Abstrakte Basis `RobotController` mit zwei Implementierungen:
  - `MockController`:  simuliert den Roboter, keine Hardware nötig (Entwicklung / CI / Demo).
  - `RTDEController`:  echte Ansteuerung via ur_rtde (RTDE-Schnittstelle des UR3e).

Die Web-/WebRTC-Schicht kennt nur das abstrakte Interface -> Hardware ist austauschbar.
Alle Posen sind TCP-Posen [x, y, z, rx, ry, rz] (Meter + Rotationsvektor, UR-Konvention).
Alle Gelenkwinkel sind 6er-Listen in Radiant (base..wrist3).
"""
from __future__ import annotations

import concurrent.futures
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

    def move_tcp_j(self, pose: List[float], speed: Optional[float] = None,
                   accel: Optional[float] = None) -> None:
        """Punkt-zu-Punkt zu einer TCP-Pose, aber im GELENKRAUM (MoveJ/IK) statt geradlinig.
        Für Transitfahrten mit Gelenkinterpolation statt einer geraden TCP-Bahn.
        Die Bahn muss separat auf Kollisionsfreiheit geprüft werden.
        Default: Fallback auf move_tcp (z. B. Mock); die echte Hardware überschreibt mit moveJ_IK."""
        self.move_tcp(pose, speed, accel)

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

    def is_busy(self) -> bool:
        """True solange eine (asynchrone) Bewegung noch läuft. Mock: immer sofort fertig."""
        return False

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
        self._motion_kind = None
        # ALLE Control-Interface-Aufrufe laufen über EINEN dedizierten Thread. ur_rtde ist nicht
        # thread-sicher; selbst sequenzielle Aufrufe aus wechselnden Threadpool-Threads lösen
        # "Another thread is controlling the robot" aus. Ein einziger Thread = keine Parallelität
        # und kein Thread-Wechsel.
        self._ctrl_exec = concurrent.futures.ThreadPoolExecutor(max_workers=1,
                                                                thread_name_prefix="ur-ctrl")
        try:
            self._rtde_c = rtde_control.RTDEControlInterface(ip)
            self._rtde_r = rtde_receive.RTDEReceiveInterface(ip)
        except Exception as e:  # pragma: no cover
            raise RobotError(f"Verbindung zum UR3e {ip} fehlgeschlagen: {e}") from e
        self._gripper_closed = False

    def _ctrl(self, fn):
        """Führt eine Control-Operation auf dem dedizierten Thread aus und wartet auf das Ergebnis."""
        return self._ctrl_exec.submit(fn).result()

    def _start_move(self, kind, fn):
        def start():
            if getattr(self, "_motion_kind", None) == "speed":
                self._rtde_c.speedStop()
                self._motion_kind = None
            if not fn():
                raise RobotError(f"{kind} command was rejected by the robot")
            self._motion_kind = kind
        self._ctrl(start)

    def is_connected(self) -> bool:
        try:
            # NUR das Receive-Interface abfragen. Das Control-Interface (_rtde_c) ist nicht
            # thread-sicher -> es darf NUR aus Steuerbefehlen (unter self._lock) angefasst werden,
            # niemals aus dem Telemetrie-Thread (sonst "Another thread is controlling the robot").
            return self._rtde_r.isConnected()
        except Exception:
            return False

    def get_state(self) -> dict:
        # Reines Lesen vom Receive-Interface -> kein Lock nötig und bleibt auch während einer
        # Roboterbewegung live (das Control-Interface wird hier bewusst NICHT angefasst).
        r = self._rtde_r
        force = r.getActualTCPForce()
        fnorm = math.sqrt(sum(f * f for f in force[:3]))
        return {
            "connected": r.isConnected(),
            "mock": False,
            "tcp_pose": [round(v, 4) for v in r.getActualTCPPose()],
            "joints": [round(v, 4) for v in r.getActualQ()],
            "tcp_force": [round(v, 3) for v in force],
            "tcp_force_norm": round(fnorm, 3),
            "gripper_closed": self._gripper_closed,
            "moving": bool(r.getActualQd() and any(abs(v) > 1e-3 for v in r.getActualQd())),
            "robot_mode": int(r.getRobotMode()),
            "safety_mode": int(r.getSafetyMode()),
        }

    def move_tcp(self, pose, speed=None, accel=None):
        s, a = speed or config.ROBOT_TCP_SPEED, accel or config.ROBOT_TCP_ACCEL
        # asynchronous=True -> der Aufruf kehrt sofort zurück, der dedizierte ur-ctrl-Thread bleibt
        # FREI. Nur so kann stop() (ESC/Not-Halt) eine laufende Fahrt sofort unterbrechen. Die
        # inspection.move_to wartet auf Zielpose, Stillstand und Async-Thread-Ende.
        self._start_move("linear", lambda: self._rtde_c.moveL(list(pose), s, a, True))

    def move_joints(self, q, speed=None, accel=None):
        s, a = speed or config.ROBOT_JOINT_SPEED, accel or config.ROBOT_JOINT_ACCEL
        self._start_move("joint", lambda: self._rtde_c.moveJ(list(q), s, a, True))

    def move_tcp_j(self, pose, speed=None, accel=None):
        """TCP-Ziel per IK und asynchronem MoveJ anfahren (rad/s, rad/s²)."""
        s = config.ROBOT_JOINT_SPEED if speed is None else speed
        a = config.ROBOT_JOINT_ACCEL if accel is None else accel
        self._start_move("joint", lambda: self._rtde_c.moveJ_IK(list(pose), s, a, True))

    def jog_tcp(self, dx=0.0, dy=0.0, dz=0.0, drx=0.0, dry=0.0, drz=0.0, speed=None):
        v = [dx, dy, dz, drx, dry, drz]
        if not any(v):
            self.stop()
            return
        def jog():
            if getattr(self, "_motion_kind", None) in ("linear", "joint"):
                raise RobotError("Stop the current move before jogging")
            if not self._rtde_c.speedL(v, config.ROBOT_TCP_ACCEL):
                raise RobotError("Jog command was rejected by the robot")
            self._motion_kind = "speed"
        self._ctrl(jog)

    def stop(self):
        def _s():
            kind = getattr(self, "_motion_kind", None)
            if kind == "speed":
                self._rtde_c.speedStop()
            elif kind == "joint":
                self._rtde_c.stopJ(2.0)
            else:
                self._rtde_c.stopL(2.0)
            self._motion_kind = None
        self._ctrl(_s)

    def is_busy(self) -> bool:
        # Async-Status deckt in ur_rtde 1.6.0 auch einzelne MoveL/MoveJ ab.
        # Ausschließlich auf dem Control-Thread lesen; Fehler nicht als "fertig" behandeln.
        def busy():
            active = self._rtde_c.getAsyncOperationProgress() >= 0
            qd = self._rtde_r.getActualQd()
            moving = bool(qd) and any(abs(v) > 1e-3 for v in qd)
            if not active and not moving and getattr(self, "_motion_kind", None) in ("linear", "joint"):
                self._motion_kind = None
            return active or moving
        return self._ctrl(busy)

    def approach_until_contact(self, direction, max_distance, force_threshold=None):
        thr = force_threshold or config.ROBOT_FORCE_THRESHOLD
        n = math.sqrt(sum(d * d for d in direction)) or 1.0
        unit = [d / n for d in direction]
        start = self._rtde_r.getActualTCPPose()
        self.jog_tcp(dx=unit[0] * 0.02, dy=unit[1] * 0.02, dz=unit[2] * 0.02)
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
                self._ctrl(lambda: self._rtde_c.setStandardDigitalOut(config.GRIPPER_DO_PIN, bool(level)))
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
        # Control-Aufrufe über den dedizierten Thread, dann Receive trennen und Thread beenden.
        try:
            self._ctrl(lambda: (self._rtde_c.stopScript(), self._rtde_c.disconnect()))
        except Exception:
            pass
        try:
            self._rtde_r.disconnect()
        except Exception:
            pass
        try:
            self._ctrl_exec.shutdown(wait=False)
        except Exception:
            pass


# --------------------------------------------------------------------------- #
_singleton: Optional[RobotController] = None


_current_ip = config.ROBOT_IP


def get_controller() -> RobotController:
    """Prozessweiter Singleton-Controller gemäß Config (Mock vs. echt)."""
    if _singleton is None:
        set_controller(config.ROBOT_MOCK, config.ROBOT_IP)
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
            if not new.is_connected():
                new.close()
                raise RobotError("Keine aktive RTDE-Verbindung.")
            result = {"ok": True, "mock": False, "ip": ip, "connected": True}
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
