"""结构化差异引擎与命令行入口。

公共 API：treediff.diff(old, new) 返回差异清单，元素为
("新增" | "删除" | "修改", 路径) 或 ("移动", 旧路径, 新路径)。
路径采用 JSON Pointer（RFC 6901），根记作 "/"。
"""

from __future__ import annotations

import json
import os
import sys

ADD = "新增"
DELETE = "删除"
MODIFY = "修改"
MOVE = "移动"


class InputError(Exception):
    """输入或用法错误（退出码 2）。"""


# ---------------------------------------------------------------- 类型与相等

def _kind(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "num"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list):
        return "arr"
    if isinstance(value, dict):
        return "obj"
    raise InputError(f"不支持的值类型: {type(value).__name__}")


def equal(a, b):
    """递归相等：数字按数学值（1 == 1.0），布尔不是数字，null 只与 null 相等。"""
    ka, kb = _kind(a), _kind(b)
    if ka != kb:
        return False
    if ka == "obj":
        if len(a) != len(b):
            return False
        for key, val in a.items():
            if key not in b or not equal(val, b[key]):
                return False
        return True
    if ka == "arr":
        if len(a) != len(b):
            return False
        return all(equal(x, y) for x, y in zip(a, b))
    return a == b


def similarity(a, b):
    """README 二定义的相似度 sim(x, y) ∈ [0, 1]。"""
    ka, kb = _kind(a), _kind(b)
    if ka != kb:
        return 0.0
    if ka == "num":
        return 1.0 - abs(a - b) / max(abs(a), abs(b), 1)
    if ka in ("bool", "str", "null"):
        return 1.0 if a == b else 0.0
    if ka == "obj":
        keys = list(a)
        keys.extend(k for k in b if k not in a)
        if not keys:
            return 1.0
        total = 0.0
        for key in keys:
            if key in a and key in b:
                total += similarity(a[key], b[key])
        return total / len(keys)
    # arr：按下标对位
    m, n = len(a), len(b)
    if m == 0 and n == 0:
        return 1.0
    total = 0.0
    for i in range(min(m, n)):
        total += similarity(a[i], b[i])
    return total / max(m, n)


# ---------------------------------------------------------------- 路径

def _escape_key(key):
    return key.replace("~", "~0").replace("/", "~1")


def _join(parent, segment):
    return parent + segment if parent == "/" else parent + "/" + segment


# ---------------------------------------------------------------- 数组对齐

def _id_index(arr):
    """满足标识键条件则返回 {id键: 下标}，否则 None。"""
    seen = {}
    for i, el in enumerate(arr):
        if not isinstance(el, dict) or "id" not in el:
            return None
        val = el["id"]
        if isinstance(val, bool) or not isinstance(val, (str, int, float)):
            return None
        key = ("s", val) if isinstance(val, str) else ("n", val)
        if key in seen:
            return None
        seen[key] = i
    return seen


def _align_by_id(old, new):
    if not old or not new:
        return None
    old_ids = _id_index(old)
    new_ids = _id_index(new)
    if old_ids is None or new_ids is None:
        return None
    pairs = [(i, new_ids[key]) for key, i in old_ids.items() if key in new_ids]
    pairs.sort()
    return pairs


def _align_by_similarity(old, new):
    """旧下标升序贪心；同分取新下标最小者；sim >= 0.5 才配对。"""
    used = [False] * len(new)
    pairs = []
    for i, old_el in enumerate(old):
        best_j = -1
        best_s = 0.0
        for j, new_el in enumerate(new):
            if used[j]:
                continue
            score = similarity(old_el, new_el)
            if score >= 0.5 and (best_j < 0 or score > best_s):
                best_j, best_s = j, score
        if best_j >= 0:
            used[best_j] = True
            pairs.append((i, best_j))
    return pairs


def _align(old, new):
    pairs = _align_by_id(old, new)
    if pairs is None:
        pairs = _align_by_similarity(old, new)
    return pairs


def _moved_old_indices(pairs):
    """配对按旧下标排开的新下标序列里，满足 p_{i-1} >= p_i 的第 i 条记移动。"""
    moved = set()
    prev_new = None
    for old_i, new_j in pairs:
        if prev_new is not None and prev_new >= new_j:
            moved.add(old_i)
        prev_new = new_j
    return moved


# ---------------------------------------------------------------- 主流水线

# 内部条目：[类型, 路径1, 路径2或None, 旧子树或None, 新子树或None]
def diff(old, new):
    entries = []
    _walk(old, new, "/", "/", entries)
    entries = _relocate_subtrees(entries)
    result = []
    for entry in entries:
        kind, p1, p2 = entry[0], entry[1], entry[2]
        result.append((kind, p1, p2) if kind == MOVE else (kind, p1))
    return result


