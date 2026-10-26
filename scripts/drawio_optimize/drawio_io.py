"""Read and write real, editable .drawio (mxGraph) files.

Supported input forms
---------------------
* plain ``<mxfile><diagram><mxGraphModel>...``
* draw.io compressed diagrams (base64 + raw deflate + URL encoding)
* a bare ``<mxGraphModel>`` document
* multiple pages (only the selected page is edited; the others are preserved
  byte-for-byte)

Invariants
----------
* Object ids, existing styles and unknown cells are preserved.
* Vertices keep their absolute geometry; children of a container are written
  relative to that container (Draw.io semantics).
* Edges are written with fixed ``exitX/exitY`` and ``entryX/entryY`` anchors and
  explicit interior waypoints, so the rendered path is the routed path.
"""
from __future__ import annotations

import base64
import html
import re
import urllib.parse
import zlib
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from .geometry import Rect, merge_rects
from .model import Edge, EdgeLabel, Graph, Node, Port, PreservedCell
from .util import (
    IdAllocator,
    NODE_STYLES,
    SIGNAL_STYLES,
    merge_style,
    parse_style,
)

DEFAULT_PAGE = {
    "dx": "1400",
    "dy": "900",
    "grid": "1",
    "gridSize": "10",
    "guides": "1",
    "tooltips": "1",
    "connect": "1",
    "arrows": "1",
    "fold": "1",
    "page": "1",
    "pageScale": "1",
    "pageWidth": "1600",
    "pageHeight": "1200",
    "math": "0",
    "shadow": "0",
}


# ---------------------------------------------------------------------------
# decoding
# ---------------------------------------------------------------------------

