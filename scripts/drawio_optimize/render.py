"""Rendering: real Draw.io export when available, deterministic preview otherwise.

Two renderers exist on purpose:

* ``export_drawio`` runs the installed draw.io CLI and produces the *actual*
  render.  Its SVG is later parsed by ``svgcheck`` to verify the paths the user
  will see.
* ``render_builtin`` draws the model directly (SVG, plus PNG via Pillow).  It is
  a preview and a fallback; it is **not** an independent verification, because
  it shares the model geometry.  Reports label it accordingly.
"""
from __future__ import annotations

import html
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .geometry import Rect, merge_rects
from .model import Graph
from .textmetrics import measurer
from .util import NODE_STYLES, SIGNAL_STYLES, parse_style

try:
    from PIL import Image, ImageDraw, ImageFont

    HAVE_PIL = True
except Exception:  # pragma: no cover
    Image = ImageDraw = ImageFont = None  # type: ignore
    HAVE_PIL = False


@dataclass
class RenderResult:
    ok: bool
    renderer: str
    format: str
    path: str | None = None
    error: str = ""
    command: list[str] = field(default_factory=list)
    independent: bool = False


# ---------------------------------------------------------------------------
# draw.io CLI
# ---------------------------------------------------------------------------

def find_drawio() -> str | None:
    for name in ("drawio", "draw.io", "drawio-desktop"):
        p = shutil.which(name)
        if p:
            return p
    for p in (
        "/usr/bin/drawio",
        "/opt/drawio/drawio",
        "/Applications/draw.io.app/Contents/MacOS/draw.io",
    ):
        if Path(p).exists():
            return p
    return None


def export_drawio(
    drawio_path: str | Path,
    out_path: str | Path,
    fmt: str = "png",
    scale: float = 1.0,
    border: float = 10.0,
    timeout: float = 120.0,
) -> RenderResult:
    exe = find_drawio()
    if not exe:
        return RenderResult(
            ok=False,
            renderer="drawio",
            format=fmt,
            error="draw.io CLI not found (install draw.io desktop or add `drawio` to PATH)",
        )
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        exe,
        "-x",
        "-f",
        fmt,
        "-o",
        str(out),
        "--no-sandbox",
        "-b",
        str(int(border)),
    ]
    if fmt in ("png", "pdf"):
        cmd += ["-s", str(scale)]
    cmd.append(str(drawio_path))
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return RenderResult(
            ok=False, renderer="drawio", format=fmt, error="draw.io export timed out", command=cmd
        )
    except OSError as exc:
        return RenderResult(
            ok=False, renderer="drawio", format=fmt, error=f"cannot run draw.io: {exc}", command=cmd
        )
    if proc.returncode != 0 or not out.exists() or out.stat().st_size == 0:
        return RenderResult(
            ok=False,
            renderer="drawio",
            format=fmt,
            error=(proc.stderr or proc.stdout or "export failed").strip()[-1200:],
            command=cmd,
        )
    return RenderResult(
        ok=True,
        renderer="drawio",
        format=fmt,
        path=str(out),
        command=cmd,
        independent=True,
    )


# ---------------------------------------------------------------------------
# builtin renderer
# ---------------------------------------------------------------------------

def _node_colors(n) -> tuple[str, str, str]:
    st = parse_style(n.style) if n.style else {}
    pal = NODE_STYLES.get(n.kind, NODE_STYLES["default"])
    return (
        st.get("fillColor", pal["fill"]),
        st.get("strokeColor", pal["stroke"]),
        st.get("fontColor", pal["font"]),
    )


def _edge_colors(e) -> tuple[str, str]:
    st = parse_style(e.style) if e.style else {}
    sig = SIGNAL_STYLES.get(e.signal, SIGNAL_STYLES["default"])
    return st.get("strokeColor", sig["stroke"]), sig["dash"]


def _content_bbox(graph: Graph, pad: float = 30.0) -> Rect:
    rects = [n.rect for n in graph.nodes.values()]
    for e in graph.edges:
        for p in e.path:
            rects.append(Rect(p[0], p[1], 0, 0))
    r = merge_rects(rects)
    return Rect(r.x - pad, r.y - pad, r.w + 2 * pad, r.h + 2 * pad)


