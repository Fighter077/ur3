"""Trigger routing without camera/model dependencies or robot hardware."""
import ast
import asyncio
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
            "config": SimpleNamespace(ROBOT_ENABLED=True), "get_controller": Mock(), "get_cell": lambda: cell, "RobotError": RuntimeError})
        for source in ("manual", "spacebar", "gesture"):
            with self.subTest(source=source):
                result = handle({"action": "start_inspection", "source": source, "part_type": "RAM"})
                self.assertTrue(result["ok"])
                cell.start.assert_called_with("RAM", source=source)


class GestureTests(unittest.IsolatedAsyncioTestCase):
    async def test_held_gesture_notifies_client_once_and_never_starts_handover(self):
        config = SimpleNamespace(ROBOT_ENABLED=True, TRIGGER_GESTURE="open", TRIGGER_STABLE_FRAMES=3,
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
            "asyncio": SimpleNamespace(get_running_loop=lambda: SimpleNamespace(run_in_executor=execute)),
            "time": time, "config": config, "GestureTrigger": trigger_class,
            "frames": SimpleNamespace(set_frame=Mock()),
            "_perceive": lambda img: ([{"gesture": "open"}], []),
            "get_cell": lambda: cell, "send_to": send, "MediaStreamError": EndStream,
            "sim": SimpleNamespace(gesture_enabled=lambda: True,
                                   update_idle=lambda hands: ({}, True, 1.0)),
        })
        await run("client", Queue())
        triggers = [c for c in send.call_args_list if c.args[1]["type"] == "gesture_trigger"]
        self.assertEqual(len(triggers), 1)
        self.assertEqual(triggers[0].args[0], "client")
        cell.start.assert_not_called()


if __name__ == "__main__":
    unittest.main()
