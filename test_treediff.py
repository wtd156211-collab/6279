import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

import treediff
from treediff import diff, render_listing

ROOT = os.path.dirname(os.path.abspath(__file__))
SAMPLES = os.path.join(ROOT, "samples")


class TestEquality(unittest.TestCase):
    def test_number_math_value(self):
        self.assertEqual(diff({"a": 1}, {"a": 1.0}), [])

    def test_bool_is_not_number(self):
        self.assertEqual(diff({"a": True}, {"a": 1}), [("修改", "/a")])

    def test_null_only_null(self):
        self.assertEqual(diff({"a": None}, {"a": 0}), [("修改", "/a")])
        self.assertEqual(diff({"a": None}, {"a": None}), [])

    def test_string_codepoints(self):
        self.assertEqual(diff({"a": "x"}, {"a": "y"}), [("修改", "/a")])

    def test_type_change(self):
        self.assertEqual(diff({"a": [1]}, {"a": {"b": 1}}), [("修改", "/a")])


class TestSimilarity(unittest.TestCase):
    def test_type_mismatch_zero(self):
        self.assertEqual(treediff.similarity(1, "1"), 0.0)

    def test_numbers(self):
        self.assertAlmostEqual(treediff.similarity(90, 95), 1 - 5 / 95)
        self.assertEqual(treediff.similarity(0, 0), 1.0)

    def test_scalars(self):
        self.assertEqual(treediff.similarity("a", "a"), 1.0)
        self.assertEqual(treediff.similarity("a", "b"), 0.0)
        self.assertEqual(treediff.similarity(None, None), 1.0)
        self.assertEqual(treediff.similarity(True, False), 0.0)

    def test_objects(self):
        self.assertAlmostEqual(
            treediff.similarity({"a": 1, "b": 2}, {"a": 1, "c": 2}), 1 / 3)
        self.assertEqual(treediff.similarity({}, {}), 1.0)

    def test_arrays(self):
        self.assertAlmostEqual(
            treediff.similarity([1, 2], [1, 2, 9]), 2 / 3)
        self.assertEqual(treediff.similarity([], []), 1.0)
        self.assertEqual(treediff.similarity([], [1]), 0.0)


class TestPaths(unittest.TestCase):
    def test_root(self):
        self.assertEqual(diff(1, 2), [("修改", "/")])

    def test_escaping(self):
        old = {"a/b": 1, "x~y": 2}
        new = {"a/b": 1}
        self.assertEqual(diff(old, new), [("删除", "/x~0y")])
        self.assertEqual(diff(new, old), [("新增", "/x~0y")])
        self.assertEqual(diff({"a/b": 1}, {"a/b": 2}), [("修改", "/a~1b")])


class TestObjects(unittest.TestCase):
    def test_order_follows_old_then_new_key_order(self):
        old = {"b": 1, "gone": 1, "a": 1}
        new = {"b": 2, "a": 1, "fresh": 2}
        self.assertEqual(diff(old, new), [
            ("修改", "/b"),
            ("删除", "/gone"),
            ("新增", "/fresh"),
        ])

    def test_delete_and_add_are_whole_subtree(self):
        old = {"x": {"deep": {"deeper": [1, 2, 3]}}}
        new = {}
        self.assertEqual(diff(old, new), [("删除", "/x")])


class TestArraysById(unittest.TestCase):
    def test_insert_does_not_shift(self):
        old = {"items": [{"id": "a", "v": 1}, {"id": "b", "v": 2}]}
        new = {"items": [{"id": "z", "v": 9}, {"id": "a", "v": 1}, {"id": "b", "v": 3}]}
        self.assertEqual(diff(old, new), [
            ("修改", "/items/2/v"),
            ("新增", "/items/0"),
        ])

    def test_modify_uses_new_path(self):
        old = [{"id": "a", "v": 1}, {"id": "b", "v": 2}]
        new = [{"id": "x"}, {"id": "a", "v": 1}, {"id": "b", "v": 3}]
        self.assertEqual(diff(old, new), [
            ("修改", "/2/v"),
            ("新增", "/0"),
        ])

    def test_numeric_and_string_ids_distinct(self):
        old = [{"id": 1}, {"id": "1"}]
        new = [{"id": "1"}, {"id": 1}]
        self.assertEqual(diff(old, new), [
            ("移动", "/1", "/0"),
        ])

    def test_duplicate_ids_fall_back_to_similarity(self):
        old = [{"id": "a", "v": 1}, {"id": "a", "v": 2}]
        new = [{"id": "a", "v": 1}, {"id": "a", "v": 3}]
        self.assertEqual(diff(old, new), [("修改", "/1/v")])


class TestArraysBySimilarity(unittest.TestCase):
    def test_duplicate_scalars_pair_in_order(self):
        old = ["x", "x", "x"]
        new = ["x", "x"]
        self.assertEqual(diff(old, new), [("删除", "/2")])

    def test_threshold_half(self):
        # sim({"a":1,"b":1},{"a":1,"b":2}) = (1 + 1/2) / 2 = 0.75 >= 0.5 → 配对
        old = [{"a": 1, "b": 1}]
        new = [{"a": 1, "b": 2}]
        self.assertEqual(diff(old, new), [("修改", "/0/b")])
        # sim = 1/3 < 0.5 → 删除 + 新增
        old = [{"a": 1, "b": 1, "c": 1}]
        new = [{"a": 1, "b": 9, "c": 9}]
        self.assertEqual(diff(old, new), [("删除", "/0"), ("新增", "/0")])

    def test_tie_prefers_smallest_new_index(self):
        old = ["a"]
        new = ["a", "a"]
        self.assertEqual(diff(old, new), [("新增", "/1")])