def render_builtin_svg(graph: Graph, out_path: str | Path) -> RenderResult:
    box = _content_bbox(graph)
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{box.w:.0f}" height="{box.h:.0f}" '
        f'viewBox="{box.x:.1f} {box.y:.1f} {box.w:.1f} {box.h:.1f}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        '<g font-family="Noto Sans CJK SC, DejaVu Sans, Helvetica, sans-serif">',
    ]
    # containers behind everything
    for n in graph.nodes.values():
        if not n.container:
            continue
        fill, stroke, font = _node_colors(n)
        parts.append(
            f'<rect x="{n.x:.1f}" y="{n.y:.1f}" width="{n.w:.1f}" height="{n.h:.1f}" '
            f'rx="6" fill="{fill}" stroke="{stroke}" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{n.x + 10:.1f}" y="{n.y + 19:.1f}" font-size="12" font-weight="600" '
            f'fill="{font}">{html.escape(n.label)}</text>'
        )
    for n in graph.nodes.values():
        if n.container:
            continue
        fill, stroke, font = _node_colors(n)
        parts.append(
            f'<rect x="{n.x:.1f}" y="{n.y:.1f}" width="{n.w:.1f}" height="{n.h:.1f}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="1.2"/>'
        )
        m = measurer(float(n.meta.get("font_size", 12)), False)
        lines = m.wrap(n.label, max(10.0, n.w - 14))
        line_h = m.font_size * 1.24
        total = len(lines) * line_h
        cy = n.y + n.h / 2 - total / 2 + line_h * 0.78
        for i, line in enumerate(lines):
            parts.append(
                f'<text x="{n.x + n.w / 2:.1f}" y="{cy + i * line_h:.1f}" text-anchor="middle" '
                f'font-size="{m.font_size:.0f}" fill="{font}">{html.escape(line)}</text>'
            )
    # edges
    for e in graph.edges:
        if len(e.path) < 2:
            continue
        stroke, dash = _edge_colors(e)
        pts = " ".join(f"{p[0]:.1f},{p[1]:.1f}" for p in e.path)
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
        parts.append(
            f'<polyline points="{pts}" fill="none" stroke="{stroke}" stroke-width="1.5"{dash_attr}/>'
        )
        _append_arrow_svg(parts, e.path, stroke)
        if e.label:
            cx, cy = (
                e.label_state.absolute
                if e.label_state.absolute
                else (e.path[0][0], e.path[0][1])
            )
            m = measurer(float(e.meta.get("label_font_size", 10)), False)
            w, h, _ = m.label_box(e.label)
            parts.append(
                f'<rect x="{cx - w / 2:.1f}" y="{cy - h / 2:.1f}" width="{w:.1f}" height="{h:.1f}" '
                f'fill="#ffffff" fill-opacity="0.92"/>'
            )
            parts.append(
                f'<text x="{cx:.1f}" y="{cy + m.font_size * 0.35:.1f}" text-anchor="middle" '
                f'font-size="{m.font_size:.0f}" fill="#334155">{html.escape(e.label)}</text>'
            )
    parts += ["</g>", "</svg>"]
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(parts), encoding="utf-8")
    return RenderResult(ok=True, renderer="builtin", format="svg", path=str(out), independent=False)


def _append_arrow_svg(parts: list[str], path, color: str) -> None:
    (x1, y1), (x2, y2) = path[-2], path[-1]
    dx, dy = x2 - x1, y2 - y1
    L = (dx * dx + dy * dy) ** 0.5 or 1.0
    ux, uy = dx / L, dy / L
    px, py = -uy, ux
    size, half = 8.0, 3.8
    bx, by = x2 - ux * size, y2 - uy * size
    pts = f"{x2:.1f},{y2:.1f} {bx + px * half:.1f},{by + py * half:.1f} {bx - px * half:.1f},{by - py * half:.1f}"
    parts.append(f'<polygon points="{pts}" fill="{color}" stroke="{color}"/>')


