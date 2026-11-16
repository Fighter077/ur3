"""Exercise the real sequence with fake hardware and no vision dependencies."""
import ast
import asyncio
import math
import json
import os
import sys
import tempfile
from pathlib import Path
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

sys.path.insert(0, str(Path(__file__).parents[1]))
from part_types import detected_part_type


class SequenceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        source = Path(__file__).parents[1] / "robot" / "inspection.py"
        tree = ast.parse(source.read_text())
        # Load production sequence definitions without importing Torch/camera packages.
        definitions = [n for n in tree.body if isinstance(
            n, (ast.ClassDef, ast.FunctionDef, ast.Assign))]
        self.events = []
        self.config = SimpleNamespace(
            POSES_FILE="/nonexistent/inspection-test-poses.json", PART_TYPES=["HDD"],
            MOVE_POS_TOL_M=0.002, MOVE_ROT_TOL_RAD=0.02, MOVE_TIMEOUT_S=1,
            STEP_PAUSE_S=0, INSPECT_SETTLE_S=0, OCR_ENGINE="openai",
            INSPECT_FLIP_SPEED=0.15,
            INSPECT_STILL_S=0.1,
        )
        self.robot = Mock()
        self.robot.is_busy.return_value = False
        self.current = [0.0] * 6
        self.pending = None
        self.stalled = False
        self.robot.move_tcp.side_effect = self.move
        self.robot.get_state.side_effect = self.state
        self.robot.set_gripper.side_effect = lambda closed: self.events.append(("grip", closed))
        self.reader = Mock()
        self.reader.read.return_value = {"status": "identified", "raw_text": "label"}
        inv = Mock()
        self.inventory = inv
        inv.add_record.return_value = {"id": "test"}
        async def execute(_executor, fn, *args):
            return fn(*args)
        loop = SimpleNamespace(run_in_executor=execute)
        self.ns = dict(
            asyncio=SimpleNamespace(get_running_loop=lambda: loop, sleep=asyncio.sleep),
            math=math, threading=threading, time=time,
            json=json, os=os,
            Optional=object, config=self.config,
            sim=SimpleNamespace(set_state=Mock(), READY=[0, 0]),
            frames=SimpleNamespace(best_for_ocr=lambda: "frame"),
            get_controller=lambda: self.robot, get_reader=lambda: self.reader,
            get_inventory=lambda: inv,
            detected_part_type=detected_part_type,
        )
        exec(compile(ast.Module(body=definitions, type_ignores=[]), str(source), "exec"), self.ns)
        self.cell = self.ns["InspectionCell"]()
        self.cell.poses["back_clearance"] = [0.25, -0.15, 0.4, 0.0, math.pi, 0.0]
        self.cell._broadcast_inventory = AsyncMock()
        self.broadcast = AsyncMock(side_effect=self.on_broadcast)
        original = sys.modules.get("websocket_hub")
        sys.modules["websocket_hub"] = SimpleNamespace(broadcast=self.broadcast)
        def restore_hub():
            if original is None:
                sys.modules.pop("websocket_hub", None)
            else:
                sys.modules["websocket_hub"] = original
        self.addCleanup(restore_hub)

    def move(self, target, speed=None):
        self.assertFalse(self.cell.state()["capture_active"])
        self.events.append(("move", list(target), speed))
        self.pending = list(target)

    def state(self):
        moving = self.pending is not None
        result = dict(tcp_pose=list(self.current), joints=[0]*6, moving=moving,
                      mock=True, safety_mode=1)
        if moving and not self.stalled:
            self.current, self.pending = self.pending, None
        return result

    async def on_broadcast(self, message):
        if message.get("capture_active"):
            self.assertIsNone(self.pending, "Flash must wait for motion completion")
            self.events.append(("flash", list(self.current)))

    async def test_pick_capture_speed_and_clearance_order(self):
        await self.cell._run_cycle("HDD", "manual")
        p = self.cell.poses
        expected = [
            ("move", p["holder_up"], None), ("grip", False),
            ("move", p["holder"], None), ("grip", True),
            ("move", p["holder_up"], None),
            ("move", p["front"], None), ("flash", p["front"]),
            ("move", p["back"], 0.15), ("flash", p["back"]),
        ]
        expected += [("move", p["back_clearance"], None),
                     ("move", p["collection"], None),
                     ("grip", False), ("move", p["home"], None)]
        self.assertEqual(self.events, expected)
        self.assertFalse(self.cell.state()["capture_active"])

    async def test_flash_wait_resets_when_motion_resumes_at_front(self):
        front_samples = 0
        resumed_at = None
        flash_at = None

        def transient_state():
            nonlocal front_samples, resumed_at
            st = self.state()
            if st["tcp_pose"] == self.cell.poses["front"] and not st["moving"]:
                front_samples += 1
                if front_samples == 2:
                    st["moving"] = True
                    resumed_at = time.monotonic()
            return st

        async def record_flash(message):
            nonlocal flash_at
            await self.on_broadcast(message)
            if message.get("capture_active") and message.get("step") == "front":
                flash_at = time.monotonic()

        self.robot.get_state.side_effect = transient_state
        self.broadcast.side_effect = record_flash
        await self.cell._run_cycle("HDD", "manual")
        self.assertIsNotNone(resumed_at)
        self.assertIsNotNone(flash_at)
        self.assertGreaterEqual(flash_at - resumed_at, self.config.INSPECT_STILL_S)

    async def test_auto_type_uses_back_capture_when_front_has_no_type(self):
        self.reader.read.side_effect = [
            {"status": "review", "raw_text": ""},
            {"status": "identified", "raw_text": "DDR4 16GB", "type": "RAM"},
        ]
        await self.cell._run_cycle("auto", "gesture")
        record = self.inventory.add_record.call_args.args[0]
        self.assertEqual(record["type"], "RAM")
        self.assertEqual(record["status"], "identified")

    async def test_conflicting_types_are_unknown_and_need_review(self):
        self.reader.read.side_effect = [
            {"status": "identified", "raw_text": "disk", "type": "HDD"},
            {"status": "identified", "raw_text": "memory", "type": "RAM"},
        ]
        await self.cell._run_cycle("auto", "manual")
        record = self.inventory.add_record.call_args.args[0]
        self.assertEqual(record["type"], "unknown")
        self.assertEqual(record["status"], "review")

    async def test_no_type_evidence_does_not_default_to_hdd(self):
        await self.cell._run_cycle("auto", "spacebar")
        record = self.inventory.add_record.call_args.args[0]
        self.assertEqual(record["type"], "unknown")
        self.assertEqual(record["status"], "review")

    async def test_taught_clearance_is_saved_loaded_and_used_before_collection(self):
        taught = [0.25, -0.1, 0.4, 0.0, math.pi, 0.0]
        self.current = list(taught)
        with tempfile.TemporaryDirectory() as directory:
            self.config.POSES_FILE = str(Path(directory) / "poses.json")
            result = self.cell.teach_pose("back_clearance")
            self.assertTrue(result["ok"])
            self.assertEqual(self.cell._load_poses()["back_clearance"], taught)
            await self.cell._run_cycle("HDD", "manual")
        moves = [event[1] for event in self.events if event[0] == "move"]
        collection_index = moves.index(self.cell.poses["collection"])
        self.assertEqual(moves[collection_index - 1], taught)
        self.assertEqual(moves[collection_index - 2], self.cell.poses["back"])

    async def test_timeout_stops_without_gripping_or_advancing(self):
        self.stalled = True
        self.config.MOVE_TIMEOUT_S = -1
        await self.cell._run_cycle("HDD", "manual")
        self.assertEqual(len(self.events), 1)
        self.robot.stop.assert_called_once()
        self.assertFalse(self.cell.state()["capture_active"])

    async def test_stationary_tcp_does_not_finish_active_async_move(self):
        busy_reads = 0
        def busy():
            nonlocal busy_reads
            busy_reads += 1
            return busy_reads <= 3
        self.robot.is_busy.side_effect = busy
        original_gripper = self.robot.set_gripper.side_effect
        def grip(closed):
            self.assertGreater(busy_reads, 3)
            original_gripper(closed)
        self.robot.set_gripper.side_effect = grip
        await self.cell._run_cycle("HDD", "manual")
        self.robot.stop.assert_not_called()
        self.assertIn(("grip", True), self.events)

    async def test_capture_error_switches_flash_off_and_stops(self):
        self.reader.read.side_effect = RuntimeError("camera failed")
        await self.cell._run_cycle("HDD", "manual")
        self.assertFalse(self.cell.state()["capture_active"])
        self.robot.stop.assert_called_once()
        moves = [event[1] for event in self.events if event[0] == "move"]
        self.assertEqual(moves[-1], self.cell.poses["front"])


if __name__ == "__main__":
    unittest.main()
