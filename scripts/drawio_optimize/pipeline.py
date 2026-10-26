"""The Analyze → Detect → Fix → Validate → Render → Inspect → Iterate loop."""
from __future__ import annotations

import platform
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .drawio_io import load, write
from .drc import DrcConfig, Violation, run_drc, drc_summary
from .geometry import Rect, merge_rects, segment_intersection
from .labels import LabelConfig, place_labels
from .layout import LayoutConfig, LayeredLayout
from .model import Graph
from .ports import PortConfig, assign_ports
from .render import (
    export_drawio,
    find_drawio,
    png_liveness,
    render_builtin_png,
    render_builtin_svg,
)
from .report import build_report, write_reports
from .router import Router, RouterConfig
from .scoring import better, raw_metrics, score
from .svgcheck import verify_svg
from .textmetrics import measurer

SKILL_VERSION = "1.3.0"


@dataclass
class PipelineOptions:
    direction: str = "LR"
    preset: str = "balanced"
    layout_mode: str = "auto"          # auto | full | preserve | hybrid
    max_iterations: int = 3
    clearance: float = 14.0
    min_clearance: float = 8.0
    render_formats: tuple[str, ...] = ("png", "svg")
    use_drawio: bool = True
    svg_check: bool = True
    weights: dict = field(default_factory=dict)
    max_preserve_shift: float = 24.0
    route_effort: str = "balanced"
    allow_bus_share: bool = False
    title: str = ""
    repair_only: bool = False


@dataclass
class PipelineResult:
    graph: Graph
    out_dir: Path
    artifacts: dict[str, str | None]
    report: dict
    exit_code: int


ROUTE_RULES = {"R01", "R02", "R04", "R05", "R06", "R07", "R08", "R09", "R11", "R12", "L01"}
PORT_RULES = {"R10", "P02", "P06"}
LAYOUT_RULES = {"P01", "P04", "P05", "P07", "P08"}
LABEL_RULES = {"L01", "L02", "L03", "L05", "L06", "L07", "L04"}