def render_builtin_png(graph: Graph, out_path: str | Path, scale: float = 2.0) -> RenderResult:
    if not HAVE_PIL:
        return RenderResult(
            ok=False, renderer="builtin", format="png", error="Pillow not installed"
        )
    box = _content_bbox(graph)
    W, H = int(box.w * scale), int(box.h * scale)
    img = Image.new("RGB", (max(10, W), max(10, H)), "white")
    draw = ImageDraw.Draw(img)
    cjk = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
    font_path = cjk if Path(cjk).exists() else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

    def fnt(size: int):
        try:
            return ImageFont.truetype(font_path, max(6, int(size * scale)))
        except Exception:
            return ImageFont.load_default()

    def X(v):
        return (v - box.x) * scale

    def Y(v):
        return (v - box.y) * scale

    for n in graph.nodes.values():
        if not n.container:
            continue
        fill, stroke, font = _node_colors(n)
        draw.rounded_rectangle([X(n.x), Y(n.y), X(n.rect.x1), Y(n.rect.y1)], radius=int(6 * scale),
                               fill=fill, outline=stroke)
        draw.text((X(n.x + 10), Y(n.y + 6)), n.label, fill=font, font=fnt(12))
    for n in graph.nodes.values():
        if n.container:
            continue
        fill, stroke, font = _node_colors(n)
        draw.rectangle([X(n.x), Y(n.y), X(n.rect.x1), Y(n.rect.y1)], fill=fill, outline=stroke)
        m = measurer(float(n.meta.get("font_size", 12)), False)
        lines = m.wrap(n.label, max(10.0, n.w - 14))
        lh = m.font_size * 1.24
        cy = n.y + n.h / 2 - len(lines) * lh / 2
        f = fnt(m.font_size)
        for i, line in enumerate(lines):
            tw = draw.textlength(line, font=f)
            draw.text((X(n.x + n.w / 2) - tw / 2, Y(cy + i * lh)), line, fill=font, font=f)
    for e in graph.edges:
        if len(e.path) < 2:
            continue
        stroke, _ = _edge_colors(e)
        draw.line([(X(p[0]), Y(p[1])) for p in e.path], fill=stroke, width=max(1, int(1.5 * scale)))
        (x1, y1), (x2, y2) = e.path[-2], e.path[-1]
        dx, dy = x2 - x1, y2 - y1
        L = (dx * dx + dy * dy) ** 0.5 or 1.0
        ux, uy = dx / L, dy / L
        px, py = -uy, ux
        s, hw = 8.0, 3.8
        bx, by = x2 - ux * s, y2 - uy * s
        draw.polygon(
            [(X(x2), Y(y2)), (X(bx + px * hw), Y(by + py * hw)), (X(bx - px * hw), Y(by - py * hw))],
            fill=stroke,
        )
        if e.label and e.label_state.absolute:
            cx, cy = e.label_state.absolute
            m = measurer(10, False)
            w, h, _ = m.label_box(e.label)
            draw.rectangle([X(cx - w / 2), Y(cy - h / 2), X(cx + w / 2), Y(cy + h / 2)], fill="white")
            f = fnt(10)
            tw = draw.textlength(e.label, font=f)
            draw.text((X(cx) - tw / 2, Y(cy - m.font_size * 0.6)), e.label, fill="#334155", font=f)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "PNG")
    return RenderResult(ok=True, renderer="builtin", format="png", path=str(out), independent=False)


def png_liveness(path: str | Path) -> dict:
    if not HAVE_PIL:
        return {"checked": False, "reason": "Pillow not installed"}
    try:
        img = Image.open(path).convert("RGB")
        colors = img.getcolors(maxcolors=1_000_000) or []
        nonwhite = sum(c for c, col in colors if col != (255, 255, 255))
        total = img.width * img.height
        return {
            "checked": True,
            "width": img.width,
            "height": img.height,
            "distinct_colors": len(colors),
            "ink_ratio": round(nonwhite / max(1, total), 4),
            "blank": nonwhite / max(1, total) < 0.001,
        }
    except Exception as exc:
        return {"checked": False, "reason": str(exc)}
