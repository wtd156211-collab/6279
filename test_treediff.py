"""treediff 的 unittest 自测。"""
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import treediff
from treediff import ADD, DEL, MOD, MOV, diff, render

SAMPLES = ROOT / "samples"


class TestEquality(unittest.TestCase):
    def test_number_math_value(self):
        self.assertEqual(diff({"a": 1}, {"a": 1.0}), [])

    def test_bool_is_not_number(self):
        self.assertEqual(diff({"a": True}, {"a": 1}), [(MOD, "/a")])
        self.assertEqual(diff({"a": 1}, {"a": True}), [(MOD, "/a")])

    def test_null_only_null(self):
        self.assertEqual(diff({"a": None}, {"a": 0}), [(MOD, "/a")])
        self.assertEqual(diff({"a": None}, {"a": None}), [])

    def test_root_scalar(self):
        self.assertEqual(diff(1, 2), [(MOD, "/")])
        self.assertEqual(diff("x", "x"), [])

    def test_type_change_whole_node(self):
        self.assertEqual(diff({"a": {"b": 1}}, {"a": [1]}), [(MOD, "/a")])


class TestPointer(unittest.TestCase):
    def test_escape(self):
        old = {"a/b": 1, "x~y": 2}
        new = {"a/b": 1, "x~y": 3}
        self.assertEqual(diff(old, new), [(MOD, "/x~0y")])
        self.assertEqual(diff(old, {}), [(DEL, "/a~1b"), (DEL, "/x~0y")])


class TestObjectOrder(unittest.TestCase):
    def test_old_order_then_new_order(self):
        old = {"b": 1, "a": 1, "gone": 5}
        new = {"b": 2, "a": 1, "born": 6}
        self.assertEqual(diff(old, new),
                         [(MOD, "/b"), (DEL, "/gone"), (ADD, "/born")])

    def test_rename_same_value_is_move(self):
        # 删除与新增内容递归相等 → 子树搬迁，报移动（README 口径）
        old = {"gone": 1}
        new = {"born": 1}
        self.assertEqual(diff(old, new), [(MOV, "/gone", "/born")])

    def test_whole_subtree_single_entry(self):
        old = {"sub": {"x": 1, "y": [1, 2, 3]}}
        self.assertEqual(diff(old, {}), [(DEL, "/sub")])
        self.assertEqual(diff({}, old), [(ADD, "/sub")])


class TestKeyedArray(unittest.TestCase):
    def test_insert_no_move(self):
        old = [{"id": i} for i in ("a", "b", "c")]
        new = [{"id": i} for i in ("a", "x", "b", "c")]
        self.assertEqual(diff(old, new), [(ADD, "/1")])

    def test_modify_uses_new_index(self):
        old = [{"id": "a", "v": 1}, {"id": "b", "v": 2}]
        new = [{"id": "x"}, {"id": "a", "v": 1}, {"id": "b", "v": 9}]
        self.assertEqual(diff(old, new),
                         [(MOD, "/2/v"), (ADD, "/0")])

    def test_numeric_and_string_ids_distinct(self):
        old = [{"id": 1}, {"id": "1"}]
        new = [{"id": "1"}, {"id": 1}]
        # id 唯一且类型各自配对：1↔1、"1"↔"1"，整体换位
        self.assertIn((MOV, "/1", "/0"), diff(old, new))

    def test_duplicate_ids_fall_back_to_similarity(self):
        old = [{"id": "a", "v": 1}, {"id": "a", "v": 2}]
        new = [{"id": "a", "v": 1}, {"id": "a", "v": 3}]
        # id 重复 → 相似度兜底，按出现顺序配对
        self.assertEqual(diff(old, new), [(MOD, "/1/v")])


class TestSimilarityArray(unittest.TestCase):
    def test_duplicates_pair_in_order(self):
        # 重复的 latency 按出现顺序一一对应：old[1] 配不到 → 删除
        old = ["latency", "latency", "x"]
        new = ["latency", "errors", "x"]
        self.assertEqual(diff(old, new), [(DEL, "/1"), (ADD, "/1")])

    def test_reorder_is_move_not_modify(self):
        old = ["latency", "latency", "errors"]
        new = ["latency", "errors", "latency"]
        self.assertEqual(diff(old, new), [(MOV, "/2", "/1")])

    def test_threshold(self):
        # sim(1, 4) = 1 - 3/4 = 0.25 < 0.5 → 不配对
        self.assertEqual(diff([1], [4]), [(DEL, "/0"), (ADD, "/0")])
        # sim(3, 4) = 0.75 ≥ 0.5 → 配对成修改
        self.assertEqual(diff([3], [4]), [(MOD, "/0")])

    def test_object_sim_formula(self):
        a = {"name": "cpu", "warn": 70, "crit": 90}
        b = {"name": "cpu", "warn": 75, "crit": 90}
        self.assertAlmostEqual(treediff._sim(a, b), 44 / 45)

    def test_empty_arrays(self):
        self.assertEqual(treediff._sim([], []), 1.0)
        self.assertEqual(diff([], []), [])


