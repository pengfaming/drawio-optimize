"""Command line interface.  Every capability is reachable from executable code."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from . import __version__
from .drawio_io import load, write
from .drc import DrcConfig, drc_summary, run_drc
from .labels import place_labels, label_report
from .layout import LayoutConfig, LayeredLayout
from .model import Graph
from .pipeline import PipelineOptions, run_pipeline
from .ports import PortConfig, assign_ports, congestion_report
from .render import export_drawio, find_drawio, png_liveness, render_builtin_png, render_builtin_svg
from .report import build_report, write_reports
from .router import Router, RouterConfig
from .scoring import raw_metrics
from .spec import SpecError, graph_from_spec, validate_spec
from .svgcheck import verify_svg
from .textmetrics import measurer
from .util import load_structured
from .validate import validate_file


def main(command: str | None = None, argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(
        prog="drawio-optimize",
        description="Generate, route, check and repair editable Draw.io diagrams.",
    )
    parser.add_argument("--version", action="version", version=f"drawio-optimize {__version__}")
    sub = parser.add_subparsers(dest="cmd")
    if command:
        argv = [command] + argv

    p_doctor = sub.add_parser("doctor", help="inspect environment and optional tools")
    p_doctor.add_argument("--json", action="store_true")

    p_gen = sub.add_parser("generate", help="build a diagram from a spec or text")
    _input_opts(p_gen)
    p_gen.add_argument("--spec", help="YAML/JSON spec")
    p_gen.add_argument("--text", help="natural-language description or 'A -> B' lines")
    p_gen.add_argument("--title")

    p_opt = sub.add_parser("optimize", help="repair/optimise an existing .drawio (or a spec)")
    _input_opts(p_opt)

    p_an = sub.add_parser("analyze", help="show structure, ports, labels and DRC")
    p_an.add_argument("--input", required=True)
    p_an.add_argument("--json", action="store_true")
    p_an.add_argument("--page")

    p_drc = sub.add_parser("drc", help="run DRC and write drc-report.json/md")
    p_drc.add_argument("--input", required=True)
    p_drc.add_argument("--out-dir", default="./pnr-out")
    p_drc.add_argument("--svg", help="rendered SVG to verify (optional)")
    p_drc.add_argument("--page")

    p_lay = sub.add_parser("layout", help="re-layout a .drawio without re-routing")
    p_lay.add_argument("--input", required=True)
    p_lay.add_argument("--out", required=True)
    p_lay.add_argument("--direction", default="LR", choices=["LR", "RL", "TB", "BT"])
    p_lay.add_argument("--mode", default="full", choices=["full", "hybrid", "preserve"])
    p_lay.add_argument("--page")

    p_route = sub.add_parser("route", help="re-assign ports and re-route a .drawio")
    p_route.add_argument("--input", required=True)
    p_route.add_argument("--out", required=True)
    p_route.add_argument("--direction", default="LR", choices=["LR", "RL", "TB", "BT"])
    p_route.add_argument("--clearance", type=float, default=14.0)
    p_route.add_argument("--page")

    p_render = sub.add_parser("render", help="export PNG/SVG from a .drawio")
    p_render.add_argument("--input", required=True)
    p_render.add_argument("--out-dir", default="./render")
    p_render.add_argument("--formats", default="png,svg")
    p_render.add_argument("--scale", type=float, default=2.0)

    p_val = sub.add_parser("validate", help="structural + round-trip + re-export checks")
    p_val.add_argument("--input", required=True)
    p_val.add_argument("--json", action="store_true")
    p_val.add_argument("--no-reexport", action="store_true")

    p_self = sub.add_parser("selftest", help="run the bundled test suite")
    p_self.add_argument("-v", "--verbose", action="store_true")

    args = parser.parse_args(argv)

    if args.cmd == "doctor":
        return _doctor(args)
    if args.cmd == "generate":
        return _generate(args)
    if args.cmd == "optimize":
        return _optimize(args)
    if args.cmd == "analyze":
        return _analyze(args)
    if args.cmd == "drc":
        return _drc(args)
    if args.cmd == "layout":
        return _layout(args)
    if args.cmd == "route":
        return _route(args)
    if args.cmd == "render":
        return _render(args)
    if args.cmd == "validate":
        return _validate(args)
    if args.cmd == "selftest":
        return _selftest(args)
    parser.print_help()
    return 2


def _input_opts(p: argparse.ArgumentParser) -> None:
    p.add_argument("--input", help="existing .drawio file")
    p.add_argument("--out-dir", default="./pnr-out")
    p.add_argument("--direction", default="auto", choices=["auto", "LR", "RL", "TB", "BT"])
    p.add_argument("--preset", default="balanced", choices=["compact", "balanced", "clean-routing"])
    p.add_argument("--layout", default="auto", choices=["auto", "full", "preserve", "hybrid"])
    p.add_argument("--iterations", type=int, default=3)
    p.add_argument("--clearance", type=float, default=14.0)
    p.add_argument("--min-clearance", type=float, default=8.0)
    p.add_argument("--render", default="png,svg")
    p.add_argument("--no-drawio", action="store_true", help="use the builtin preview renderer only")
    p.add_argument(
        "--repair-only",
        action="store_true",
        help="minimal repair of an existing hand-authored drawing: fix terminal "
        "arrow overshoots and edge-label collisions; do not re-layout or re-route",
    )
    p.add_argument("--page")


def _options(args) -> PipelineOptions:
    formats = tuple(x.strip() for x in (args.render or "").split(",") if x.strip())
    return PipelineOptions(
        direction="" if args.direction == "auto" else args.direction,
        preset=args.preset,
        layout_mode=args.layout,
        max_iterations=args.iterations,
        clearance=args.clearance,
        min_clearance=args.min_clearance,
        render_formats=formats,
        use_drawio=not args.no_drawio,
        repair_only=bool(getattr(args, "repair_only", False)),
    )


def _direction_for(graph: Graph, args) -> str:
    return graph.direction if args.direction == "auto" else args.direction


def _generate(args) -> int:
    if not args.spec and not args.text:
        print("error: generate needs --spec or --text", file=sys.stderr)
        return 2
    try:
        if args.spec:
            spec = load_structured(args.spec)
        else:
            from .nl import text_to_spec

            spec = text_to_spec(args.text, title=args.title)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    problems = validate_spec(spec)
    if problems:
        for p in problems:
            print(f"spec error: {p}", file=sys.stderr)
        return 2
    try:
        graph, spec_opts = graph_from_spec(spec)
    except SpecError as exc:
        print(f"spec error: {exc}", file=sys.stderr)
        return 2
    opts = _options(args)
    opts.direction = _direction_for(graph, args)
    if args.preset == "balanced" and spec_opts.preset:
        opts.preset = spec_opts.preset
    if args.layout == "auto":
        opts.layout_mode = "full"
    if spec_opts.layout.h_gap:
        spec_opts.layout.direction = opts.direction or graph.direction
    graph.direction = opts.direction or graph.direction
    if args.title:
        graph.title = args.title
    res = run_pipeline(graph, args.out_dir, opts)
    _print_result(res)
    return res.exit_code


def _optimize(args) -> int:
    if not args.input:
        print("error: optimize needs --input", file=sys.stderr)
        return 2
    p = Path(args.input)
    if p.suffix.lower() in (".yaml", ".yml", ".json"):
        if getattr(args, "repair_only", False):
            print("error: --repair-only needs an existing .drawio input", file=sys.stderr)
            return 2
        spec = load_structured(p)
        problems = validate_spec(spec)
        if problems:
            for x in problems:
                print(f"spec error: {x}", file=sys.stderr)
            return 2
        graph, _ = graph_from_spec(spec)
        mode = "full"
    else:
        try:
            graph = load(p, page=args.page)
        except Exception as exc:
            print(f"error: cannot read {p}: {exc}", file=sys.stderr)
            return 2
        mode = "preserve" if args.layout == "auto" else args.layout
    opts = _options(args)
    opts.direction = _direction_for(graph, args)
    opts.layout_mode = mode
    res = run_pipeline(graph, args.out_dir, opts)
    _print_result(res)
    return res.exit_code


def _analyze(args) -> int:
    try:
        g = load(args.input, page=args.page)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    assign_ports(g, PortConfig(direction=g.direction))
    vs = run_drc(g, DrcConfig(direction=g.direction))
    data = {
        "file": args.input,
        "direction": g.direction,
        "summary": g.summary(),
        "ranks": LayeredLayout(g, LayoutConfig(direction=g.direction, mode="preserve")).run().ranks,
        "congestion": congestion_report(g, PortConfig(direction=g.direction)),
        "labels": label_report(g),
        "drc": drc_summary(vs),
        "violations": [v.to_dict() for v in vs],
        "text_backend": measurer(12).backend,
        "nodes": [
            {"id": n.id, "label": n.label, "rect": [n.x, n.y, n.w, n.h],
             "container": n.container, "parent": n.parent, "locked": n.locked}
            for n in g.nodes.values()
        ],
        "edges": [
            {"id": e.id, "src": e.src, "tgt": e.tgt, "label": e.label,
             "signal": e.signal, "waypoints": e.waypoints, "path": e.path}
            for e in g.edges
        ],
        "preserved": [{"id": p.cell_id, "reason": p.reason} for p in g.preserved],
    }
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        s = data["summary"]
        print(f"{args.input}: {s['nodes']} nodes ({s['containers']} containers), {s['edges']} edges")
        print(f"direction={g.direction}  text={data['text_backend']}")
        print(f"DRC: {len(vs)} finding(s) {data['drc']['by_severity']}")
        for v in vs:
            print(f"  {v.rule_id} {v.severity}: {v.message}")
        if data["congestion"]:
            print(f"congested sides: {len(data['congestion'])}")
        if g.preserved:
            print(f"preserved unsupported cells: {len(g.preserved)}")
    return 0 if data["drc"]["hard_failures"] == 0 else 1


def _drc(args) -> int:
    try:
        g = load(args.input, page=args.page)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    from .pipeline import _authored_paths

    _authored_paths(g)
    assign_ports(g, PortConfig(direction=g.direction))
    _router = Router(g, RouterConfig())
    for _e in g.edges:
        if not _e.path and _e.src in g.nodes and _e.tgt in g.nodes:
            _e.path = _router.route_edge(_e).path
            _router.board.register(_e)
    place_labels(g)
    render_info: dict = {"renderer": "none", "independent": False}
    render_obj = None
    if args.svg:
        render_obj = verify_svg(args.svg, g)
        render_info["svg_check"] = render_obj.as_dict()
        render_info["renderer"] = "drawio"
        render_info["independent"] = render_obj.verified
    vs = run_drc(g, DrcConfig(direction=g.direction), render=render_obj)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    raw = raw_metrics(g)
    report = build_report(
        graph=g,
        direction=g.direction,
        preset="n/a",
        layout_mode="preserve",
        artifacts={"drc_report_json": str(out / "drc-report.json"),
                   "drc_report_md": str(out / "drc-report.md")},
        metrics_before=raw,
        metrics_after=raw,
        score_before=None,
        score_after=None,
        drc_before=vs,
        drc_after=vs,
        iterations=[],
        render=render_info,
        validation={"ok": drc_summary(vs)["hard_failures"] == 0,
                    "structure_ok": True, "topology_ok": True, "ids_ok": True,
                    "reexport": "not checked", "notes": []},
        toolchain={"python": sys.version.split()[0], "drawio": find_drawio() or "not installed"},
        limitations=["DRC only; no fix was applied. Re-run `optimize` to repair findings."],
    )
    write_reports(report, out / "drc-report.json", out / "drc-report.md")
    print(f"{len(vs)} finding(s): {drc_summary(vs)['by_severity']}")
    for v in vs:
        print(f"  {v.rule_id} {v.severity}: {v.message}")
    print(f"wrote {out/'drc-report.json'} and {out/'drc-report.md'}")
    return 0 if drc_summary(vs)["hard_failures"] == 0 else 1


def _layout(args) -> int:
    try:
        g = load(args.input, page=args.page)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    g.direction = args.direction
    info = LayeredLayout(g, LayoutConfig(direction=args.direction, mode=args.mode)).run()
    write(g, args.out)
    print(f"laid out {len(g.nodes)} nodes in {len(info.layers)} rank(s) -> {args.out}")
    return 0


def _route(args) -> int:
    try:
        g = load(args.input, page=args.page)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    g.direction = args.direction
    assign_ports(g, PortConfig(direction=args.direction))
    results = Router(g, RouterConfig(clearance=args.clearance)).route_all()
    write(g, args.out)
    bad = sum(1 for r in results.values() if not r.ok)
    print(f"routed {len(results)} edges, {bad} fallback(s) -> {args.out}")
    return 0 if bad == 0 else 1


def _render(args) -> int:
    try:
        g = load(args.input)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    formats = [x.strip() for x in args.formats.split(",") if x.strip()]
    rc = 0
    for fmt in formats:
        if fmt == "png":
            r = export_drawio(args.input, out / "diagram.png", "png", scale=args.scale)
            if not r.ok:
                print(f"draw.io PNG failed: {r.error}; using builtin", file=sys.stderr)
                r = render_builtin_png(g, out / "diagram.png")
                rc = 1
            if r.ok:
                print(f"PNG: {r.path} ({r.renderer}) {png_liveness(r.path)}")
        elif fmt == "svg":
            r = export_drawio(args.input, out / "diagram.svg", "svg")
            if not r.ok:
                print(f"draw.io SVG failed: {r.error}; using builtin", file=sys.stderr)
                r = render_builtin_svg(g, out / "diagram.svg")
                rc = 1
            if r.ok:
                print(f"SVG: {r.path} ({r.renderer})")
                if r.renderer == "drawio":
                    v = verify_svg(r.path, g)
                    print(f"render check: {json.dumps(v.as_dict(), ensure_ascii=False)}")
        else:
            print(f"unsupported format {fmt!r}", file=sys.stderr)
            rc = 2
    return rc


def _validate(args) -> int:
    result = validate_file(args.input, reexport=not args.no_reexport)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"{args.input}: {'OK' if result['ok'] else 'FAILED'}")
        for k, v in result["checks"].items():
            print(f"  {k}: {v}")
        for note in result["notes"]:
            print(f"  note: {note}")
    return 0 if result["ok"] else 1


def _doctor(args) -> int:
    info = {
        "skill_version": __version__,
        "python": sys.version.split()[0],
        "executable": sys.executable,
        "drawio": find_drawio() or "not installed",
        "pillow": None,
        "pyyaml": None,
        "graphviz_dot": shutil.which("dot") or "not installed (optional)",
        "text_backend": measurer(12).backend,
        "features": {},
    }
    try:
        import PIL

        info["pillow"] = PIL.__version__
    except Exception:
        info["pillow"] = "not installed"
    try:
        import yaml

        info["pyyaml"] = yaml.__version__
    except Exception:
        info["pyyaml"] = "not installed"
    info["features"] = {
        "generate_and_write": True,
        "parse_and_repair": True,
        "layered_layout": True,
        "orthogonal_routing": True,
        "drc": True,
        "score_and_iterate": True,
        "independent_render": bool(find_drawio()),
        "rendered_path_drc": bool(find_drawio()),
        "yaml_specs": info["pyyaml"] != "not installed",
        "text_metrics": info["text_backend"].startswith("pillow"),
    }
    if args.json:
        print(json.dumps(info, ensure_ascii=False, indent=2))
    else:
        for k, v in info.items():
            print(f"{k:18} {v}")
        print("\nP0 path (generate/layout/route/DRC) requires only Python + stdlib.")
        if not find_drawio():
            print("draw.io CLI missing: PNG/SVG fall back to the builtin preview renderer;")
            print("install draw.io desktop or put `drawio` on PATH for independent verification.")
    return 0


def _selftest(args) -> int:
    import unittest

    root = Path(__file__).resolve().parents[2]
    loader = unittest.TestLoader()
    suite = loader.discover(str(root / "tests"), pattern="test_*.py")
    runner = unittest.TextTestRunner(verbosity=2 if args.verbose else 1)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


def _print_result(res) -> None:
    s = res.report["summary"]
    print(
        f"{'PASS' if s['passed'] else 'FAIL'}: {s['nodes']} nodes, {s['edges']} edges, "
        f"violations {s['violations_before']} -> {s['violations_after']}, "
        f"fixed {s['fixed']}, unfixed errors {s['unfixed_error']}"
    )
    print(f"artifacts: {res.out_dir}")
    for k, v in res.artifacts.items():
        if v:
            print(f"  {k}: {v}")
    r = res.report.get("render") or {}
    if r.get("svg_check"):
        print(f"render check: {r['svg_check'].get('verified')} "
              f"({len(r['svg_check'].get('issues') or [])} issue(s))")
    elif r.get("reason"):
        print(f"render note: {r['reason']}")
    if not s["passed"]:
        for v in res.report["drc"]["unfixed_violations"][:10]:
            print(f"  {v['rule']} {v['severity']}: {v['message']}")