def run_pipeline(graph: Graph, out_dir: str | Path, opts: PipelineOptions | None = None) -> PipelineResult:
    opts = opts or PipelineOptions()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    graph.direction = opts.direction or graph.direction
    if opts.title:
        graph.title = opts.title
    if opts.repair_only:
        if not graph.source_path:
            raise ValueError("repair-only mode needs an existing .drawio input file")
        from .repair import RepairOptions, run_repair_only

        return run_repair_only(
            graph,
            graph.source_path,
            out,
            RepairOptions(
                direction=graph.direction,
                min_clearance=opts.min_clearance,
                render=opts.use_drawio and bool(opts.render_formats),
                render_formats=opts.render_formats,
            ),
        )
    if opts.layout_mode == "auto":
        mode = "preserve" if (graph.source_path or "").lower().endswith((".drawio", ".xml")) else "full"
    else:
        mode = opts.layout_mode
    if mode == "preserve":
        for n in graph.nodes.values():
            n.meta["preserve_mode"] = True

    baseline = graph.geometry_snapshot()
    # displacement is only meaningful when the user gave us an existing drawing
    disp_baseline = baseline if graph.source_path else None
    topology0 = graph.topology_signature()
    base_rect = merge_rects([n.rect for n in graph.nodes.values()]) if graph.nodes else Rect(0, 0, 1, 1)
    diag = max(1.0, (base_rect.w ** 2 + base_rect.h ** 2) ** 0.5)

    layout_cfg = LayoutConfig(direction=graph.direction, mode=mode if mode != "hybrid" else "hybrid")
    labels_cfg = LabelConfig(font_size=10, node_font_size=12)

    ranks: dict = {}
    if mode != "preserve":
        ranks = LayeredLayout(graph, layout_cfg).run().ranks
    else:
        ranks = LayeredLayout(graph, layout_cfg).run().ranks

    drc_cfg = DrcConfig(
        direction=graph.direction,
        min_clearance=opts.min_clearance,
        allow_bus_share=opts.allow_bus_share,
    )
    iterations: list[dict] = []
    score_before = None
    # Honour the geometry that is actually in the input file: Draw.io waypoints
    # are turned into a provisional path so the "before" report describes the
    # defects the user sees, not a fresh route that already hid them.
    authored = _authored_paths(graph)
    # Baseline inspection for existing drawings: keep the authored waypoints
    # and give the remaining edges one provisional route.  Generated specs have
    # no input geometry, so iteration 1 is their honest baseline.
    drc_before: list[Violation] = []
    if authored:
        assign_ports(graph, PortConfig(direction=graph.direction))
        base_router = Router(graph, RouterConfig(clearance=opts.clearance, effort="fast"))
        for e in graph.edges:
            if not e.path and e.src in graph.nodes and e.tgt in graph.nodes:
                r = base_router.route_edge(e)
                e.path = r.path
                base_router.board.register(e)
        drc_before = run_drc(graph, drc_cfg, ranks=ranks)
        metrics_before = raw_metrics(graph, disp_baseline, ranks)
        score_before = score(
            metrics_before, graph, preset=opts.preset, weights=opts.weights,
            hard_failures=drc_summary(drc_before)["hard_failures"], baseline_diagonal=diag,
        )
        iterations.append(
            {
                "iteration": 0,
                "violations": len({v.key() for v in drc_before}),
                "fixed": 0,
                "score": score_before["total"],
                "accepted": True,
                "note": "input geometry (authored waypoints preserved)",
            }
        )
        for e in graph.edges:
            if e.meta.get("authored_path"):
                e.path = []
    else:
        metrics_before = raw_metrics(graph, disp_baseline, ranks)

    prev_score = None
    for it in range(1, max(1, opts.max_iterations) + 1):
        assign_ports(graph, PortConfig(direction=graph.direction))
        Router(graph, RouterConfig(clearance=opts.clearance, effort=opts.route_effort)).route_all()
        place_labels(graph, labels_cfg)
        violations = run_drc(graph, drc_cfg, ranks=ranks)
        raw = raw_metrics(graph, disp_baseline, ranks)
        sc = score(
            raw,
            graph,
            preset=opts.preset,
            weights=opts.weights,
            hard_failures=drc_summary(violations)["hard_failures"],
            baseline_diagonal=diag,
        )
        fixable = [v for v in violations if v.fixable]
        iterations.append(
            {
                "iteration": it,
                "violations": len({v.key() for v in violations}),
                "fixed": 0,
                "score": sc["total"],
                "accepted": True,
                "note": "analyze/detect",
            }
        )
        if it == 1 and not authored:
            drc_before = violations
            metrics_before = raw
            score_before = sc
        if not fixable:
            prev_score = sc
            break
        snapshot = graph.copy()
        applied = _apply_fixes(graph, violations, opts, mode, ranks, labels_cfg)
        assign_ports(graph, PortConfig(direction=graph.direction))
        Router(graph, RouterConfig(clearance=opts.clearance, effort="thorough")).route_all()
        place_labels(graph, labels_cfg)
        violations2 = run_drc(graph, drc_cfg, ranks=ranks)
        raw2 = raw_metrics(graph, disp_baseline, ranks)
        sc2 = score(
            raw2,
            graph,
            preset=opts.preset,
            weights=opts.weights,
            hard_failures=drc_summary(violations2)["hard_failures"],
            baseline_diagonal=diag,
        )
        if better(sc2, sc) or sc2["total"] <= sc["total"] + 1e-6:
            iterations[-1]["fixed"] = applied
            iterations[-1]["note"] = f"fixed {applied} violation(s)"
            prev_score = sc2
            if sc2["total"] >= sc["total"] - 1e-6 and applied == 0:
                break
        else:
            _restore(graph, snapshot)
            iterations[-1]["accepted"] = False
            iterations[-1]["note"] = "rollback (score worsened)"
            prev_score = sc
            break

    # ---- render, inspect the real output, optionally iterate once more ----
    render_info = _render(graph, out, opts)
    rendered_errors = 0
    if render_info.get("svg_check") and render_info["svg_check"].get("verified"):
        _attach_rendered(graph, render_info)
        vrender = run_drc(graph, drc_cfg, ranks=ranks, render=render_info.get("_render_obj"))
        rendered_errors = drc_summary(vrender)["hard_failures"]
        render_info["drc_errors"] = rendered_errors
        if rendered_errors and opts.max_iterations > 1:
            applied = _apply_fixes(graph, vrender, opts, mode, ranks, labels_cfg)
            if applied:
                assign_ports(graph, PortConfig(direction=graph.direction))
                Router(graph, RouterConfig(clearance=opts.clearance, effort="thorough")).route_all()
                place_labels(graph, labels_cfg)
                render_info = _render(graph, out, opts)
                if render_info.get("svg_check") and render_info["svg_check"].get("verified"):
                    _attach_rendered(graph, render_info)

    final_violations = run_drc(
        graph, drc_cfg, ranks=ranks, render=render_info.get("_render_obj")
    )
    metrics_after = raw_metrics(graph, disp_baseline, ranks)
    score_after = score(
        metrics_after,
        graph,
        preset=opts.preset,
        weights=opts.weights,
        hard_failures=drc_summary(final_violations)["hard_failures"],
        baseline_diagonal=diag,
    )
    metrics_after["crossings"] = metrics_after.get("crossings", 0)
    metrics_after["bends"] = metrics_after.get("bends", 0)

    drawio_path = out / "diagram.drawio"
    write(graph, drawio_path)
    validation = validate_output(graph, drawio_path, topology0, render_info)
    artifacts = {
        "drawio": str(drawio_path),
        "png": render_info.get("png_path"),
        "svg": render_info.get("svg_path"),
        "drc_report_json": str(out / "drc-report.json"),
        "drc_report_md": str(out / "drc-report.md"),
    }
    limitations = []
    if not render_info.get("independent"):
        limitations.append(
            "draw.io CLI was not available or its export failed; PNG/SVG are builtin "
            "previews, not an independent render, so rendered-path verification is incomplete."
        )
    if not (render_info.get("svg_check") or {}).get("verified"):
        limitations.append(
            "Rendered-path DRC (R02/R04/R07 on the exported SVG) was not performed; "
            "checks are limited to the model geometry."
        )
    limitations.append(
        "Text width is measured with real font files (Pillow) and a 6% safety margin. "
        "Browser line breaking may still differ by a few pixels for very long labels."
    )
    if graph.preserved:
        limitations.append(
            f"{len(graph.preserved)} unsupported cell(s) were preserved verbatim and treated "
            "as obstacles where possible; they were not restyled."
        )
    report = build_report(
        graph=graph,
        direction=graph.direction,
        preset=opts.preset,
        layout_mode=mode,
        artifacts=artifacts,
        metrics_before=metrics_before,
        metrics_after=metrics_after,
        score_before=score_before,
        score_after=score_after,
        drc_before=drc_before,
        drc_after=final_violations,
        iterations=iterations,
        render=render_info,
        validation=validation,
        toolchain=_toolchain(),
        options={
            "clearance": opts.clearance,
            "min_clearance": opts.min_clearance,
            "max_iterations": opts.max_iterations,
            "route_effort": opts.route_effort,
        },
        limitations=limitations,
    )
    write_reports(report, out / "drc-report.json", out / "drc-report.md")
    passed = report["summary"]["passed"] and score_after["hard_failures"] == 0
    return PipelineResult(
        graph=graph,
        out_dir=out,
        artifacts=artifacts,
        report=report,
        exit_code=0 if passed else 1,
    )