def _walk(old, new, old_path, new_path, out):
    ko, kn = _kind(old), _kind(new)
    if ko == "obj" and kn == "obj":
        for key, old_val in old.items():
            seg = _escape_key(key)
            if key in new:
                _walk(old_val, new[key], _join(old_path, seg), _join(new_path, seg), out)
            else:
                out.append([DELETE, _join(old_path, seg), None, old_val, None])
        for key, new_val in new.items():
            if key not in old:
                out.append([ADD, _join(new_path, _escape_key(key)), None, None, new_val])
        return
    if ko == "arr" and kn == "arr":
        pairs = _align(old, new)
        by_old = dict(pairs)
        moved = _moved_old_indices(pairs)
        paired_new = set(by_old.values())
        for i, old_el in enumerate(old):
            old_elem_path = _join(old_path, str(i))
            j = by_old.get(i)
            if j is None:
                out.append([DELETE, old_elem_path, None, old_el, None])
                continue
            new_elem_path = _join(new_path, str(j))
            if i in moved:
                out.append([MOVE, old_elem_path, new_elem_path, None, None])
            _walk(old_el, new[j], old_elem_path, new_elem_path, out)
        for j, new_el in enumerate(new):
            if j not in paired_new:
                out.append([ADD, _join(new_path, str(j)), None, None, new_el])
        return
    if not equal(old, new):
        out.append([MODIFY, new_path, None, None, None])


def _relocate_subtrees(entries):
    """删除按清单顺序，在新增里找内容递归相等且顺序最靠前的一条配成移动。"""
    available = [e for e in entries if e[0] == ADD]
    consumed = set()
    for entry in entries:
        if entry[0] != DELETE:
            continue
        for add_entry in available:
            if id(add_entry) in consumed:
                continue
            if equal(entry[3], add_entry[4]):
                consumed.add(id(add_entry))
                entry[0] = MOVE
                entry[2] = add_entry[1]
                entry[3] = None
                break
    if not consumed:
        return entries
    return [e for e in entries if not (e[0] == ADD and id(e) in consumed)]


# ---------------------------------------------------------------- 清单渲染

def render_listing(entries):
    lines = []
    for entry in entries:
        if entry[0] == MOVE:
            lines.append(f"{MOVE}\t{entry[1]}\t{entry[2]}")
        else:
            lines.append(f"{entry[0]}\t{entry[1]}")
    return "".join(line + "\n" for line in lines)


# ---------------------------------------------------------------- 输入

def _reject_constant(text):
    raise InputError(f"非法数字: {text}")


def load_doc(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            doc = json.load(f, parse_constant=_reject_constant)
    except InputError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InputError(f"无法读取 {path}: {exc}") from exc
    _validate(doc)
    return doc


def _validate(node):
    if isinstance(node, dict):
        for key, val in node.items():
            if any(ord(ch) < 0x20 for ch in key):
                raise InputError(f"对象键含控制字符: {key!r}")
            _validate(val)
    elif isinstance(node, list):
        for val in node:
            _validate(val)


# ---------------------------------------------------------------- check

def list_samples(root):
    pairs_dir = os.path.join(root, "pairs")
    if not os.path.isdir(pairs_dir):
        raise InputError(f"样例目录不存在: {pairs_dir}")
    names = sorted(
        name for name in os.listdir(pairs_dir)
        if os.path.isdir(os.path.join(pairs_dir, name))
    )
    if not names:
        raise InputError(f"样例目录为空: {pairs_dir}")
    return names


def check_all(root):
    """返回 [(名, old, new, 清单, 期望字节或None, 实际字节, 是否相符)]。"""
    results = []
    for name in list_samples(root):
        pair_dir = os.path.join(root, "pairs", name)
        old = load_doc(os.path.join(pair_dir, "old.json"))
        new = load_doc(os.path.join(pair_dir, "new.json"))
        entries = diff(old, new)
        got = render_listing(entries).encode("utf-8")
        expected_path = os.path.join(root, "expected", name + ".txt")
        try:
            with open(expected_path, "rb") as f:
                expected = f.read()
        except OSError:
            expected = None
        results.append((name, old, new, entries, expected, got, expected == got))
    return results


# ---------------------------------------------------------------- CLI

def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
        if len(argv) < 2:
            raise InputError("用法: treediff.py {diff <旧.json> <新.json> | check <样例根目录> | report <样例根目录> <输出.html>}")
        command = argv[1]
        if command == "diff":
            if len(argv) != 4:
                raise InputError("用法: treediff.py diff <旧.json> <新.json>")
            old = load_doc(argv[2])
            new = load_doc(argv[3])
            sys.stdout.write(render_listing(diff(old, new)))
            return 0
        if command == "check":
            if len(argv) != 3:
                raise InputError("用法: treediff.py check <样例根目录>")
            results = check_all(argv[2])
            return _report_summary(results)
        if command == "report":
            if len(argv) != 4:
                raise InputError("用法: treediff.py report <样例根目录> <输出.html>")
            from treediff_report import build_report
            results = check_all(argv[2])
            code = _report_summary(results)
            try:
                with open(argv[3], "w", encoding="utf-8") as f:
                    f.write(build_report(results))
            except OSError as exc:
                raise InputError(f"无法写入 {argv[3]}: {exc}") from exc
            return code
        raise InputError(f"未知命令: {command}")
    except InputError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2


def _report_summary(results):
    bad = [r[0] for r in results if not r[6]]
    for name in bad:
        print(f"mismatch={name}")
    if bad:
        return 1
    print(f"ok={len(results)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