class TestMoves(unittest.TestCase):
    def test_in_array_move(self):
        old = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
        new = [{"id": "c"}, {"id": "a"}, {"id": "b"}]
        self.assertEqual(diff(old, new), [("移动", "/2", "/0")])

    def test_pure_shift_is_not_move(self):
        old = [{"id": "a"}, {"id": "b"}]
        new = [{"id": "z"}, {"id": "a"}, {"id": "b"}]
        self.assertEqual(diff(old, new), [("新增", "/0")])

    def test_move_still_recurses(self):
        old = [{"id": "a", "v": 1}, {"id": "b", "v": 2}]
        new = [{"id": "b", "v": 3}, {"id": "a", "v": 1}]
        self.assertEqual(diff(old, new), [
            ("移动", "/1", "/0"),
            ("修改", "/0/v"),
        ])

    def test_subtree_relocation(self):
        old = {"left": {"probe": {"path": "/h"}}, "right": {}}
        new = {"left": {}, "right": {"probe": {"path": "/h"}}}
        self.assertEqual(diff(old, new), [("移动", "/left/probe", "/right/probe")])

    def test_relocation_needs_exact_content(self):
        old = {"left": {"probe": {"path": "/h", "t": 5}}, "right": {}}
        new = {"left": {}, "right": {"probe": {"path": "/h", "t": 6}}}
        self.assertEqual(diff(old, new), [
            ("删除", "/left/probe"),
            ("新增", "/right/probe"),
        ])

    def test_relocation_picks_earliest_add(self):
        old = {"a": {"v": 1}}
        new = {"b": {"v": 1}, "c": {"v": 1}}
        self.assertEqual(diff(old, new), [("移动", "/a", "/b"), ("新增", "/c")])


class TestListing(unittest.TestCase):
    def test_render(self):
        entries = [("修改", "/a"), ("移动", "/b", "/c")]
        self.assertEqual(render_listing(entries), "修改\t/a\n移动\t/b\t/c\n")

    def test_empty(self):
        self.assertEqual(render_listing([]), "")


class TestDeterminism(unittest.TestCase):
    def test_same_input_same_output(self):
        old = {"arr": [{"k": i, "v": [i, "s"]} for i in range(50)]}
        new = {"arr": [{"k": i, "v": [i, "s"]} for i in (25, 0, 1)] + [{"k": 99}]}
        self.assertEqual(render_listing(diff(old, new)), render_listing(diff(old, new)))


class TestSamples(unittest.TestCase):
    def test_check_all_ok(self):
        results = treediff.check_all(SAMPLES)
        bad = [r[0] for r in results if not r[6]]
        self.assertEqual(bad, [])
        self.assertEqual(len(results), 7)

    def test_scale_performance(self):
        old = treediff.load_doc(os.path.join(SAMPLES, "pairs/06_scale/old.json"))
        new = treediff.load_doc(os.path.join(SAMPLES, "pairs/06_scale/new.json"))
        start = time.perf_counter()
        entries = diff(old, new)
        elapsed = time.perf_counter() - start
        self.assertEqual(len(entries), 5)
        self.assertLess(elapsed, 3.0)


class TestCli(unittest.TestCase):
    def run_cli(self, *args, cwd=ROOT):
        return subprocess.run(
            [sys.executable, "treediff.py", *args],
            cwd=cwd, capture_output=True, text=True, encoding="utf-8")

    def test_diff_command(self):
        proc = self.run_cli(
            "diff",
            os.path.join(SAMPLES, "pairs/02_insert/old.json"),
            os.path.join(SAMPLES, "pairs/02_insert/new.json"))
        self.assertEqual(proc.returncode, 0)
        with open(os.path.join(SAMPLES, "expected/02_insert.txt"), encoding="utf-8") as f:
            self.assertEqual(proc.stdout, f.read())

    def test_check_command(self):
        proc = self.run_cli("check", SAMPLES)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "ok=7\n")

    def test_usage_error(self):
        proc = self.run_cli()
        self.assertEqual(proc.returncode, 2)
        self.assertTrue(proc.stderr.startswith("error: "))

    def test_missing_file(self):
        proc = self.run_cli("diff", "nope.json", "nope2.json")
        self.assertEqual(proc.returncode, 2)
        self.assertTrue(proc.stderr.startswith("error: "))

    def test_control_char_in_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = os.path.join(tmp, "bad.json")
            with open(bad, "w", encoding="utf-8") as f:
                f.write('{"a\\u0001b": 1}')
            proc = self.run_cli("diff", bad, bad)
            self.assertEqual(proc.returncode, 2)
            self.assertTrue(proc.stderr.startswith("error: "))

    def test_report_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "r.html")
            proc = self.run_cli("report", SAMPLES, out)
            self.assertEqual(proc.returncode, 0)
            with open(out, encoding="utf-8") as f:
                page = f.read()
            self.assertIn("ok=7", proc.stdout)
            self.assertIn("全部相符", page)
            self.assertNotIn("http://", page.replace("http://www.w3.org/2000/svg", ""))
            self.assertNotIn("https://", page)


if __name__ == "__main__":
    unittest.main()