class TestMove(unittest.TestCase):
    def test_array_internal_move(self):
        old = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
        new = [{"id": "c"}, {"id": "a"}, {"id": "b"}]
        self.assertEqual(diff(old, new), [(MOV, "/2", "/0")])

    def test_move_with_inner_modify(self):
        old = [{"id": "a", "v": 1}, {"id": "b", "v": 2}]
        new = [{"id": "b", "v": 9}, {"id": "a", "v": 1}]
        self.assertEqual(diff(old, new), [(MOV, "/1", "/0"), (MOD, "/0/v")])

    def test_shift_is_not_move(self):
        # 头部插入只让下标平移，不报移动
        old = [{"id": i} for i in range(5)]
        new = [{"id": "x"}] + old
        self.assertEqual(diff(old, new), [(ADD, "/0")])

    def test_subtree_relocation(self):
        old = {"left": {"probe": {"path": "/h"}}, "right": {}}
        new = {"left": {}, "right": {"probe": {"path": "/h"}}}
        self.assertEqual(diff(old, new),
                         [(MOV, "/left/probe", "/right/probe")])

    def test_relocation_keeps_list_position(self):
        old = {"a": {"v": 1}, "b": 2}
        new = {"b": 3, "c": {"v": 1}}
        # 删除 /a 就地改写成移动，顺序不变
        self.assertEqual(diff(old, new),
                         [(MOV, "/a", "/c"), (MOD, "/b")])

    def test_relocation_picks_earliest_add(self):
        old = {"x": {"v": 1}}
        new = {"p": {"v": 1}, "q": {"v": 1}}
        self.assertEqual(diff(old, new),
                         [(MOV, "/x", "/p"), (ADD, "/q")])


class TestDeterminism(unittest.TestCase):
    def test_repeat_identical(self):
        old = json.loads((SAMPLES / "pairs/04_dups/old.json").read_text())
        new = json.loads((SAMPLES / "pairs/04_dups/new.json").read_text())
        self.assertEqual(render(diff(old, new)), render(diff(old, new)))


class TestSamples(unittest.TestCase):
    def test_all_pairs_match_expected(self):
        results = treediff.run_samples(SAMPLES)
        self.assertEqual(len(results), 7)
        bad = [name for name, *_rest, ok in results if not ok]
        self.assertEqual(bad, [])

    def test_scale_performance(self):
        old = json.loads((SAMPLES / "pairs/06_scale/old.json").read_text())
        new = json.loads((SAMPLES / "pairs/06_scale/new.json").read_text())
        start = time.perf_counter()
        entries = diff(old, new)
        elapsed = time.perf_counter() - start
        self.assertEqual(len(entries), 5)
        self.assertLess(elapsed, 3.0)


class TestCli(unittest.TestCase):
    def run_cli(self, *args, cwd=None):
        return subprocess.run(
            [sys.executable, str(ROOT / "treediff.py"), *args],
            capture_output=True, text=True, cwd=cwd or ROOT)

    def test_diff_stdout(self):
        proc = self.run_cli("diff", "samples/pairs/01_keys/old.json",
                            "samples/pairs/01_keys/new.json")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout,
                         (SAMPLES / "expected/01_keys.txt").read_text())

    def test_check_ok(self):
        proc = self.run_cli("check", "samples")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "ok=7\n")

    def test_check_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pairs/t").mkdir(parents=True)
            (root / "pairs/t/old.json").write_text('{"a": 1}')
            (root / "pairs/t/new.json").write_text('{"a": 2}')
            (root / "expected").mkdir()
            (root / "expected/t.txt").write_text("")
            proc = self.run_cli("check", str(root))
            self.assertEqual(proc.returncode, 1)
            self.assertEqual(proc.stdout, "mismatch=t\n")

    def test_usage_error(self):
        proc = self.run_cli("diff", "only-one.json")
        self.assertEqual(proc.returncode, 2)
        self.assertTrue(proc.stderr.startswith("error: "))
        self.assertEqual(proc.stderr.count("\n"), 1)

    def test_bad_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.json"
            bad.write_text("{not json")
            proc = self.run_cli("diff", str(bad), str(bad))
            self.assertEqual(proc.returncode, 2)
            self.assertTrue(proc.stderr.startswith("error: "))

    def test_control_char_key_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc = Path(tmp) / "c.json"
            doc.write_text('{"a\\u0001b": 1}')
            proc = self.run_cli("diff", str(doc), str(doc))
            self.assertEqual(proc.returncode, 2)

    def test_report_writes_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "r.html"
            proc = self.run_cli("report", "samples", str(out))
            self.assertEqual(proc.returncode, 0)
            self.assertEqual(proc.stdout, "ok=7\n")
            html = out.read_text(encoding="utf-8")
            self.assertIn("treediff-data", html)
            self.assertNotIn("http://", html.replace(
                "http://www.w3.org/2000/svg", ""))


if __name__ == "__main__":
    unittest.main()
