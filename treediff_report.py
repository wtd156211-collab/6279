"""单文件 HTML 报告：样式与数据内联，高亮与箭头全部来自差异清单。"""

from __future__ import annotations

import html
import json

from treediff import ADD, DELETE, MODIFY, MOVE, _escape_key, _join, _kind, render_listing

_HL_CLASS = {ADD: "hl-add", DELETE: "hl-del", MODIFY: "hl-mod", MOVE: "hl-mov"}
_TYPE_LABEL = {ADD: "新增", DELETE: "删除", MODIFY: "修改", MOVE: "移动"}


def build_report(results):
    """results 来自 treediff.check_all；返回完整 HTML 文本。"""
    sections = []
    moves_js = {}
    totals = {ADD: 0, DELETE: 0, MODIFY: 0, MOVE: 0}
    all_ok = True
    for name, old, new, entries, _expected, _got, ok in results:
        all_ok = all_ok and ok
        counts = {ADD: 0, DELETE: 0, MODIFY: 0, MOVE: 0}
        for entry in entries:
            counts[entry[0]] += 1
            totals[entry[0]] += 1
        old_hl, new_hl, moves = {}, {}, []
        for entry in entries:
            kind = entry[0]
            if kind == DELETE:
                old_hl[entry[1]] = "del"
            elif kind == ADD:
                new_hl[entry[1]] = "add"
            elif kind == MODIFY:
                new_hl[entry[1]] = "mod"
            else:
                old_hl[entry[1]] = "mov"
                new_hl[entry[2]] = "mov"
                moves.append([entry[1], entry[2]])
        moves_js[name] = moves
        old_tree, _ = _render_node(old, "/", None, old_hl)
        new_tree, _ = _render_node(new, "/", None, new_hl)
        sections.append(_section_html(name, ok, counts, entries, old_tree, new_tree))
    badge = '<span class="badge ok">全部相符</span>' if all_ok else '<span class="badge bad">存在不符</span>'
    total_count = sum(totals.values())
    head_counts = _counts_html(totals, total_count)
    moves_json = json.dumps(moves_js, ensure_ascii=False).replace("<", "\\u003c")
    return _PAGE.format(
        badge=badge,
        total_count=total_count,
        head_counts=head_counts,
        sections="\n".join(sections),
        moves_json=moves_json,
    )


def _counts_html(counts, total):
    parts = [f'<span class="cnt">共 <b>{total}</b> 条</span>']
    for kind, cls in ((ADD, "add"), (DELETE, "del"), (MODIFY, "mod"), (MOVE, "mov")):
        parts.append(f'<span class="cnt {cls}">{_TYPE_LABEL[kind]} <b>{counts[kind]}</b></span>')
    return '<span class="counts">' + "".join(parts) + "</span>"


def _section_html(name, ok, counts, entries, old_tree, new_tree):
    esc_name = html.escape(name)
    badge = '<span class="badge ok">相符</span>' if ok else '<span class="badge bad">不符</span>'
    listing = html.escape(render_listing(entries)) or "（无差异）"
    return f"""<section data-sample="{esc_name}">
<h2>{esc_name} {badge} {_counts_html(counts, sum(counts.values()))}</h2>
<details class="listing"><summary>差异清单（{sum(counts.values())} 条）</summary><pre>{listing}</pre></details>
<div class="panes">
<div class="pane old"><h3>old.json</h3><div class="tree">{old_tree}</div></div>
<div class="pane new"><h3>new.json</h3><div class="tree">{new_tree}</div></div>
</div>
<svg class="arrows" xmlns="http://www.w3.org/2000/svg"></svg>
</section>"""


