"""Standalone file validation (structure, round-trip, re-export)."""
from __future__ import annotations

from pathlib import Path

from .drawio_io import load, write
from .render import export_drawio, find_drawio
from .svgcheck import verify_svg


def validate_file(path: str | Path, reexport: bool = True, svg_check: bool = True) -> dict:
    p = Path(path)
    out: dict = {"path": str(p), "ok": False, "checks": {}, "notes": []}
    try:
        g = load(p)
    except Exception as exc:
        out["notes"].append(f"parse failed: {exc}")
        return out
    checks = out["checks"]
    checks["parsed"] = True
    checks["nodes"] = len(g.nodes)
    checks["edges"] = len(g.edges)
    checks["containers"] = sum(1 for n in g.nodes.values() if n.container)
    checks["preserved_cells"] = len(g.preserved)
    ids = set(g.nodes) | {e.id for e in g.edges}
    checks["unique_ids"] = len(ids) == len(g.nodes) + len(g.edges)
    checks["geometry"] = all(n.w > 0 and n.h > 0 for n in g.nodes.values())
    checks["endpoints"] = all(
        (e.src in g.nodes and e.tgt in g.nodes) for e in g.edges if e.src or e.tgt
    )
    checks["floating_edges"] = sum(1 for e in g.edges if not e.src or not e.tgt)
    # round-trip
    tmp = p.with_suffix(".validate.tmp.drawio")
    try:
        write(g, tmp)
        again = load(tmp)
        checks["roundtrip_topology"] = again.topology_signature() == g.topology_signature()
        checks["roundtrip_ids"] = (set(again.nodes) | {e.id for e in again.edges}) == ids
        checks["roundtrip_geometry"] = all(
            abs(again.nodes[nid].x - g.nodes[nid].x) < 0.5
            and abs(again.nodes[nid].y - g.nodes[nid].y) < 0.5
            and abs(again.nodes[nid].w - g.nodes[nid].w) < 0.5
            and abs(again.nodes[nid].h - g.nodes[nid].h) < 0.5
            for nid in g.nodes
        )
    except Exception as exc:
        checks["roundtrip_topology"] = False
        out["notes"].append(f"write failed: {exc}")
    finally:
        tmp.unlink(missing_ok=True)

    if reexport and find_drawio():
        tmp_svg = p.with_suffix(".validate.tmp.svg")
        r = export_drawio(p, tmp_svg, "svg")
        checks["drawio_reexport"] = r.ok
        if r.ok and svg_check:
            vr = verify_svg(tmp_svg, g)
            checks["rendered_paths"] = vr.verified
            checks["rendered_issues"] = vr.issues
            out["render"] = vr.as_dict()
        elif not r.ok:
            out["notes"].append(f"draw.io export failed: {r.error}")
        tmp_svg.unlink(missing_ok=True)
    else:
        checks["drawio_reexport"] = None
        out["notes"].append("draw.io CLI unavailable; re-export not checked")

    required = [
        "parsed",
        "unique_ids",
        "geometry",
        "endpoints",
        "roundtrip_topology",
        "roundtrip_ids",
        "roundtrip_geometry",
    ]
    out["ok"] = all(checks.get(k) for k in required) and checks.get("floating_edges", 0) == 0
    return out
