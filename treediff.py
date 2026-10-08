#!/usr/bin/env python3
"""treediff — 结构化差异引擎、命令行入口与单文件 HTML 报告。

用法:
    python3 treediff.py diff   <旧.json> <新.json>
    python3 treediff.py check  <样例根目录>
    python3 treediff.py report <样例根目录> <输出.html>

库接口:
    treediff.diff(old, new) -> [("新增"|"删除"|"修改", 路径) | ("移动", 旧路径, 新路径)]

只用 Python 标准库。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ADD = "新增"
DEL = "删除"
MOD = "修改"
MOV = "移动"
KINDS = (ADD, DEL, MOD, MOV)

_SIM_THRESHOLD = 0.5


class InputError(ValueError):
    """输入或用法错误（退出码 2）。"""


# ---------------------------------------------------------------------------
# 相等与相似度
# ---------------------------------------------------------------------------

def _is_num(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _eq(a, b):
    """文档相等：数字按数学值，布尔不是数字，null 只等于 null。"""
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if _is_num(a) and _is_num(b):
        return a == b
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        if len(a) != len(b):
            return False
        for key, value in a.items():
            if key not in b or not _eq(value, b[key]):
                return False
        return True
    if isinstance(a, list):
        return len(a) == len(b) and all(_eq(x, y) for x, y in zip(a, b))
    return a == b


def _canon(value):
    """内容的规范哈希形式，与 _eq 同口径，用于子树搬迁匹配。"""
    if isinstance(value, bool):
        return ("b", value)
    if value is None:
        return ("z",)
    if _is_num(value):
        return ("n", value)
    if isinstance(value, str):
        return ("s", value)
    if isinstance(value, dict):
        return ("o", tuple(sorted((k, _canon(v)) for k, v in value.items())))
    return ("a", tuple(_canon(v) for v in value))


def _sim(a, b):
    """相似度 sim(x, y) ∈ [0, 1]，只在无标识键的数组对齐时用。"""
    ta = _kind_of(a)
    if ta != _kind_of(b):
        return 0.0
    if ta == "num":
        return 1.0 - abs(a - b) / max(abs(a), abs(b), 1)
    if ta in ("str", "bool", "null"):
        return 1.0 if a == b else 0.0
    if ta == "obj":
        keys = set(a) | set(b)
        if not keys:
            return 1.0
        total = 0.0
        for key in keys:
            if key in a and key in b:
                total += _sim(a[key], b[key])
        return total / len(keys)
    # 数组：按下标对位
    m, n = len(a), len(b)
    if m == 0 and n == 0:
        return 1.0
    total = sum(_sim(x, y) for x, y in zip(a, b))
    return total / max(m, n)


def _kind_of(value):
    if isinstance(value, bool):
        return "bool"
    if value is None:
        return "null"
    if _is_num(value):
        return "num"
    if isinstance(value, str):
        return "str"
    if isinstance(value, dict):
        return "obj"
    return "arr"


# ---------------------------------------------------------------------------
# 路径（JSON Pointer，RFC 6901）
# ---------------------------------------------------------------------------

def _escape(segment):
    return segment.replace("~", "~0").replace("/", "~1")


def _join(base, segment):
    segment = _escape(segment)
    return "/" + segment if base == "/" else base + "/" + segment


# ---------------------------------------------------------------------------
# 数组对齐
# ---------------------------------------------------------------------------

def _id_key(element):
    """元素的标识键；不满足条件返回 None（bool 不算数字）。"""
    value = element.get("id")
    if isinstance(value, str):
        return ("s", value)
    if _is_num(value):
        return ("n", value)
    return None


def _align(old, new):
    """返回配对列表 [(旧下标, 新下标), ...]。"""
    if old and new and all(isinstance(e, dict) for e in old) \
            and all(isinstance(e, dict) for e in new):
        old_ids = [_id_key(e) for e in old]
        new_ids = [_id_key(e) for e in new]
        if None not in old_ids and None not in new_ids \
                and len(set(old_ids)) == len(old) \
                and len(set(new_ids)) == len(new):
            new_pos = {key: j for j, key in enumerate(new_ids)}
            return [(i, new_pos[key]) for i, key in enumerate(old_ids)
                    if key in new_pos]
    # 相似度贪心：旧下标升序，取 sim 最大者，同分取新下标最小者
    pairs = []
    used = [False] * len(new)
    for i, element in enumerate(old):
        best_j = -1
        best_sim = _SIM_THRESHOLD
        for j, candidate in enumerate(new):
            if used[j]:
                continue
            score = _sim(element, candidate)
            if score >= _SIM_THRESHOLD and (best_j < 0 or score > best_sim):
                best_j = j
                best_sim = score
        if best_j >= 0:
            used[best_j] = True
            pairs.append((i, best_j))
    return pairs


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def _walk(old, new, old_path, new_path, out):
    """深度优先；条目内部形式 (类型, 旧路径|None, 新路径|None, 子树|None)。"""
    if isinstance(old, dict) and isinstance(new, dict):
        for key, value in old.items():
            if key in new:
                _walk(value, new[key], _join(old_path, key),
                      _join(new_path, key), out)
            else:
                out.append((DEL, _join(old_path, key), None, value))
        for key, value in new.items():
            if key not in old:
                out.append((ADD, None, _join(new_path, key), value))
        return
    if isinstance(old, list) and isinstance(new, list):
        if _eq(old, new):
            return
        pairs = _align(old, new)
        new_of = {}
        paired_new = set()
        for i, j in pairs:
            new_of[i] = j
            paired_new.add(j)
        moved = set()
        prev = None
        for i, j in sorted(pairs):
            if prev is not None and prev >= j:
                moved.add(i)
            prev = j
        for i, element in enumerate(old):
            j = new_of.get(i)
            if j is None:
                out.append((DEL, _join(old_path, str(i)), None, element))
            else:
                if i in moved:
                    out.append((MOV, _join(old_path, str(i)),
                                _join(new_path, str(j)), None))
                _walk(element, new[j], _join(old_path, str(i)),
                      _join(new_path, str(j)), out)
        for j, element in enumerate(new):
            if j not in paired_new:
                out.append((ADD, None, _join(new_path, str(j)), element))
        return
    if not _eq(old, new):
        out.append((MOD, old_path, new_path, None))


def _relocate(out):
    """子树搬迁：删除与新增内容递归相等时配成移动，就地改写。"""
    adds_by_hash = {}
    for index, entry in enumerate(out):
        if entry[0] == ADD:
            adds_by_hash.setdefault(_canon(entry[3]), []).append(index)
    for index, entry in enumerate(out):
        if entry is None or entry[0] != DEL:
            continue
        bucket = adds_by_hash.get(_canon(entry[3]))
        if not bucket:
            continue
        add_index = min(bucket)
        adds_by_hash[_canon(entry[3])].remove(add_index)
        out[index] = (MOV, entry[1], out[add_index][2], None)
        out[add_index] = None
    out[:] = [entry for entry in out if entry is not None]


def _entries(old, new):
    """完整条目：(类型, 旧路径|None, 新路径|None)。供 diff 与报告共用。"""
    out = []
    _walk(old, new, "/", "/", out)
    _relocate(out)
    return [(kind, old_path, new_path) for kind, old_path, new_path, _ in out]


def diff(old, new):
    """比较两份文档，返回顺序固定的差异清单。

    元素为 ("新增"|"删除"|"修改", 路径) 或 ("移动", 旧路径, 新路径)。
    """
    result = []
    for kind, old_path, new_path in _entries(old, new):
        if kind == MOV:
            result.append((MOV, old_path, new_path))
        elif kind == DEL:
            result.append((DEL, old_path))
        elif kind == ADD:
            result.append((ADD, new_path))
        else:
            result.append((MOD, new_path))
    return result


def render(entries):
    """把 diff() 的清单渲染成文本（每条一行，TAB 分隔，行尾 \\n）。"""
    lines = []
    for entry in entries:
        if entry[0] == MOV:
            lines.append("\t".join(entry))
        else:
            lines.append(entry[0] + "\t" + entry[1])
    return "".join(line + "\n" for line in lines)


# ---------------------------------------------------------------------------
# 输入校验与加载
# ---------------------------------------------------------------------------

def _validate(value, path="/"):
    """键里不得有控制字符（U+0000–U+001F）。"""
    if isinstance(value, dict):
        for key, child in value.items():
            if any(ord(ch) < 0x20 for ch in key):
                raise InputError(f"键含控制字符: {path!r} 下的键 {key!r}")
            _validate(child, _join(path, key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _validate(child, _join(path, str(index)))


def _reject_constant(name):
    raise InputError(f"非法 JSON 常量: {name}")


def load_json(path):
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise InputError(f"无法读取 {path}: {exc.strerror or exc}") from exc
    try:
        value = json.loads(text, parse_constant=_reject_constant)
    except InputError:
        raise
    except ValueError as exc:
        raise InputError(f"JSON 解析失败 {path}: {exc}") from exc
    _validate(value)
    return value


# ---------------------------------------------------------------------------
# 样例核对
# ---------------------------------------------------------------------------

def run_samples(root):
    """跑遍 <root>/pairs/*/ 并与 expected/<名>.txt 逐字节比对。

    返回 [(名, old, new, 清单, 是否相符), ...]，按名排序。
    """
    root = Path(root)
    pairs_dir = root / "pairs"
    if not pairs_dir.is_dir():
        raise InputError(f"样例目录不存在: {pairs_dir}")
    results = []
    for pair_dir in sorted(pairs_dir.iterdir()):
        if not pair_dir.is_dir():
            continue
        name = pair_dir.name
        old = load_json(pair_dir / "old.json")
        new = load_json(pair_dir / "new.json")
        entries = diff(old, new)
        expected_path = root / "expected" / f"{name}.txt"
        try:
            expected = expected_path.read_bytes()
        except OSError as exc:
            raise InputError(
                f"无法读取 {expected_path}: {exc.strerror or exc}") from exc
        results.append((name, old, new, entries,
                        render(entries).encode("utf-8") == expected))
    return results


def _cmd_check(root):
    results = run_samples(root)
    bad = [name for name, _, _, _, ok in results if not ok]
    if bad:
        for name in bad:
            print(f"mismatch={name}")
        return 1
    print(f"ok={len(results)}")
    return 0


# ---------------------------------------------------------------------------
# HTML 报告
# ---------------------------------------------------------------------------

def _report_data(results):
    samples = []
    for name, old, new, entries, ok in results:
        detail = _entries(old, new)
        items = []
        for kind, old_path, new_path in detail:
            items.append({"k": kind, "o": old_path, "n": new_path})
        samples.append({
            "name": name,
            "ok": ok,
            "old": old,
            "new": new,
            "entries": items,
        })
    return {"samples": samples}


def build_report(results):
    data = json.dumps(_report_data(results), ensure_ascii=False,
                      separators=(",", ":"))
    data = data.replace("</", "<\\/")
    return _HTML_TEMPLATE.replace("__DATA__", data)


def _cmd_report(root, output):
    results = run_samples(root)
    bad = [name for name, _, _, _, ok in results if not ok]
    if bad:
        for name in bad:
            print(f"mismatch={name}")
    else:
        print(f"ok={len(results)}")
    html_text = build_report(results)
    try:
        Path(output).write_text(html_text, encoding="utf-8")
    except OSError as exc:
        raise InputError(f"无法写入 {output}: {exc.strerror or exc}") from exc
    return 1 if bad else 0


# ---------------------------------------------------------------------------
# 单文件 HTML 报告模板（样式与数据内联，无外链）
# ---------------------------------------------------------------------------

_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>treediff 结构化差异报告</title>
<style>
  * { box-sizing: border-box; }
  body { margin: 0; padding: 0 24px 48px; color: #1f2328;
         font-family: -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif;
         background: #ffffff; }
  header.top { padding: 20px 0 12px; border-bottom: 1px solid #d1d9e0;
               position: sticky; top: 0; background: #ffffff; z-index: 10; }
  h1 { font-size: 20px; margin: 0 0 8px; }
  h2 { font-size: 16px; margin: 0; }
  .counts { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
  .badge { display: inline-block; padding: 2px 10px; border-radius: 12px;
           font-size: 12px; font-weight: 600; }
  .b-total { background: #eaeef2; color: #1f2328; }
  .b-add { background: #dafbe1; color: #1a7f37; }
  .b-del { background: #ffebe9; color: #cf222e; }
  .b-mod { background: #fff8c5; color: #9a6700; }
  .b-mov { background: #fbefff; color: #8250df; }
  .b-ok  { background: #dafbe1; color: #1a7f37; }
  .b-bad { background: #ffebe9; color: #cf222e; }
  .sample { margin-top: 20px; border: 1px solid #d1d9e0; border-radius: 8px;
            overflow: hidden; }
  .sample-head { display: flex; gap: 10px; align-items: center;
                 padding: 10px 14px; background: #f6f8fa;
                 border-bottom: 1px solid #d1d9e0; flex-wrap: wrap; }
  .panes { display: flex; position: relative; align-items: stretch; }
  .pane { flex: 1 1 50%; min-width: 0; padding: 8px 12px; }
  .pane + .pane { border-left: 1px solid #d1d9e0; }
  .pane-title { font-size: 12px; font-weight: 600; color: #59636e;
                margin: 2px 0 6px; }
  svg.arrows { position: absolute; left: 0; top: 0; pointer-events: none;
               overflow: visible; z-index: 5; }
  .node { font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
          font-size: 12.5px; line-height: 1.7; }
  .children { margin-left: 14px; border-left: 1px dotted #d1d9e0;
              padding-left: 8px; }
  .line { white-space: nowrap; padding: 0 6px 0 2px; border-radius: 4px;
          border-left: 3px solid transparent; }
  .line:hover { background: #f6f8fa; }
  .toggle { display: inline-block; width: 14px; cursor: pointer;
            color: #59636e; user-select: none; }
  .key { color: #0550ae; }
  .key::after { content: ": "; color: #59636e; }
  .val.str { color: #0a3069; }
  .val.num { color: #953800; }
  .val.lit { color: #8250df; }
  .summary { color: #59636e; }
  .hl-add { background: #dafbe1; border-left-color: #1a7f37; }
  .hl-del { background: #ffebe9; border-left-color: #cf222e; }
  .hl-mod { background: #fff8c5; border-left-color: #9a6700; }
  .hl-mov { background: #fbefff; border-left-color: #8250df; }
  .legend { font-size: 12px; color: #59636e; margin-top: 6px;
            display: flex; gap: 12px; flex-wrap: wrap; }
  .legend span::before { content: ""; display: inline-block; width: 10px;
                         height: 10px; border-radius: 2px; margin-right: 4px;
                         vertical-align: -1px; }
  .legend .l-add::before { background: #1a7f37; }
  .legend .l-del::before { background: #cf222e; }
  .legend .l-mod::before { background: #9a6700; }
  .legend .l-mov::before { background: #8250df; }
</style>
</head>
<body>
<header class="top">
  <h1>treediff 结构化差异报告</h1>
  <div class="counts" id="summary"></div>
  <div class="legend">
    <span class="l-add">新增</span><span class="l-del">删除</span>
    <span class="l-mod">修改</span><span class="l-mov">移动（箭头连接）</span>
  </div>
</header>
<main id="samples"></main>
<script id="treediff-data" type="application/json">__DATA__</script>
<script>
"use strict";
var DATA = JSON.parse(document.getElementById("treediff-data").textContent);
var KIND_CLASS = {"新增": "add", "删除": "del", "修改": "mod", "移动": "mov"};
var KINDS = ["新增", "删除", "修改", "移动"];
var SVGNS = "http://www.w3.org/2000/svg";

function el(tag, cls, text) {
  var e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}
function esc(seg) { return seg.replace(/~/g, "~0").replace(/\\//g, "~1"); }
function joinPath(base, seg) {
  return base === "/" ? "/" + esc(seg) : base + "/" + esc(seg);
}
function parsePath(p) {
  if (p === "/") return [];
  return p.slice(1).split("/").map(function (s) {
    return s.replace(/~1/g, "/").replace(/~0/g, "~");
  });
}
function countKinds(entries) {
  var c = {"新增": 0, "删除": 0, "修改": 0, "移动": 0};
  entries.forEach(function (e) { c[e.k] += 1; });
  return c;
}
function badges(counts, total) {
  var box = el("span", "counts");
  box.appendChild(el("span", "badge b-total", "差异 " + total));
  KINDS.forEach(function (k) {
    box.appendChild(el("span", "badge b-" + KIND_CLASS[k],
                       k + " " + counts[k]));
  });
  return box;
}

function scalarView(v) {
  if (v === null) return ["null", "lit"];
  var t = typeof v;
  if (t === "string") return [JSON.stringify(v), "str"];
  if (t === "boolean") return [v ? "true" : "false", "lit"];
  return [String(v), "num"];
}

function makeNode(value, label, path, side, sample) {
  var node = {path: path, value: value, side: side, sample: sample,
              open: false, built: false,
              isContainer: value !== null && typeof value === "object"};
  var root = el("div", "node");
  var line = el("div", "line");
  node.line = line;
  root.appendChild(line);
  if (node.isContainer) {
    var isArr = Array.isArray(value);
    var count = isArr ? value.length : Object.keys(value).length;
    var tog = el("span", "toggle", "\\u25B8");
    node.tog = tog;
    line.appendChild(tog);
    if (label !== null) line.appendChild(el("span", "key", label));
    line.appendChild(el("span", "summary",
      (isArr ? "[ " : "{ ") + count + " 项" + (isArr ? " ]" : " }")));
    var childBox = el("div", "children");
    childBox.style.display = "none";
    root.appendChild(childBox);
    node.childBox = childBox;
    tog.addEventListener("click", function () {
      setOpen(node, !node.open);
      scheduleArrows(sample);
    });
  } else {
    line.appendChild(el("span", "toggle"));
    if (label !== null) line.appendChild(el("span", "key", label));
    var sv = scalarView(value);
    line.appendChild(el("span", "val " + sv[1], sv[0]));
  }
  node.root = root;
  sample.index[side].set(path, node);
  return node;
}

function setOpen(node, open) {
  if (!node.isContainer) return;
  if (open && !node.built) {
    node.built = true;
    var value = node.value, path = node.path, side = node.side;
    var sample = node.sample;
    if (Array.isArray(value)) {
      for (var i = 0; i < value.length; i++) {
        node.childBox.appendChild(
          makeNode(value[i], "[" + i + "]", joinPath(path, String(i)),
                   side, sample).root);
      }
    } else {
      Object.keys(value).forEach(function (k) {
        node.childBox.appendChild(
          makeNode(value[k], k, joinPath(path, k), side, sample).root);
      });
    }
  }
  node.open = open;
  node.childBox.style.display = open ? "" : "none";
  node.tog.textContent = open ? "\\u25BE" : "\\u25B8";
}

function ensureNode(sample, side, path) {
  var segs = parsePath(path);
  var cur = "/";
  var node = sample.index[side].get(cur);
  for (var i = 0; i < segs.length; i++) {
    if (!node) return null;
    setOpen(node, true);
    cur = joinPath(cur, segs[i]);
    node = sample.index[side].get(cur);
  }
  return node;
}

function mark(sample, side, path, cls) {
  var node = ensureNode(sample, side, path);
  if (node) node.line.classList.add(cls);
}

function drawArrows(sample) {
  var layer = sample.arrowLayer;
  while (layer.firstChild) layer.removeChild(layer.firstChild);
  svg.setAttribute("width", host.scrollWidth);
  svg.setAttribute("height", host.scrollHeight);
  svg.style.width = host.scrollWidth + "px";
  svg.style.height = host.scrollHeight + "px";
  var base = host.getBoundingClientRect();
  sample.moves.forEach(function (mv) {
    var a = sample.index.old.get(mv.o), b = sample.index["new"].get(mv.n);
    if (!a || !b) return;
    var r1 = a.line.getBoundingClientRect();
    var r2 = b.line.getBoundingClientRect();
    if ((r1.width === 0 && r1.height === 0) ||
        (r2.width === 0 && r2.height === 0)) return;
    var x1 = r1.right - base.left, y1 = r1.top - base.top + r1.height / 2;
    var x2 = r2.left - base.left, y2 = r2.top - base.top + r2.height / 2;
    var dx = Math.max(30, Math.abs(x2 - x1) / 3);
    var p = document.createElementNS(SVGNS, "path");
    p.setAttribute("d", "M " + x1 + " " + y1 +
                   " C " + (x1 + dx) + " " + y1 + ", " +
                   (x2 - dx) + " " + y2 + ", " + x2 + " " + y2);
    p.setAttribute("class", "arrow");
    p.setAttribute("marker-end", "url(#" + sample.arrowId + ")");
    layer.appendChild(p);
  });
}

function scheduleArrows(sample) {
  if (sample.raf) return;
  sample.raf = requestAnimationFrame(function () {
    sample.raf = null;
    drawArrows(sample);
  });
}

function renderSample(sample) {
  var section = el("section", "sample");
  var head = el("div", "sample-head");
  head.appendChild(el("h2", null, sample.name));
  head.appendChild(badges(countKinds(sample.entries), sample.entries.length));
  head.appendChild(el("span", "badge " + (sample.ok ? "b-ok" : "b-bad"),
                      sample.ok ? "核对通过" : "核对不符"));
  section.appendChild(head);

  var panes = el("div", "panes");
  sample.panes = panes;
  sample.index = {old: new Map(), "new": new Map()};
  sample.moves = [];
  sample.arrowId = "arrowhead-" + sample.name;

  var left = el("div", "pane");
  left.appendChild(el("div", "pane-title", "旧文档"));
  var right = el("div", "pane");
  right.appendChild(el("div", "pane-title", "新文档"));

  var oldRoot = makeNode(sample.old, null, "/", "old", sample);
  var newRoot = makeNode(sample["new"], null, "/", "new", sample);
  setOpen(oldRoot, true);
  setOpen(newRoot, true);
  left.appendChild(oldRoot.root);
  right.appendChild(newRoot.root);

  var svg = document.createElementNS(SVGNS, "svg");
  svg.setAttribute("class", "arrows");
  var layer = document.createElementNS(SVGNS, "g");
  sample.arrowLayer = layer;
  svg.appendChild(layer);
  var defs = document.createElementNS(SVGNS, "defs");
  var marker = document.createElementNS(SVGNS, "marker");
  marker.setAttribute("id", sample.arrowId);
  marker.setAttribute("viewBox", "0 0 10 10");
  marker.setAttribute("refX", "9");
  marker.setAttribute("refY", "5");
  marker.setAttribute("markerWidth", "7");
  marker.setAttribute("markerHeight", "7");
  marker.setAttribute("orient", "auto-start-reverse");
  var arrowHead = document.createElementNS(SVGNS, "path");
  arrowHead.setAttribute("d", "M 0 0 L 10 5 L 0 10 z");
  arrowHead.setAttribute("fill", "#8250df");
  marker.appendChild(arrowHead);
  defs.appendChild(marker);
  svg.appendChild(defs);
  sample.svg = svg;

  panes.appendChild(left);
  panes.appendChild(right);
  panes.appendChild(svg);
  section.appendChild(panes);

  sample.entries.forEach(function (e) {
    if (e.k === "删除") mark(sample, "old", e.o, "hl-del");
    else if (e.k === "新增") mark(sample, "new", e.n, "hl-add");
    else if (e.k === "修改") {
      mark(sample, "old", e.o, "hl-mod");
      mark(sample, "new", e.n, "hl-mod");
    } else if (e.k === "移动") {
      mark(sample, "old", e.o, "hl-mov");
      mark(sample, "new", e.n, "hl-mov");
      sample.moves.push(e);
    }
  });
  return section;
}


var style = document.createElement("style");
style.textContent = "svg.arrows path.arrow { stroke: #8250df; stroke-width: 1.6; " +
                    "fill: none; opacity: 0.85; }";
document.head.appendChild(style);

var allSamples = [];
var totalCounts = {"新增": 0, "删除": 0, "修改": 0, "移动": 0};
var totalAll = 0;
DATA.samples.forEach(function (sample) {
  var section = renderSample(sample);
  document.getElementById("samples").appendChild(section);
  allSamples.push(sample);
  var c = countKinds(sample.entries);
  KINDS.forEach(function (k) { totalCounts[k] += c[k]; });
  totalAll += sample.entries.length;
});
var summary = document.getElementById("summary");
summary.appendChild(badges(totalCounts, totalAll));
summary.appendChild(el("span", "badge b-total",
                       "样例 " + DATA.samples.length + " 组"));

function redrawAll() { allSamples.forEach(drawArrows); }
window.addEventListener("resize", redrawAll);
requestAnimationFrame(redrawAll);
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# 命令行
# ---------------------------------------------------------------------------

_USAGE = ("用法: python3 treediff.py diff <旧.json> <新.json> | "
          "check <样例根目录> | report <样例根目录> <输出.html>")


def main(argv=None):
    argv = sys.argv if argv is None else argv
    try:
        if len(argv) < 2:
            raise InputError(_USAGE)
        command = argv[1]
        if command == "diff":
            if len(argv) != 4:
                raise InputError(_USAGE)
            old = load_json(argv[2])
            new = load_json(argv[3])
            sys.stdout.write(render(diff(old, new)))
            return 0
        if command == "check":
            if len(argv) != 3:
                raise InputError(_USAGE)
            return _cmd_check(argv[2])
        if command == "report":
            if len(argv) != 4:
                raise InputError(_USAGE)
            return _cmd_report(argv[2], argv[3])
        raise InputError(_USAGE)
    except InputError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