def _render_node(node, path, key_label, highlights):
    """返回 (html, 子树是否含高亮)。"""
    cls = highlights.get(path)
    has_hl = cls is not None
    cls_attr = f" hl-{cls}" if cls else ""
    path_attr = html.escape(path, quote=True)
    key_html = f'<span class="k">{html.escape(key_label)}</span>' if key_label is not None else ""
    kind = _kind(node)
    if kind in ("obj", "arr") and node:
        if kind == "obj":
            children = [(f'"{k}":', v, _join(path, _escape_key(k))) for k, v in node.items()]
            brace = "{"
        else:
            children = [(f"{i}:", v, _join(path, str(i))) for i, v in enumerate(node)]
            brace = "["
        parts = []
        for label, val, child_path in children:
            child_html, child_hl = _render_node(val, child_path, label, highlights)
            parts.append(child_html)
            has_hl = has_hl or child_hl
        open_attr = " open" if has_hl else ""
        return (
            f'<details class="node{cls_attr}" data-path="{path_attr}"{open_attr}>'
            f"<summary>{key_html}<span class=\"brace\">{brace}</span>"
            f'<span class="meta">{len(children)} 项</span></summary>'
            f'<div class="kids">{"".join(parts)}</div></details>'
        ), has_hl
    if kind in ("obj", "arr"):
        text = "{}" if kind == "obj" else "[]"
        value_html = f'<span class="v e">{text}</span>'
    elif kind == "str":
        value_html = f'<span class="v s">{html.escape(json.dumps(node, ensure_ascii=False))}</span>'
    elif kind == "num":
        value_html = f'<span class="v n">{html.escape(json.dumps(node))}</span>'
    elif kind == "bool":
        value_html = f'<span class="v b">{str(node).lower()}</span>'
    else:
        value_html = '<span class="v z">null</span>'
    return (
        f'<div class="node leaf{cls_attr}" data-path="{path_attr}">{key_html}{value_html}</div>'
    ), has_hl