# ---------------------------------------------------------------------------
# fixes
# ---------------------------------------------------------------------------

def _authored_paths(graph: Graph) -> bool:
    """Build the path Draw.io would show from the file's waypoints."""
    found = False
    for e in graph.edges:
        if not e.waypoints or not e.src or not e.tgt:
            continue
        a = graph.nodes.get(e.src)
        b = graph.nodes.get(e.tgt)
        if not a or not b:
            continue
        p0 = e.src_port.point(a.rect) if e.src_port else _boundary_toward(a.rect, e.waypoints[0])
        q0 = e.tgt_port.point(b.rect) if e.tgt_port else _boundary_toward(b.rect, e.waypoints[-1])
        e.path = [p0] + [tuple(w) for w in e.waypoints] + [q0]
        e.meta["authored_path"] = True
        found = True
    return found


def _boundary_toward(rect: Rect, target) -> tuple[float, float]:
    c = rect.center
    # walk from the centre to the target and stop at the first boundary hit
    from .geometry import segment_intersection
    if abs(target[0] - c[0]) >= abs(target[1] - c[1]):
        edge = (rect.x1, c[1]) if target[0] > c[0] else (rect.x, c[1])
        other = (edge[0], target[1])
    else:
        edge = (c[0], rect.y1) if target[1] > c[1] else (c[0], rect.y)
        other = (target[0], edge[1])
    pt = segment_intersection(c, other, (rect.x, rect.y), (rect.x1, rect.y))
    if pt:
        return pt
    # fall back to the nearest side midpoint
    return min(
        [(rect.x1, c[1]), (rect.x, c[1]), (c[0], rect.y1), (c[0], rect.y)],
        key=lambda p: abs(p[0] - target[0]) + abs(p[1] - target[1]),
    )