def _decode_diagram_text(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return "<mxGraphModel><root><mxCell id=\"0\"/><mxCell id=\"1\" parent=\"0\"/></root></mxGraphModel>"
    if text.startswith("<"):
        return text
    padded = text + "=" * (-len(text) % 4)
    raw = base64.b64decode(padded)
    try:
        inflated = zlib.decompress(raw, -15)
    except zlib.error:
        inflated = zlib.decompress(raw)
    return urllib.parse.unquote(inflated.decode("utf-8"))


def _looks_compressed(text: str) -> bool:
    t = (text or "").strip()
    return bool(t) and not t.startswith("<")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------

def load(path: str | Path, page: str | int | None = None) -> Graph:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    root = ET.fromstring(text)
    graph = Graph(source_path=str(p))
    tag = _local(root.tag)

    if tag == "mxfile":
        diagrams = [c for c in root if _local(c.tag) == "diagram"]
        if not diagrams:
            raise ValueError(f"{p}: no <diagram> element")
        idx = 0
        if page is not None:
            for i, d in enumerate(diagrams):
                if str(page) in (d.get("id"), d.get("name"), str(i)):
                    idx = i
                    break
        selected = diagrams[idx]
        file_attrs = dict(root.attrib)
        diagram_attrs = {
            k: v for k, v in selected.attrib.items() if k not in ("id", "name")
        }
        graph.page = {k: v for k, v in file_attrs.items() if k != "modified"}
        graph.page.update({k: v for k, v in selected.attrib.items()})
        graph.page.update(
            {
                "_page_index": str(idx),
                "_page_count": str(len(diagrams)),
                "_compressed": "1" if _looks_compressed(selected.text or "") else "0",
                "_file_attrs": file_attrs,
                "_diagram_attrs": diagram_attrs,
            }
        )
        graph.other_pages = [
            {
                "index": i,
                "xml": ET.tostring(d, encoding="unicode"),
                "id": d.get("id"),
                "name": d.get("name"),
            }
            for i, d in enumerate(diagrams)
            if i != idx
        ]
        model_el = None
        for c in selected:
            if _local(c.tag) == "mxGraphModel":
                model_el = c
                break
        if model_el is None:
            model_text = _decode_diagram_text(selected.text or "")
            model_el = ET.fromstring(model_text)
        graph.page["_model_attrs"] = dict(model_el.attrib)
    elif tag == "mxGraphModel":
        model_el = root
        graph.page = dict(DEFAULT_PAGE)
        graph.page.update(
            {
                "_file_attrs": {},
                "_diagram_attrs": {},
                "_model_attrs": dict(root.attrib),
            }
        )
    else:
        raise ValueError(f"{p}: not a Draw.io file (root element <{tag}>)")

    graph.page.update({k: v for k, v in model_el.attrib.items()})
    graph.page.setdefault("autofit", "0")
    _parse_cells(graph, model_el)
    _finalize_geometry(graph)
    return graph


def _cell_records(root_el: ET.Element) -> list[tuple[str | None, ET.Element, ET.Element | None]]:
    """Return ``(id, cell_element, wrapper_element)`` for every draw.io cell.

    Draw.io stores cells with metadata (tooltip, link, ``data-source`` ...) as
    ``<UserObject id="..." label="..."><mxCell .../></UserObject>``.  The id and
    label live on the wrapper, while style/geometry/parent live on the inner
    ``<mxCell>``.  Treating only bare ``<mxCell>`` elements as cells silently
    drops all wrapped vertices and disconnects every edge that references them.
    """
    out: list[tuple[str | None, ET.Element, ET.Element | None]] = []
    for child in root_el:
        tag = _local(child.tag)
        if tag == "mxCell":
            out.append((child.get("id"), child, None))
            continue
        if tag in ("UserObject", "object"):
            inner = None
            for sub in child:
                if _local(sub.tag) == "mxCell":
                    inner = sub
                    break
            if inner is None:
                # A wrapper without an mxCell cannot be parsed as a vertex or
                # edge; emit it verbatim from the main parser.
                out.append((child.get("id"), child, None))
            else:
                out.append((child.get("id") or inner.get("id"), inner, child))
            continue
        # Unknown root-level elements are preserved by the caller.
        out.append((child.get("id"), child, None))
    return out


def _cell_value(el: ET.Element, wrapper: ET.Element | None) -> str:
    if wrapper is not None:
        for key in ("label", "value"):
            if wrapper.get(key) is not None:
                return wrapper.get(key) or ""
    return el.get("value") or ""


def _wrapper_meta(wrapper: ET.Element, inner: ET.Element | None) -> dict[str, Any]:
    """Metadata needed to re-emit a UserObject/object wrapper losslessly."""
    attrs = {
        k: v
        for k, v in wrapper.attrib.items()
        if k not in ("id", "label", "value")
    }
    if "label" in wrapper.attrib:
        label_key = "label"
    elif "value" in wrapper.attrib:
        label_key = "value"
    else:
        label_key = "label" if _local(wrapper.tag) in ("UserObject", "object") else "value"
    extra = [
        ET.tostring(ch, encoding="unicode")
        for ch in wrapper
        if inner is None or ch is not inner
    ]
    return {
        "tag": _local(wrapper.tag),
        "attrs": attrs,
        "label_key": label_key,
        "extra": extra,
    }


def _parse_cells(graph: Graph, model_el: ET.Element) -> None:
    root_el = model_el.find("root")
    if root_el is None:
        raise ValueError("mxGraphModel has no <root>")
    records = _cell_records(root_el)
    ids = {cid for cid, _el, _w in records if cid}
    alloc = IdAllocator(ids)

    raw_by_id: dict[str, ET.Element] = {}
    wrapper_by_id: dict[str, ET.Element] = {}
    for cid, el, wrapper in records:
        if cid in (None, "0", "1"):
            continue
        raw_by_id[cid] = el
        if wrapper is not None:
            wrapper_by_id[cid] = wrapper
    edge_ids = {cid for cid, el in raw_by_id.items() if el.get("edge") == "1"}
    label_ids = {
        cid
        for cid, el in raw_by_id.items()
        if el.get("parent") in edge_ids
        and el.get("vertex") == "1"
        and "edgeLabel" in (el.get("style") or "")
    }

    # parent cell ids for preserved cells
    def parent_ok(pid: str | None) -> str | None:
        if pid in (None, "0", "1"):
            return None
        return pid if pid in raw_by_id else None

    for cid, el in raw_by_id.items():
        if cid in label_ids:
            continue
        wrapper = wrapper_by_id.get(cid)
        if _local(el.tag) in ("UserObject", "object"):
            # Wrapper with no inner mxCell: keep it exactly as authored.
            _preserve(graph, el, cid, "wrapper-without-mxCell")
            continue
        style_raw = el.get("style") or ""
        style = parse_style(style_raw)
        value = _cell_value(el, wrapper)
        wmeta = _wrapper_meta(wrapper, el) if wrapper is not None else None
        is_edge = el.get("edge") == "1"
        is_vertex = el.get("vertex") == "1"
        geo = el.find("mxGeometry")
        if is_edge:
            labels = [
                (x, wrapper_by_id.get(x.get("id")))
                for x in raw_by_id.values()
                if x.get("parent") == cid and x.get("vertex") == "1"
            ]
            _parse_edge(
                graph, el, cid, style, style_raw, value, labels, wrapper_meta=wmeta
            )
        elif is_vertex:
            if geo is None:
                # group cell without geometry: children keep absolute coords
                meta: dict[str, Any] = {
                    "value_html": value,
                    "group_no_geometry": True,
                }
                if wmeta is not None:
                    meta["wrapper"] = wmeta
                n = Node(
                    id=cid,
                    label=_plain(value),
                    parent=parent_ok(el.get("parent")),
                    container=True,
                    container_style="group",
                    style=style_raw,
                    meta=meta,
                )
                graph.add_node(n)
                continue
            if "text" in style and style.get("text") == "1" and "fillColor" not in style:
                _preserve(graph, el, cid, "text-cell", wrapper=wrapper)
                continue
            if "image" in style or style.get("shape") == "image":
                _preserve(graph, el, cid, "image-cell", wrapper=wrapper)
                continue
            x = float(geo.get("x", 0) or 0)
            y = float(geo.get("y", 0) or 0)
            w = float(geo.get("width", 0) or 0)
            h = float(geo.get("height", 0) or 0)
            if w <= 0 or h <= 0:
                _preserve(graph, el, cid, "zero-size-vertex", wrapper=wrapper)
                continue
            meta = {
                "value_html": value,
                "style_map": style,
                "font_size": float(style.get("fontSize", 12) or 12),
                "raw": el,
            }
            if wmeta is not None:
                meta["wrapper"] = wmeta
            n = Node(
                id=cid,
                label=_plain(value),
                x=x,
                y=y,
                w=w,
                h=h,
                parent=parent_ok(el.get("parent")),
                style=style_raw,
                meta=meta,
            )
            kind = style.get("pnrKind") or infer_kind(value, style)
            n.kind = kind
            n.container = (
                "swimlane" in style
                or style.get("container") == "1"
                or "group" in style
                or _has_vertex_children(raw_by_id, cid)
            )
            if n.container:
                n.container_style = "swimlane" if "swimlane" in style else "group"
            graph.add_node(n)
        else:
            _preserve(graph, el, cid, "not-vertex-or-edge", wrapper=wrapper)


def _has_vertex_children(raw: dict[str, ET.Element], cid: str) -> bool:
    return any(
        c.get("parent") == cid and c.get("vertex") == "1" for c in raw.values()
    )


def _plain(value: str) -> str:
    """Strip simple HTML from a label for measurement/DRC purposes."""
    if not value:
        return ""
    v = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    v = re.sub(r"</(div|p|li)>", "\n", v, flags=re.I)
    v = re.sub(r"<[^>]+>", "", v)
    v = html.unescape(v)
    return v.strip()


def _parse_edge(
    graph: Graph,
    el: ET.Element,
    cid: str,
    style: dict[str, str],
    style_raw: str,
    value: str,
    label_cells: list[tuple[ET.Element, ET.Element | None]] | None = None,
    wrapper_meta: dict[str, Any] | None = None,
) -> None:
    src = el.get("source")
    tgt = el.get("target")
    geo = el.find("mxGeometry")
    waypoints: list[tuple[float, float]] = []
    if geo is not None:
        arr = geo.find("Array")
        if arr is not None and arr.get("as") == "points":
            for pt in arr:
                if _local(pt.tag) == "mxPoint":
                    waypoints.append(
                        (float(pt.get("x", 0) or 0), float(pt.get("y", 0) or 0))
                    )
    sp = _port_from_style(style, "exit")
    tp = _port_from_style(style, "entry")
    signal = style.get("pnrSignal") or infer_signal(value, style)
    meta: dict[str, Any] = {
        "value_html": value,
        "style_map": style,
        "floating": src is None or tgt is None,
        "label_font_size": float(style.get("fontSize", 10) or 10),
        "raw": el,
    }
    if wrapper_meta is not None:
        meta["wrapper"] = wrapper_meta
    edge = Edge(
        id=cid,
        src=src,
        tgt=tgt,
        label=_plain(value),
        signal=signal,
        src_port=sp,
        tgt_port=tp,
        waypoints=waypoints,
        style=style_raw,
        parent=el.get("parent") if el.get("parent") not in ("0", "1") else None,
        meta=meta,
    )
    # label cells are siblings with parent=<edge id> in Draw.io XML
    for child, label_wrapper in (label_cells or []):
        if child.get("vertex") != "1":
            continue
        cgeo = child.find("mxGeometry")
        rel_x = float(cgeo.get("x", 0) or 0) if cgeo is not None else 0.0
        rel_y = float(cgeo.get("y", 0) or 0) if cgeo is not None else 0.0
        text = _plain(_cell_value(child, label_wrapper))
        lid = (
            label_wrapper.get("id")
            if label_wrapper is not None
            else child.get("id")
        )
        if text and not edge.label_cell_id:
            edge.label_cell_id = lid
            edge.label = text
            edge.label_state = EdgeLabel(
                text=text, rel_x=rel_x, rel_y=rel_y, attached=True
            )
            edge.meta["label_style"] = child.get("style") or ""
            if label_wrapper is not None:
                edge.meta["label_wrapper"] = _wrapper_meta(label_wrapper, child)
        elif text:
            extra = {
                "text": text,
                "rel_x": rel_x,
                "rel_y": rel_y,
                "style": child.get("style") or "",
                "id": lid,
            }
            if label_wrapper is not None:
                extra["wrapper"] = _wrapper_meta(label_wrapper, child)
            edge.extra_labels.append(extra)
    # A label moved from the edge value to a child cell must not lose its
    # inferred signal (e.g. valid/ready -> control).
    if edge.label and not style.get("pnrSignal"):
        edge.signal = infer_signal(edge.label, style)
    graph.edges.append(edge)


def _port_from_style(style: dict[str, str], which: str) -> Port | None:
    sx = style.get(f"{which}X")
    sy = style.get(f"{which}Y")
    if sx is None and sy is None:
        return None
    try:
        x = float(sx) if sx is not None else None
        y = float(sy) if sy is not None else None
    except ValueError:
        return None
    # draw.io anchors are on the boundary: x in {0,1} -> left/right,
    # y in {0,1} -> top/bottom.  Prefer the axis that is actually at 0/1.
    if y is not None and abs(y) <= 1e-6 and x is not None:
        return Port(side="T", frac=x, role="out" if which == "exit" else "in")
    if y is not None and abs(y - 1) <= 1e-6 and x is not None:
        return Port(side="B", frac=x, role="out" if which == "exit" else "in")
    if x is not None and abs(x) <= 1e-6 and y is not None:
        return Port(side="L", frac=y, role="out" if which == "exit" else "in")
    if x is not None and abs(x - 1) <= 1e-6 and y is not None:
        return Port(side="R", frac=y, role="out" if which == "exit" else "in")
    # floating anchor (e.g. 0.5/0.5): let the router decide
    return None


def _finalize_geometry(graph: Graph) -> None:
    # absolute origin of a cell = parent origin + own geometry offset.
    # A geometry-less group contributes no offset: its children keep the
    # coordinates they had relative to the group's parent (Draw.io behaviour).
    cache: dict[str, tuple[float, float]] = {}

    def abs_pos(nid: str) -> tuple[float, float]:
        if nid in cache:
            return cache[nid]
        n = graph.nodes[nid]
        if n.parent and n.parent in graph.nodes:
            px, py = abs_pos(n.parent)
            if n.meta.get("group_no_geometry"):
                pos = (px, py)
            else:
                pos = (px + n.x, py + n.y)
        else:
            pos = (n.x, n.y)
        cache[nid] = pos
        return pos

    raw = {nid: (n.x, n.y) for nid, n in graph.nodes.items()}
    for n in graph.nodes.values():
        ax, ay = abs_pos(n.id)
        # keep the stored relative values for groups, recompute below
        if not n.meta.get("group_no_geometry"):
            n.x, n.y = ax, ay
        n.original = (n.x, n.y, n.w, n.h)

    # geometry-less groups derive their rect from children
    for n in sorted(
        (x for x in graph.nodes.values() if x.meta.get("group_no_geometry")),
        key=lambda x: len(graph.ancestors(x.id)),
        reverse=True,
    ):
        kids = [graph.nodes[c] for c in graph.descendants(n.id) if not graph.nodes[c].container]
        if not kids:
            kids = [graph.nodes[c] for c in graph.descendants(n.id)]
        if kids:
            r = merge_rects([k.rect for k in kids])
            pad = 24.0
            n.x, n.y = r.x - pad, r.y - pad - 24
            n.w, n.h = r.w + 2 * pad, r.h + 2 * pad + 24
        else:
            n.w, n.h = 200, 120

    # edge endpoints that point at containers are kept; unknown endpoints are
    # reported later by DRC (dangling), never silently re-attached.
    for e in graph.edges:
        if e.src and e.src not in graph.nodes:
            graph.diagnostics.append(f"edge {e.id}: source {e.src!r} not found")
        if e.tgt and e.tgt not in graph.nodes:
            graph.diagnostics.append(f"edge {e.id}: target {e.tgt!r} not found")

    # preserved shapes become routing obstacles only if they look like shapes
    for pc in graph.preserved:
        r = pc.__dict__.get("rect")
        if r:
            graph.extra_obstacles.append(r)


def _preserve(
    graph: Graph,
    el: ET.Element,
    cid: str,
    reason: str,
    wrapper: ET.Element | None = None,
) -> None:
    geo = el.find("mxGeometry")
    rect = None
    if geo is not None and el.get("vertex") == "1":
        try:
            rect = Rect(
                float(geo.get("x", 0) or 0),
                float(geo.get("y", 0) or 0),
                float(geo.get("width", 0) or 0),
                float(geo.get("height", 0) or 0),
            )
        except ValueError:
            rect = None
    source = wrapper if wrapper is not None else el
    pc = PreservedCell(
        xml=ET.tostring(source, encoding="unicode"),
        parent=el.get("parent"),
        cell_id=cid,
        reason=reason,
    )
    if rect and reason != "text-cell":
        pc.__dict__["rect"] = rect
    graph.preserved.append(pc)


# ---------------------------------------------------------------------------
# inference helpers (only for files that were not produced by this skill)
# ---------------------------------------------------------------------------

KIND_KEYWORDS = [
    ("jtag", "interface"),
    ("tap", "controller"),
    ("scan", "controller"),
    ("mbist", "controller"),
    ("bist", "controller"),
    ("controller", "controller"),
    ("cpu", "datapath"),
    ("gpu", "datapath"),
    ("npu", "datapath"),
    ("alu", "datapath"),
    ("register", "datapath"),
    ("cache", "memory"),
    ("sram", "memory"),
    ("dram", "memory"),
    ("memory", "memory"),
    ("fifo", "memory"),
    ("ram", "memory"),
    ("pll", "clock"),
    ("clock", "clock"),
    ("reset", "clock"),
    ("pad", "external"),
    ("io", "external"),
    ("interface", "interface"),
    ("uart", "interface"),
    ("spi", "interface"),
    ("i2c", "interface"),
    ("axi", "interface"),
]


def infer_kind(label: str, style: dict[str, str] | None = None) -> str:
    low = (label or "").lower()
    for key, kind in KIND_KEYWORDS:
        if key in low:
            return kind
    return "default"


def infer_signal(label: str, style: dict[str, str] | None = None) -> str:
    if style and style.get("dashed") == "1":
        return "control"
    low = (label or "").lower()
    if any(k in low for k in ("clk", "clock", "rst", "reset")):
        return "clock"
    if any(k in low for k in ("en", "sel", "ctrl", "control", "mode", "irq", "ready", "valid")):
        return "control"
    if any(k in low for k in ("bus", "axi", "ahb", "data[")):
        return "bus"
    return "data"


# ---------------------------------------------------------------------------
# writing
# ---------------------------------------------------------------------------

def write(graph: Graph, path: str | Path, autofit: bool = True) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    file_attrs = dict(graph.page.get("_file_attrs") or {})
    file_attrs.setdefault("host", "drawio-optimize")
    file_attrs["agent"] = "drawio-optimize"
    root = ET.Element("mxfile", file_attrs)
    page_index = int(graph.page.get("_page_index", "0") or 0)
    page_name = graph.page.get("_page_name") or graph.page.get("name") or "Page-1"
    page_id = graph.page.get("_page_id") or graph.page.get("id") or "pnr-page"

    model = ET.SubElement(
        _make_diagram(root, graph, page_id, page_name),
        "mxGraphModel",
        _model_attrs(graph, autofit),
    )
    root_el = ET.SubElement(model, "root")
    ET.SubElement(root_el, "mxCell", {"id": "0"})
    ET.SubElement(root_el, "mxCell", {"id": "1", "parent": "0"})

    id_map = _write_cells(graph, root_el)
    # preserved cells (unsupported objects) are re-emitted verbatim
    for pc in graph.preserved:
        try:
            el = ET.fromstring(pc.xml)
        except ET.ParseError:
            continue
        target = el
        if _local(el.tag) in ("UserObject", "object"):
            for sub in el:
                if _local(sub.tag) == "mxCell":
                    target = sub
                    break
        pid = target.get("parent")
        if pid in id_map:
            target.set("parent", id_map[pid])
        root_el.append(el)

    for page in sorted(graph.other_pages, key=lambda x: int(x.get("index", 0))):
        try:
            el = ET.fromstring(page["xml"])
        except (ET.ParseError, KeyError):
            continue
        # keep other pages untouched; insert before/after according to index
        if int(page.get("index", 0)) < page_index:
            root.insert(int(page.get("index", 0)), el)
        else:
            root.append(el)

    ET.indent(root, space="  ")
    xml = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    with open(out, "wb") as fh:
        fh.write(xml)
        fh.write(b"\n")
    return out


def _make_diagram(root: ET.Element, graph: Graph, page_id: str, page_name: str) -> ET.Element:
    attrs = dict(graph.page.get("_diagram_attrs") or {})
    attrs["id"] = page_id
    attrs["name"] = page_name
    d = ET.Element("diagram", attrs)
    root.append(d)
    return d


def _model_attrs(graph: Graph, autofit: bool) -> dict[str, str]:
    source = graph.page.get("_model_attrs")
    if source is not None:
        attrs = {str(k): str(v) for k, v in source.items() if v is not None}
    else:
        attrs = {k: str(v) for k, v in DEFAULT_PAGE.items()}
        for k, v in graph.page.items():
            if k.startswith("_") or k in ("id", "name", "autofit", "modified", "host", "agent", "type"):
                continue
            if v is not None and str(v) != "":
                attrs[k] = str(v)
    if autofit and graph.nodes:
        r = merge_rects([n.rect for n in graph.nodes.values()])
        pw = max(850.0, r.x1 + 80)
        ph = max(1100.0, r.y1 + 80)
        attrs["pageWidth"] = str(int(pw))
        attrs["pageHeight"] = str(int(ph))
    return attrs


def _emit_cell(
    root_el: ET.Element,
    attrs: dict[str, Any],
    wrapper: dict[str, Any] | None = None,
    value: str = "",
) -> ET.Element:
    """Append one cell, restoring a ``UserObject``/``object`` wrapper if needed.

    For wrapped cells Draw.io keeps the id and label on the wrapper and the
    style/geometry/parent on the inner ``mxCell``.  Returning the inner cell
    lets callers append ``mxGeometry`` in the same place as for plain cells.
    """
    attrs = dict(attrs)
    if wrapper:
        rid = attrs.pop("id", None)
        wattrs = dict(wrapper.get("attrs") or {})
        if rid is not None:
            wattrs["id"] = rid
        wattrs[wrapper.get("label_key", "label")] = value
        w = ET.Element(wrapper.get("tag") or "UserObject", wattrs)
        cell = ET.Element("mxCell", attrs)
        w.append(cell)
        for extra_xml in wrapper.get("extra", []) or []:
            try:
                w.append(ET.fromstring(extra_xml))
            except ET.ParseError:
                pass
        root_el.append(w)
    else:
        attrs["value"] = value
        cell = ET.Element("mxCell", attrs)
        root_el.append(cell)
    return cell


def _write_cells(graph: Graph, root_el: ET.Element) -> dict[str, str]:
    id_map: dict[str, str] = {}
    used = set(graph.nodes) | {e.id for e in graph.edges} | {"0", "1"}
    # Existing label/preserved ids must not be reused by a newly moved label.
    for e in graph.edges:
        if e.label_cell_id:
            used.add(e.label_cell_id)
        for extra in e.extra_labels:
            if extra.get("id"):
                used.add(str(extra["id"]))
    for pc in graph.preserved:
        if pc.cell_id:
            used.add(pc.cell_id)
    alloc = IdAllocator(used)

    def rel_geom(n: Node) -> tuple[float, float]:
        if n.parent and n.parent in graph.nodes:
            p = graph.nodes[n.parent]
            return (n.x - p.x, n.y - p.y)
        return (n.x, n.y)

    ordered = sorted(
        graph.nodes.values(),
        key=lambda n: (len(graph.ancestors(n.id)), 0 if n.container else 1, n.id),
    )
    container_ids = {n.id for n in graph.nodes.values() if n.container}

    # containers first so children can reference them
    for n in [x for x in ordered if x.container]:
        parent = n.parent if n.parent in container_ids else "1"
        rid = alloc.take("g") if n.id.startswith("__new") else n.id
        id_map[n.id] = rid
        value = n.meta.get("value_html", _html_value(n.label))
        el = _emit_cell(
            root_el,
            {
                "id": rid,
                "style": _container_style(n),
                "vertex": "1",
                "parent": parent,
            },
            n.meta.get("wrapper"),
            value,
        )
        x, y = rel_geom(n)
        ET.SubElement(
            el, "mxGeometry", {"x": _num(x), "y": _num(y), "width": _num(n.w), "height": _num(n.h), "as": "geometry"}
        )

    for n in [x for x in ordered if not x.container]:
        parent = n.parent if n.parent in container_ids else "1"
        rid = n.id if not n.id.startswith("__new") else alloc.take("n")
        id_map[n.id] = rid
        value = n.meta.get("value_html") if n.meta.get("value_html") is not None else _html_value(n.label)
        style = _node_style(n)
        el = _emit_cell(
            root_el,
            {"id": rid, "style": style, "vertex": "1", "parent": parent},
            n.meta.get("wrapper"),
            value,
        )
        x, y = rel_geom(n)
        ET.SubElement(
            el, "mxGeometry", {"x": _num(x), "y": _num(y), "width": _num(n.w), "height": _num(n.h), "as": "geometry"}
        )

    for e in graph.edges:
        eid = e.id if not e.id.startswith("__new") else alloc.take("e")
        id_map[e.id] = eid
        attrs = {
            "id": eid,
            "style": _edge_style(e),
            "edge": "1",
            "parent": "1",
        }
        if e.src and e.src in id_map:
            attrs["source"] = id_map[e.src]
        if e.tgt and e.tgt in id_map:
            attrs["target"] = id_map[e.tgt]
        value = e.meta.get("value_html") if e.meta.get("value_html") is not None else ""
        moved_label = bool(e.meta.get("label_moved"))
        if e.label and (
            e.label_cell_id
            or moved_label
            or abs(e.label_state.rel_x) >= 0.02
            or abs(e.label_state.rel_y) >= 0.5
        ):
            # the text now lives in a child label cell; keeping it on the edge
            # as well would render it twice
            value = ""
        elif e.label and not e.label_cell_id:
            value = _html_value(e.label)
        el = _emit_cell(root_el, attrs, e.meta.get("wrapper"), value)
        geo = ET.SubElement(el, "mxGeometry", {"relative": "1", "as": "geometry"})
        # A routed path replaces the authored waypoints.  A repaired edge can
        # legitimately become a straight two-point path, so the old waypoints
        # must be dropped rather than resurrected (that would re-draw the defect
        # the fix just removed).
        if e.path and (len(e.path) > 2 or e.waypoints):
            pts = e.path[1:-1]
        else:
            pts = list(e.waypoints)
        if pts:
            arr = ET.SubElement(geo, "Array", {"as": "points"})
            for x, y in pts:
                ET.SubElement(arr, "mxPoint", {"x": _num(x), "y": _num(y)})
        # label as a child cell when it is deliberately off-centre
        if e.label and (
            e.label_cell_id
            or moved_label
            or abs(e.label_state.rel_x) >= 0.02
            or abs(e.label_state.rel_y) >= 0.5
        ):
            lid = e.label_cell_id or alloc.take("l")
            style = e.meta.get("label_style")
            if not style and moved_label:
                sm = e.meta.get("style_map", {}) or {}
                style = (
                    "edgeLabel;html=1;align=center;verticalAlign=middle;resizable=0;points=[];"
                    f"fontFamily={sm.get('fontFamily', 'Helvetica')};"
                    f"fontSize={sm.get('fontSize', 10)};"
                    f"fontColor={sm.get('fontColor', '#334155')};"
                    f"labelBackgroundColor={sm.get('labelBackgroundColor', '#ffffff')};"
                    f"whiteSpace={sm.get('whiteSpace', 'wrap')};"
                )
                if sm.get("labelWidth"):
                    style += f"labelWidth={sm['labelWidth']};"
            lbl = _emit_cell(
                root_el,
                {
                    "id": lid,
                    "style": style
                    or "edgeLabel;html=1;align=center;verticalAlign=middle;resizable=0;points=[];"
                    "fontSize=10;fontColor=#334155;labelBackgroundColor=#ffffff;",
                    "vertex": "1",
                    "connectable": "0",
                    "parent": eid,
                },
                e.meta.get("label_wrapper"),
                _html_value(e.label),
            )
            ET.SubElement(
                lbl,
                "mxGeometry",
                {
                    "x": _num(round(e.label_state.rel_x, 4)),
                    "y": _num(round(e.label_state.rel_y, 2)),
                    "relative": "1",
                    "as": "geometry",
                },
            )
        # extra labels that came from the source file
        for extra in e.extra_labels:
            lid = extra.get("id") or alloc.take("l")
            lbl = _emit_cell(
                root_el,
                {
                    "id": lid,
                    "style": extra.get("style")
                    or "edgeLabel;html=1;align=center;verticalAlign=middle;resizable=0;points=[];fontSize=10;",
                    "vertex": "1",
                    "connectable": "0",
                    "parent": eid,
                },
                extra.get("wrapper"),
                _html_value(extra.get("text", "")),
            )
            ET.SubElement(
                lbl,
                "mxGeometry",
                {
                    "x": _num(extra.get("rel_x", 0)),
                    "y": _num(extra.get("rel_y", 0)),
                    "relative": "1",
                    "as": "geometry",
                },
            )
    return id_map


def _num(v: float) -> str:
    if abs(v - round(v)) < 0.01:
        return str(int(round(v)))
    return f"{v:.2f}".rstrip("0").rstrip(".")


def _html_value(text: str) -> str:
    """Produce the semantic HTML string stored in a Draw.io label."""
    return html.escape(str(text), quote=False).replace("\n", "<br>")


def _node_style(n: Node) -> str:
    if n.style and not n.meta.get("generated"):
        if n.meta.get("preserve_style"):
            return n.style
        return merge_style(n.style, {"html": "1", "whiteSpace": "wrap"})
    pal = NODE_STYLES.get(n.kind, NODE_STYLES["default"])
    return (
        "rounded=0;whiteSpace=wrap;html=1;"
        f"fillColor={pal['fill']};strokeColor={pal['stroke']};fontColor={pal['font']};"
        "fontSize=12;align=center;verticalAlign=middle;spacingLeft=6;spacingRight=6;"
        f"pnrKind={n.kind};"
    )


def _container_style(n: Node) -> str:
    if n.style and not n.meta.get("generated"):
        return n.style
    pal = NODE_STYLES["group"]
    return (
        "swimlane=1;container=1;collapsible=0;startSize=28;rounded=1;arcSize=4;"
        f"fillColor={pal['fill']};swimlaneFillColor=#ffffff;strokeColor={pal['stroke']};"
        f"fontColor={pal['font']};fontStyle=1;fontSize=12;align=left;verticalAlign=top;"
        "spacingLeft=10;html=1;pnrKind=group;"
    )


def _edge_style(e: Edge) -> str:
    if e.meta.get("preserve_style") and e.style:
        return e.style
    if e.style and not e.meta.get("generated"):
        base = parse_style(e.style)
        base.update(_anchor_style(e))
        base["pnrSignal"] = e.signal
        # never keep stale automatic routing that would ignore our waypoints
        base.pop("edgeStyle", None)
        base["edgeStyle"] = "none"
        base["orthogonal"] = "1"
        return ";".join(f"{k}={v}" for k, v in base.items()) + ";"
    sig = SIGNAL_STYLES.get(e.signal, SIGNAL_STYLES["default"])
    parts = [
        "edgeStyle=none",
        "orthogonal=1",
        "rounded=0",
        "html=1",
        "endArrow=block",
        "endFill=1",
        "endSize=7",
        "strokeWidth=1.5",
        f"strokeColor={sig['stroke']}",
        "fontSize=10",
        "fontColor=#334155",
        "labelBackgroundColor=#ffffff",
        f"pnrSignal={e.signal}",
    ]
    if sig["dash"]:
        parts += ["dashed=1", f"dashPattern={sig['dash']}"]
    anchor = _anchor_style(e)
    parts += [f"{k}={v}" for k, v in anchor.items()]
    return ";".join(parts) + ";"


def _anchor_style(e: Edge) -> dict[str, str]:
    out: dict[str, str] = {}
    if e.src_port:
        out.update(
            {
                "exitX": _frac(e.src_port.frac),
                "exitY": "1" if e.src_port.side == "B" else ("0" if e.src_port.side == "T" else _frac(e.src_port.frac)),
                "exitDx": "0",
                "exitDy": "0",
            }
        )
        if e.src_port.side in ("L", "R"):
            out["exitX"] = "0" if e.src_port.side == "L" else "1"
            out["exitY"] = _frac(e.src_port.frac)
    if e.tgt_port:
        out.update(
            {
                "entryX": _frac(e.tgt_port.frac),
                "entryY": "1" if e.tgt_port.side == "B" else ("0" if e.tgt_port.side == "T" else _frac(e.tgt_port.frac)),
                "entryDx": "0",
                "entryDy": "0",
            }
        )
        if e.tgt_port.side in ("L", "R"):
            out["entryX"] = "0" if e.tgt_port.side == "L" else "1"
            out["entryY"] = _frac(e.tgt_port.frac)
    return out


def _frac(v: float) -> str:
    # Keep enough decimals that a shared anchor coordinate survives the round
    # trip: at 2 decimals a 66 px side moved by ~0.4 px, which is enough for
    # Draw.io to draw a sloped "straight" wire and clip the arrow (R12).
    v = max(0.0, min(1.0, v))
    return f"{v:.5f}".rstrip("0").rstrip(".")


# ---------------------------------------------------------------------------
# introspection
# ---------------------------------------------------------------------------

def inspect_file(path: str | Path, page: str | int | None = None) -> dict[str, Any]:
    g = load(path, page=page)
    return {
        "path": str(path),
        "page": {k: v for k, v in g.page.items() if not k.startswith("_")},
        "summary": g.summary(),
        "nodes": [
            {
                "id": n.id,
                "label": n.label,
                "rect": [n.x, n.y, n.w, n.h],
                "container": n.container,
                "parent": n.parent,
                "kind": n.kind,
            }
            for n in g.nodes.values()
        ],
        "edges": [
            {
                "id": e.id,
                "src": e.src,
                "tgt": e.tgt,
                "label": e.label,
                "signal": e.signal,
                "waypoints": e.waypoints,
            }
            for e in g.edges
        ],
        "preserved": [
            {"id": p.cell_id, "reason": p.reason, "parent": p.parent} for p in g.preserved
        ],
        "diagnostics": g.diagnostics,
    }