_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>treediff 报告</title>
<style>
body {{ font: 14px/1.5 -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif; margin: 0; background: #f0f2f5; color: #1f2328; }}
header.top {{ position: sticky; top: 0; z-index: 20; background: #fff; border-bottom: 1px solid #d8dbe0; padding: 10px 20px; }}
header.top h1 {{ display: inline-block; font-size: 17px; margin: 0 14px 0 0; }}
section {{ position: relative; max-width: 1560px; margin: 16px auto; background: #fff; border: 1px solid #e1e4e8; border-radius: 8px; padding: 10px 16px 16px; }}
h2 {{ font-size: 15px; margin: 8px 0; }}
h3 {{ font-size: 13px; color: #57606a; margin: 4px 0 8px; }}
.badge {{ font-size: 12px; font-weight: 600; border-radius: 10px; padding: 1px 10px; vertical-align: 2px; }}
.badge.ok {{ background: #dafbe1; color: #1a7f37; }}
.badge.bad {{ background: #ffebe9; color: #cf222e; }}
.counts {{ margin-left: 10px; font-size: 12.5px; color: #57606a; }}
.counts .cnt {{ margin-right: 10px; }}
.cnt.add b {{ color: #1a7f37; }} .cnt.del b {{ color: #cf222e; }}
.cnt.mod b {{ color: #9a6700; }} .cnt.mov b {{ color: #8250df; }}
details.listing {{ margin: 4px 0 10px; font-size: 12.5px; }}
details.listing summary {{ cursor: pointer; color: #57606a; }}
details.listing pre {{ background: #f6f8fa; border: 1px solid #e1e4e8; border-radius: 6px; padding: 8px 10px; overflow-x: auto; margin: 6px 0; }}
.panes {{ display: flex; gap: 28px; }}
.pane {{ flex: 1 1 0; min-width: 0; overflow-x: auto; border: 1px solid #e1e4e8; border-radius: 6px; padding: 6px 10px; }}
.tree {{ font: 12.5px/1.7 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; white-space: nowrap; }}
.node {{ margin-left: 16px; }}
.tree > .node {{ margin-left: 0; }}
summary {{ cursor: pointer; list-style: none; }}
summary::-webkit-details-marker {{ display: none; }}
details.node > summary::before {{ content: "\\25B8"; display: inline-block; width: 13px; color: #8c959f; }}
details.node[open] > summary::before {{ content: "\\25BE"; }}
.k {{ color: #0550ae; margin-right: 6px; }}
.brace {{ color: #57606a; }}
.meta {{ color: #8c959f; font-size: 11px; margin-left: 8px; }}
.v.s {{ color: #0a3069; }} .v.n {{ color: #953800; }} .v.b {{ color: #8250df; }}
.v.z {{ color: #57606a; font-style: italic; }} .v.e {{ color: #57606a; }}
.leaf.hl-add, details.hl-add > summary {{ background: #dafbe1; box-shadow: inset 3px 0 0 #2da44e; }}
.leaf.hl-del, details.hl-del > summary {{ background: #ffebe9; box-shadow: inset 3px 0 0 #cf222e; }}
.leaf.hl-mod, details.hl-mod > summary {{ background: #fff8c5; box-shadow: inset 3px 0 0 #d4a72c; }}
.leaf.hl-mov, details.hl-mov > summary {{ background: #f0e7ff; box-shadow: inset 3px 0 0 #8250df; }}
.leaf, details.node > summary {{ border-radius: 3px; padding: 0 4px; }}
svg.arrows {{ position: absolute; inset: 0; pointer-events: none; z-index: 5; overflow: visible; }}
svg.arrows path {{ stroke: #8250df; stroke-width: 1.6; fill: none; opacity: 0.85; }}
svg.arrows circle {{ fill: #8250df; }}
</style>
</head>
<body>
<header class="top"><h1>treediff 报告</h1>{badge}{head_counts}</header>
{sections}
<svg width="0" height="0" style="position:absolute"><defs>
<marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
<path d="M 0 0 L 10 5 L 0 10 z" fill="#8250df"></path>
</marker>
</defs></svg>
<script>
const MOVES = {moves_json};
function anchorOf(el) {{
  if (el.tagName === "DETAILS") {{
    const s = el.querySelector(":scope > summary");
    if (s) return s;
  }}
  return el;
}}
function drawSection(sec) {{
  const svg = sec.querySelector("svg.arrows");
  if (!svg) return;
  const moves = MOVES[sec.dataset.sample] || [];
  const srect = sec.getBoundingClientRect();
  const oldMap = {{}}, newMap = {{}};
  sec.querySelectorAll(".pane.old [data-path]").forEach(el => {{ oldMap[el.dataset.path] = el; }});
  sec.querySelectorAll(".pane.new [data-path]").forEach(el => {{ newMap[el.dataset.path] = el; }});
  svg.setAttribute("width", sec.scrollWidth);
  svg.setAttribute("height", sec.scrollHeight);
  let out = "";
  for (const [op, np] of moves) {{
    const a = oldMap[op], b = newMap[np];
    if (!a || !b) continue;
    const ra = anchorOf(a).getBoundingClientRect();
    const rb = anchorOf(b).getBoundingClientRect();
    if ((ra.width === 0 && ra.height === 0) || (rb.width === 0 && rb.height === 0)) continue;
    const x1 = ra.right - srect.left, y1 = ra.top + ra.height / 2 - srect.top;
    const x2 = rb.left - srect.left, y2 = rb.top + rb.height / 2 - srect.top;
    const c = Math.max(40, Math.abs(x2 - x1) / 2);
    out += `<circle cx="${{x1}}" cy="${{y1}}" r="3"/>` +
      `<path d="M ${{x1}} ${{y1}} C ${{x1 + c}} ${{y1}}, ${{x2 - c}} ${{y2}}, ${{x2}} ${{y2}}" marker-end="url(#arr)"/>`;
  }}
  svg.innerHTML = out;
}}
function drawAll() {{ document.querySelectorAll("section").forEach(drawSection); }}
window.addEventListener("load", drawAll);
window.addEventListener("resize", drawAll);
document.addEventListener("toggle", ev => {{
  const sec = ev.target.closest("section");
  if (sec) drawSection(sec);
}}, true);
</script>
</body>
</html>
"""