def _apply_fixes(graph: Graph, violations: list[Violation], opts: PipelineOptions,
                 mode: str, ranks: dict, labels_cfg: LabelConfig) -> int:
    before = len({v.key() for v in violations})
    rules = {v.rule_id for v in violations if v.fixable}
    if rules & PORT_RULES:
        assign_ports(graph, PortConfig(direction=graph.direction))
    if rules & LAYOUT_RULES:
        if mode != "preserve":
            LayeredLayout(graph, LayoutConfig(direction=graph.direction, mode="full")).run()
        else:
            _nudge_overlaps(graph, violations, opts.max_preserve_shift)
    # routing is always re-run when any routing rule fired; labels come last
    # so they can avoid the final paths
    if rules & ROUTE_RULES or rules & LAYOUT_RULES or rules & PORT_RULES:
        clearance = opts.clearance
        for v in violations:
            if v.rule_id == "R04" and v.evidence.get("distance") is not None:
                clearance = max(clearance, float(v.evidence["distance"]) + 6.0)
        Router(graph, RouterConfig(clearance=clearance, effort="thorough")).route_all()
        # still penetrating? open the channels and try once more
        if mode != "preserve":
            probe = run_drc(graph, DrcConfig(direction=graph.direction, min_clearance=opts.min_clearance))
            if any(v.rule_id == "R02" for v in probe):
                LayeredLayout(
                    graph,
                    LayoutConfig(
                        direction=graph.direction,
                        mode="full",
                        h_gap=layout_gap(opts, "h"),
                        v_gap=layout_gap(opts, "v"),
                    ),
                ).run()
                Router(graph, RouterConfig(clearance=clearance, effort="thorough")).route_all()
    if rules & LABEL_RULES or rules & ROUTE_RULES:
        place_labels(graph, labels_cfg)
    after = len({v.key() for v in run_drc(graph, DrcConfig(direction=graph.direction, min_clearance=opts.min_clearance))})
    return max(0, before - after)


def layout_gap(opts: PipelineOptions, axis: str) -> float:
    base = 84.0 if axis == "h" else 52.0
    return base + 36.0


def _nudge_overlaps(graph: Graph, violations: list[Violation], max_shift: float) -> None:
    for v in violations:
        if v.rule_id != "P01" or len(v.objects) < 2:
            continue
        a = graph.nodes.get(v.objects[0])
        b = graph.nodes.get(v.objects[1])
        if not a or not b:
            continue
        mover, fixed = (a, b) if not a.locked else ((b, a) if not b.locked else (None, None))
        if mover is None:
            continue
        dx = (fixed.rect.x1 + 18) - mover.x if mover.rect.cx <= fixed.rect.cx else (fixed.x - 18 - mover.rect.x1)
        dy = (fixed.rect.y1 + 18) - mover.y if mover.rect.cy <= fixed.rect.cy else (fixed.y - 18 - mover.rect.y1)
        if abs(dx) <= abs(dy):
            delta = max(-max_shift, min(max_shift, dx))
            mover.x += delta
        else:
            delta = max(-max_shift, min(max_shift, dy))
            mover.y += delta
        mover.meta["nudge"] = True


# ---------------------------------------------------------------------------
# render + validation
# ---------------------------------------------------------------------------

