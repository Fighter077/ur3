"""Classification from OCR fields without downloading models or calling an API."""
import ast
from pathlib import Path
import re
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parents[1]))
from part_types import detected_part_type, infer_part_type


class PartTypeTests(unittest.TestCase):
    def test_local_label_evidence(self):
        for text, expected in [
            ("Samsung 16GB DDR4-3200", "RAM"),
            ("Micron PC4-3200AA 8GB", "RAM"),
            ("SO-DIMM DDR3L", "RAM"),
            ("Seagate 1TB 7200rpm", "HDD"),
            ("HARD DISK DRIVE 500GB", "HDD"),
            ("DVD-RW Super Multi", "optical"),
            ("BD-ROM", "optical"),
            ("Samsung 16GB", None),
            ("DDR4 HDD", None),
        ]:
            with self.subTest(text=text):
                self.assertEqual(infer_part_type({"raw_text": text}), expected)

    def test_vision_types_are_normalized_and_conflicts_rejected(self):
        self.assertEqual(infer_part_type({"type": " ram "}), "RAM")
        self.assertIsNone(infer_part_type({"type": "keyboard"}))
        self.assertEqual(detected_part_type({}, {"type": "HDD"}), "HDD")
        self.assertIsNone(detected_part_type({"type": "HDD"}, {"type": "RAM"}))

    def test_local_ocr_parser_returns_type(self):
        source = Path(__file__).parents[1] / "perception" / "ocr.py"
        tree = ast.parse(source.read_text())
        constants = [n for n in tree.body if isinstance(n, ast.Assign)
                     and any(isinstance(t, ast.Name) and t.id in
                             ("_MANUFACTURERS", "_CAP_RE", "_SPEED_RE", "_MODEL_RE") for t in n.targets)]
        reader = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "LabelReader")
        parse = next(n for n in reader.body if isinstance(n, ast.FunctionDef) and n.name == "_parse")
        parse.decorator_list = []
        namespace = {"re": re, "infer_part_type": infer_part_type}
        exec(compile(ast.Module(body=constants + [parse], type_ignores=[]), str(source), "exec"), namespace)
        self.assertEqual(namespace["_parse"]("Micron PC4-3200AA 16GB")["type"], "RAM")
        self.assertEqual(namespace["_parse"]("Seagate HDD 1TB")["type"], "HDD")


if __name__ == "__main__":
    unittest.main()
