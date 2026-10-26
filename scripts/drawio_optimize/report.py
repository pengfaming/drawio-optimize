"""Machine-readable and human-readable reports."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .drc import RULES, Violation, drc_summary
from .util import sha256_file, write_text


def _violations(vs: list[Violation]) -> list[dict]:
    return [v.to_dict() for v in vs]


def build_report(
    *,
    graph,
    direction: str,
    preset: str,
    layout_mode: str,
    artifacts: dict[str, str | None],
    metrics_before: dict,
    metrics_after: dict,
    score_before: dict | None,
    score_after: dict | None,
    drc_before: list[Violation],
    drc_after: list[Violation],
    iterations: list[dict],
    render: dict,
    validation: dict,
    toolchain: dict,
    options: dict | None = None,
    limitations: list[str] | None = None,
) -> dict:
    summary = drc_summary(drc_after)
    before_keys = {v.key() for v in drc_before}
    after_keys = {v.key() for v in drc_after}
    fixed = [v for v in drc_before if v.key() not in after_keys or v.fixed]
    unfixed = [v for v in drc_after if v.severity in ("error", "warning")]
    files = {}
    for name, path in artifacts.items():
        if not path:
            files[name] = None
            continue
        p = Path(path)
        if name.startswith("drc_report"):
            # written after this report is assembled; its own size is not known
            files[name] = {"path": str(p), "exists": True, "bytes": None,
                           "sha256": None, "note": "written with this report"}
            continue
        files[name] = {
            "path": str(p),
            "exists": p.exists(),
            "bytes": p.stat().st_size if p.exists() else 0,
            "sha256": sha256_file(p) if p.exists() else None,
        }
    hard = summary["hard_failures"]
    distinct_before = len({v.key() for v in drc_before})
    distinct_after = len({v.key() for v in drc_after})
    render = {k: v for k, v in render.items() if not k.startswith("_")}
    report = {
        "schema": "drawio-optimize/report/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "title": getattr(graph, "title", "") or "",
        "input": {
            "source": getattr(graph, "source_path", None),
            "direction": direction,
            "preset": preset,
            "layout_mode": layout_mode,
            "options": options or {},
        },
        "summary": {
            "nodes": len([n for n in graph.nodes.values() if not n.container]),
            "containers": len([n for n in graph.nodes.values() if n.container]),
            "edges": len(graph.edges),
            "violations_before": distinct_before,
            "violations_after": distinct_after,
            "fixed": len(fixed),
            "unfixed_error": hard,
            "unfixed_warning": summary["by_severity"]["warning"],
            "passed": hard == 0 and validation.get("ok", False),
        },
        "metrics": {
            "before": metrics_before,
            "after": metrics_after,
            "score_before": score_before,
            "score_after": score_after,
        },
        "drc": {
            "rules_total": len(RULES),
            "by_severity": summary["by_severity"],
            "by_rule": summary["by_rule"],
            "violations_before": _violations(drc_before),
            "violations_after": _violations(drc_after),
            "fixed_violations": _violations(fixed),
            "unfixed_violations": _violations(unfixed),
        },
        "iterations": iterations,
        "render": render,
        "validation": validation,
        "files": files,
        "toolchain": toolchain,
        "limitations": limitations or [],
    }
    return report


def write_reports(report: dict, json_path: str | Path, md_path: str | Path) -> None:
    write_text(json_path, json.dumps(report, ensure_ascii=False, indent=2))
    write_text(md_path, render_markdown(report))


def render_markdown(r: dict) -> str:
    s = r["summary"]
    verdict = "PASS" if s["passed"] else "FAIL"
    lines = [
        f"# Draw.io Optimize Report — {r.get('title') or 'diagram'}",
        "",
        f"**Result: {verdict}**  ",
        f"Nodes **{s['nodes']}** (+{s['containers']} containers) · edges **{s['edges']}** · "
        f"violations before **{s['violations_before']}** → after **{s['violations_after']}** · "
        f"auto-fixed **{s['fixed']}**  ",
        f"Direction `{r['input']['direction']}` · preset `{r['input']['preset']}` · "
        f"layout `{r['input']['layout_mode']}`  ",
        f"Generated {r['generated_at']}",
        "",
        "## Artifacts",
        "",
        "| file | exists | bytes | sha256 |",
        "|---|---|---|---|",
    ]
    for name, info in r["files"].items():
        if not info:
            lines.append(f"| {name} | no | 0 | — |")
            continue
        size = info["bytes"] if info["bytes"] is not None else "—"
        lines.append(
            f"| `{Path(info['path']).name}` | {'yes' if info['exists'] else 'no'} | "
            f"{size} | `{(info['sha256'] or '')[:16]}` |"
        )
    lines += ["", "## Metrics", "", "| metric | before | after |", "|---|---|---|"]
    for key in sorted(set(r["metrics"]["before"]) | set(r["metrics"]["after"])):
        b = r["metrics"]["before"].get(key, "—")
        a = r["metrics"]["after"].get(key, "—")
        lines.append(f"| {key} | {b} | {a} |")
    sb = r["metrics"].get("score_before") or {}
    sa = r["metrics"].get("score_after") or {}
    if sb and sa:
        lines += [
            "",
            f"Score `{sb.get('preset')}`: **{sb.get('total')} → {sa.get('total')}** "
            f"(hard failures {sa.get('hard_failures')})",
            "",
            "| normalised term | before | after |",
            "|---|---|---|",
        ]
        for key in sorted(set(sb.get("normalised", {})) | set(sa.get("normalised", {}))):
            lines.append(
                f"| {key} | {sb.get('normalised', {}).get(key, '—')} | "
                f"{sa.get('normalised', {}).get(key, '—')} |"
            )

    lines += ["", "## DRC", "", "| rule | severity | violations | fixed | description |",
              "|---|---|---|---|---|"]
    after_counts: dict[str, int] = {}
    fixed_counts: dict[str, int] = {}
    for v in r["drc"]["violations_after"]:
        after_counts[v["rule"]] = after_counts.get(v["rule"], 0) + 1
    for v in r["drc"]["fixed_violations"]:
        fixed_counts[v["rule"]] = fixed_counts.get(v["rule"], 0) + 1
    for rid, info in r["drc"]["by_rule"].items():
        if not after_counts.get(rid) and not fixed_counts.get(rid):
            continue
        lines.append(
            f"| {rid} | {info['severity']} | {after_counts.get(rid, 0)} | "
            f"{fixed_counts.get(rid, 0)} | {info['description'][:90]} |"
        )
    if r["drc"]["unfixed_violations"]:
        lines += ["", "### Unfixed violations", ""]
        for v in r["drc"]["unfixed_violations"][:60]:
            loc = v.get("location")
            where = f" @ ({loc[0]:.0f}, {loc[1]:.0f})" if loc else ""
            lines.append(
                f"- **{v['rule']}** {v['severity']}: {v['message']} "
                f"[objects: {', '.join(v['objects']) or '—'}]{where}"
            )
        if len(r["drc"]["unfixed_violations"]) > 60:
            lines.append(f"- … {len(r['drc']['unfixed_violations']) - 60} more (see JSON)")

    lines += ["", "## Render verification", ""]
    rv = r["render"]
    lines.append(
        f"- renderer: `{rv.get('renderer')}` (independent: {rv.get('independent')})"
    )
    v = rv.get("svg_check") or {}
    lines.append(f"- SVG path check: {'verified' if v.get('verified') else 'not available'}")
    if v.get("verified"):
        endpoint_mismatches = sum(
            1 for err in (v.get("endpoint_errors") or {}).values() if err > 12
        )
        lines.append(
            f"- edges matched: {v.get('edges_matched')} · diagonal segments: "
            f"{v.get('diagonal_segments')} · endpoint mismatches: "
            f"{endpoint_mismatches} · bend mismatches: "
            f"{len(v.get('bend_mismatches') or {})}"
        )
    else:
        lines.append(f"- reason: {v.get('reason') or rv.get('error') or 'draw.io CLI unavailable'}")
    if v.get("limitations"):
        lines.append(f"- limits: {v['limitations']}")
    if rv.get("png"):
        p = rv["png"]
        lines.append(
            f"- PNG: {p.get('width')}×{p.get('height')} px, {p.get('distinct_colors')} colors, "
            f"ink {p.get('ink_ratio')}"
        )
    if rv.get("error"):
        lines.append(f"- export error: {rv['error']}")

    lines += ["", "## Iterations", "", "| # | violations | fixed | score | accepted | note |",
              "|---|---|---|---|---|---|"]
    for it in r["iterations"]:
        lines.append(
            f"| {it.get('iteration')} | {it.get('violations')} | {it.get('fixed')} | "
            f"{it.get('score')} | {it.get('accepted')} | {it.get('note', '')} |"
        )

    val = r["validation"]
    lines += ["", "## Validation", "", f"- round-trip topology: {'OK' if val.get('topology_ok') else 'FAILED'}",
              f"- file structure: {'OK' if val.get('structure_ok') else 'FAILED'}",
              f"- ids preserved: {'OK' if val.get('ids_ok') else 'FAILED'}",
              f"- draw.io re-export: {val.get('reexport', 'not checked')}"]
    for note in val.get("notes", []):
        lines.append(f"- note: {note}")

    lines += ["", "## Toolchain", ""]
    for k, v in r["toolchain"].items():
        lines.append(f"- {k}: `{v}`")
    if r["limitations"]:
        lines += ["", "## Limitations", ""]
        for lim in r["limitations"]:
            lines.append(f"- {lim}")
    lines += [
        "",
        "---",
        "Generated by `drawio-optimize`. The `.drawio` file remains the editable source of truth; "
        "the PNG/SVG are previews.",
        "",
    ]
    return "\n".join(lines)