def _render(graph: Graph, out: Path, opts: PipelineOptions) -> dict:
    info: dict = {"renderer": "none", "independent": False, "format": []}
    drawio_path = out / "diagram.drawio"
    write(graph, drawio_path)
    exe = find_drawio()
    png_path = out / "diagram.png"
    svg_path = out / "diagram.svg"
    info["drawio"] = exe or ""
    if opts.use_drawio and exe:
        if "png" in opts.render_formats:
            r = export_drawio(drawio_path, png_path, "png", scale=2.0)
            if r.ok:
                info["png_path"] = str(png_path)
                info["png"] = png_liveness(png_path)
                info["renderer"] = "drawio"
                info["independent"] = True
            else:
                info["error"] = r.error
        if "svg" in opts.render_formats:
            r = export_drawio(drawio_path, svg_path, "svg")
            if r.ok:
                info["svg_path"] = str(svg_path)
                info["renderer"] = "drawio"
                info["independent"] = True
                if opts.svg_check:
                    check = verify_svg(svg_path, graph)
                    info["_render_obj"] = check
                    info["svg_check"] = check.as_dict()
            else:
                info["error"] = r.error
    if not info.get("png_path"):
        r = render_builtin_png(graph, png_path)
        if r.ok:
            info["png_path"] = str(png_path)
            info["png"] = png_liveness(png_path)
            info["renderer"] = "builtin"
    if not info.get("svg_path"):
        r = render_builtin_svg(graph, svg_path)
        if r.ok:
            info["svg_path"] = str(svg_path)
            if info["renderer"] == "none":
                info["renderer"] = "builtin"
    if not info.get("independent"):
        info["reason"] = (
            info.get("error")
            or "draw.io CLI not installed; used the builtin preview renderer"
        )
    return info


def _attach_rendered(graph: Graph, render_info: dict) -> None:
    obj = render_info.get("_render_obj")
    if obj is None or not obj.verified:
        return
    for e in graph.edges:
        if e.id in obj.paths:
            e.rendered_path = list(obj.paths[e.id])


def validate_output(graph: Graph, path: Path, topology0: tuple, render_info: dict) -> dict:
    notes: list[str] = []
    structure_ok = True
    topology_ok = False
    ids_ok = False
    try:
        again = load(path)
    except Exception as exc:
        return {
            "ok": False,
            "structure_ok": False,
            "topology_ok": False,
            "ids_ok": False,
            "reexport": "not checked",
            "notes": [f"cannot re-open output: {exc}"],
        }
    ids0 = set(graph.nodes) | {e.id for e in graph.edges}
    ids1 = set(again.nodes) | {e.id for e in again.edges}
    ids_ok = ids0 == ids1
    topology_ok = again.topology_signature() == topology0
    if not topology_ok:
        notes.append("topology changed during write/read round-trip")
    for n in again.nodes.values():
        if n.w <= 0 or n.h <= 0:
            structure_ok = False
            notes.append(f"node {n.id} has invalid geometry after round-trip")
    for e in again.edges:
        if not e.src or not e.tgt:
            structure_ok = False
            notes.append(f"edge {e.id} lost an endpoint")
    # write/read again to prove stability
    try:
        second = Path(path).with_suffix(".roundtrip.drawio")
        write(again, second)
        reloaded = load(second)
        if reloaded.topology_signature() != topology0:
            topology_ok = False
            notes.append("second round-trip is not topology-stable")
        second.unlink(missing_ok=True)
    except Exception as exc:
        notes.append(f"round-trip check failed: {exc}")
    reexport = "not checked"
    if render_info.get("independent"):
        reexport = "ok (draw.io exported the written file)"
    return {
        "ok": bool(structure_ok and topology_ok and ids_ok),
        "structure_ok": structure_ok,
        "topology_ok": topology_ok,
        "ids_ok": ids_ok,
        "reexport": reexport,
        "notes": notes,
    }


def _restore(graph: Graph, snapshot: Graph) -> None:
    graph.nodes = snapshot.nodes
    graph.edges = snapshot.edges


def _toolchain() -> dict:
    d = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "skill_version": SKILL_VERSION,
        "drawio": find_drawio() or "not installed",
        "graphviz_dot": shutil.which("dot") or "not installed (optional, unused)",
        "text_backend": measurer(12).backend,
    }
    try:
        import PIL

        d["pillow"] = PIL.__version__
    except Exception:
        d["pillow"] = "not installed"
    try:
        import yaml

        d["pyyaml"] = yaml.__version__
    except Exception:
        d["pyyaml"] = "not installed"
    return d
