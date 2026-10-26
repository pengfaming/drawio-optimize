"""Lightweight natural-language front end.

The agent is expected to turn complex requests into an explicit spec; this
module handles the common cases deterministically and, importantly, never
invents connections that the user did not state.  Domain keywords only select a
starter template; explicit ``A -> B`` relations in the prompt always win.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

TEMPLATES = {
    "soc_dft": {"jtag", "tap", "scan", "mbist", "atpg", "dft"},
    "cpu_block": {"cpu", "alu", "decode", "regfile", "pipeline", "乱序", "流水线"},
    "microservices": {"microservice", "微服务", "服务", "网关", "gateway", "kafka", "redis"},
    "api_flow": {"api", "接口", "调用", "request", "response", "客户端"},
    "rtl_datapath": {"rtl", "datapath", "数据通路", "fifo", "寄存器", "alu"},
}

ARROW = re.compile(
    r"^\s*(?P<a>[^\->\[\]\(\)]+?)\s*(?:->|-->|→|=>)\s*(?P<b>[^\[]+?)\s*"
    r"(?:\[(?P<label>[^\]]*)\]|\((?P<label2>[^\)]*)\))?\s*$"
)


def template_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "assets" / "templates"


def detect_domain(text: str) -> str | None:
    low = text.lower()
    best, score = None, 0
    for name, words in TEMPLATES.items():
        hit = sum(1 for w in words if w in low)
        if hit > score:
            best, score = name, hit
    return best if score >= 2 else None


def load_template(name: str) -> dict[str, Any]:
    path = template_dir() / f"{name}.yaml"
    if not path.exists():
        path = template_dir() / f"{name}.json"
    if not path.exists():
        return {"nodes": [], "edges": []}
    try:
        import yaml

        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        import json

        return json.loads(path.read_text(encoding="utf-8"))


def parse_explicit_edges(text: str) -> list[dict[str, str]]:
    out = []
    for raw in text.replace("；", ";").splitlines():
        for piece in re.split(r"[;；]", raw):
            m = ARROW.match(piece.strip())
            if not m:
                continue
            label = (m.group("label") or m.group("label2") or "").strip()
            out.append({"from": m.group("a").strip(), "to": m.group("b").strip(), "label": label})
    return out


def merge_explicit(spec: dict[str, Any], relations: list[dict[str, str]]) -> dict[str, Any]:
    if not relations:
        return spec
    nodes = {str(n.get("id")): dict(n) for n in spec.get("nodes") or []}
    # explicit arrows often use the human label ("Scan Controller") while the
    # template uses a stable id ("scan_ctrl"); resolve by label first
    by_label = {
        str(n.get("label", "")).strip().lower(): nid for nid, n in nodes.items()
    }
    edges = [dict(e) for e in spec.get("edges") or []]
    for rel in relations:
        resolved = {}
        for key in ("from", "to"):
            name = rel[key]
            nid = name if name in nodes else by_label.get(name.strip().lower(), name)
            resolved[key] = nid
            if nid not in nodes:
                nodes[nid] = {"id": nid, "label": name}
        edges.append({"from": resolved["from"], "to": resolved["to"], "label": rel.get("label", "")})
    spec = dict(spec)
    spec["nodes"] = list(nodes.values())
    spec["edges"] = edges
    return spec


def text_to_spec(text: str, title: str | None = None) -> dict[str, Any]:
    relations = parse_explicit_edges(text)
    domain = detect_domain(text)
    spec: dict[str, Any] = {}
    if domain:
        spec = dict(load_template(domain))
        spec.setdefault("title", title or domain.replace("_", " ").title())
    if not spec.get("nodes"):
        names = _names_from_text(text)
        spec = {
            "title": title or (text.strip().splitlines() or ["Diagram"])[0][:80],
            "direction": "LR",
            "nodes": [{"id": n, "label": n} for n in names],
            "edges": [],
        }
    spec = merge_explicit(spec, relations)
    if not spec.get("nodes") and not relations:
        raise ValueError(
            "could not infer a diagram from the text; provide a spec "
            "(nodes + edges) or use explicit 'A -> B' relations"
        )
    if title:
        spec["title"] = title
    return spec


def _names_from_text(text: str) -> list[str]:
    # "包含/包括/含/with/containing" followed by a list
    m = re.search(r"(?:包含|包括|含|由|with|containing|contains)\s*[：:]?\s*(.+)", text, re.S)
    chunk = m.group(1) if m else ""
    chunk = re.split(r"[。.;；\n]", chunk)[0]
    parts = re.split(r"\s*(?:、|,|，|/|和|and)\s*", chunk)
    names = [p.strip(" 。") for p in parts if 0 < len(p.strip()) <= 40]
    # unique, preserve order
    seen, out = set(), []
    for n in names:
        if n and n not in seen:
            out.append(n)
            seen.add(n)
    return out
