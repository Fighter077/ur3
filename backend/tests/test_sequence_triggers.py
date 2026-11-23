"""Trigger routing without camera/model dependencies or robot hardware."""
import ast
import asyncio
import math
from pathlib import Path
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock


BACKEND = Path(__file__).parents[1]


def definition(path, name, namespace):
    node = next(n for n in ast.parse((BACKEND / path).read_text()).body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), namespace)
    return namespace[name]


class CommandTests(unittest.TestCase):
    def test_all_start_sources_use_same_sequence_and_selected_part(self):
        cell = Mock(running=False)
        cell.start.return_value = {"ok": True}
        handle = definition("websocket_hub.py", "_handle_command", {
            "get_controller": Mock(), "get_cell": lambda: cell, "RobotError": RuntimeError})
        for source in ("manual", "spacebar", "gesture"):
            with self.subTest(source=source):
                result = handle({"action": "start_inspection", "source": source, "part_type": "RAM"})
                self.assertTrue(result["ok"])
                cell.start.assert_called_with("RAM", source=source)


class GestureTests(unittest.IsolatedAsyncioTestCase):
    async def test_held_gesture_notifies_client_once_and_never_starts_handover(self):
        config = SimpleNamespace(TRIGGER_GESTURE="open", TRIGGER_STABLE_FRAMES=3,
                                 DETECT_EVERY_N=2)
        trigger_class = definition("perception/gesture_trigger.py", "GestureTrigger", {"config": config})
        cell = Mock(running=False)
        send = AsyncMock()
        class EndStream(Exception):
            pass
        class Queue:
            count = 0
            async def get(self):
                self.count += 1
                if self.count > 20:
                    raise EndStream()
                return SimpleNamespace(to_ndarray=lambda **kw: "frame")
        async def execute(_executor, fn, *args):
            return fn(*args)
        run = definition("rtc.py", "_process_frames", {
            "CameraSource": object,
            "camera_is_selected": lambda client_id, network: not network,
            "asyncio": SimpleNamespace(get_running_loop=lambda: SimpleNamespace(run_in_executor=execute)),
            "time": time, "config": config, "GestureTrigger": trigger_class,
            "frames": SimpleNamespace(set_frame=Mock()),
            "_detect_latest": AsyncMock(side_effect=lambda source: (object(), time.monotonic(), [{"gesture": "open"}], [], None)),
            "get_cell": lambda: cell, "send_to": send, "MediaStreamError": EndStream,
            "sim": SimpleNamespace(control_mode=lambda: "gesture", gesture_enabled=lambda: True,
                                   update_idle=lambda hands: ({}, True, 1.0)),
        })
        await run("client", Queue(), object())
        triggers = [c for c in send.call_args_list if c.args[1]["type"] == "gesture_trigger"]
        self.assertEqual(len(triggers), 1)
        self.assertEqual(triggers[0].args[0], "client")
        cell.start.assert_not_called()


class HandoverTests(unittest.TestCase):
    def test_requires_three_distinct_fresh_pairs_and_clears_on_loss(self):
        clock = Mock(return_value=10.0)
        ns = {
            "math": math, "monotonic": clock, "_STABLE_SAMPLES": 3,
            "_MAX_PAIR_SKEW_S": 0.2, "_MAX_GRASP_AGE_S": 0.6,
        }
        ns["same_grasp"] = definition("perception/handover.py", "same_grasp", ns)
        tracker_class = definition("perception/handover.py", "GraspTracking", ns)
        plan = ((.4, .1, .4, 0, 0, 0), (.4, .1, .45, 0, 0, 0))
        geometry = Mock()
        geometry.estimate.return_value = plan
        tracker = tracker_class(geometry)
        for i in range(3):
            clock.return_value = stamp = 10.0 + i * .15
            tracker.update(False, "local", stamp - .03)
            tracker.update(False, "local", stamp - .02)
            self.assertIsNone(tracker.candidate())
            tracker.update(True, "remote", stamp)
        self.assertEqual(tracker.candidate(), plan)
        self.assertEqual(geometry.estimate.call_count, 3)
        clock.return_value += 1
        self.assertIsNone(tracker.candidate())
        tracker.update(False, "local", clock.return_value - .02)
        tracker.update(True, "remote", clock.return_value)
        self.assertIsNone(tracker.candidate())
        tracker.update(True, None, clock.return_value)
        self.assertIsNone(tracker.candidate())


class CameraSourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_viewers_share_one_detection_and_skip_queued_frames(self):
        now = Mock(return_value=2.0)
        perceive = Mock(return_value=([], [], None))
        async def execute(_executor, fn, *args):
            await asyncio.sleep(0)
            return fn(*args)
        detect = definition("rtc.py", "_detect_latest", {
            "CameraSource": object, "_perception_lock": asyncio.Lock(),
            "asyncio": SimpleNamespace(get_running_loop=lambda: SimpleNamespace(run_in_executor=execute)),
            "time": SimpleNamespace(monotonic=now),
            "_perceive": perceive, "_DETECTION_INTERVAL_S": 0.1,
        })
        first = object()
        source = SimpleNamespace(latest=first, updated_at=1.9, detection=None, next_detection_at=0)
        local, relayed = await asyncio.gather(detect(source), detect(source))
        self.assertIs(local, relayed)
        perceive.assert_called_once_with(first)
        source.latest = object()
        self.assertIs(await detect(source), local)
        newest = source.latest = object()
        source.updated_at = 2.2
        now.return_value = 2.3
        self.assertEqual((await detect(source))[:2], (newest, 2.2))
        self.assertEqual(perceive.call_count, 2)

    async def test_discovery_excludes_local_track_and_closed_source_releases_viewer(self):
        source_class = definition("camera_sources.py", "CameraSource", {
            "asyncio": asyncio, "uuid4": lambda: SimpleNamespace(hex="published-track"), "MediaRelay": Mock,
            "monotonic": time.monotonic,
        })
        first, second = source_class(Mock()), source_class(Mock())
        camera = definition("camera_sources.py", "remote_camera", {
            "CameraSource": source_class, "sources": {"laptop": first, "phone": second},
        })
        self.assertIs(camera("laptop"), second)
        self.assertIs(camera("phone"), first)
        frame = object()
        second.update(frame)
        self.assertIs(await second.next_frame(None), frame)
        waiting = asyncio.create_task(second.next_frame(frame))
        await asyncio.sleep(0)
        self.assertFalse(waiting.done())
        second.close()
        self.assertIsNone(await waiting)


if __name__ == "__main__":
    unittest.main()
