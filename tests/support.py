"""Shared helpers for the drawio-optimize test suite (stdlib unittest only)."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from drawio_optimize.drawio_io import load, write  # noqa: E402
from drawio_optimize.drc import DrcConfig, run_drc  # noqa: E402
from drawio_optimize.geometry import Rect  # noqa: E402
from drawio_optimize.labels import place_labels  # noqa: E402
from drawio_optimize.layout import LayoutConfig, LayeredLayout  # noqa: E402
from drawio_optimize.model import Edge, Graph, Node, Port  # noqa: E402
from drawio_optimize.pipeline import PipelineOptions, run_pipeline  # noqa: E402
from drawio_optimize.ports import PortConfig, assign_ports  # noqa: E402
from drawio_optimize.router import Router, RouterConfig  # noqa: E402


def graph(nodes, edges, direction="LR"):
    g = Graph(direction=direction)
    for spec in nodes:
        nid, label = spec[0], spec[1]
        kw = spec[2] if len(spec) > 2 else {}
        g.add_node(Node(nid, label, **kw))
    for spec in edges:
        eid, s, t = spec[:3]
        kw = spec[3] if len(spec) > 3 else {}
        g.edges.append(Edge(eid, s, t, **kw))
    return g


def routed(g, direction=None, clearance=14.0):
    d = direction or g.direction
    assign_ports(g, PortConfig(direction=d))
    Router(g, RouterConfig(clearance=clearance)).route_all()
    return g


def drc(g, direction=None, **kw):
    return run_drc(g, DrcConfig(direction=direction or g.direction, **kw))


def tmpdir():
    return tempfile.TemporaryDirectory(prefix="drawio-optimize-test-")


def options(**kw):
    kw.setdefault("use_drawio", False)
    kw.setdefault("render_formats", ("png",))
    kw.setdefault("max_iterations", 2)
    return PipelineOptions(**kw)


def pipeline(g, out, **kw):
    return run_pipeline(g, out, options(**kw))


def write_bad_jog(path: Path, through_obstacle: bool = False, label: str = "data"):
    """A Draw.io file with a terminal jog on e1 and (optionally) a wire through a box."""
    pts = [(160, 250), (140, 250)]
    extra = ""
    if through_obstacle:
        pts = [(300, 200), (300, 240)]
        extra = (
            '<mxCell id="ob" value="Obstacle" style="rounded=0;whiteSpace=wrap;html=1;'
            'fillColor=#ffe6cc;strokeColor=#d79b00;" vertex="1" parent="1">'
            '<mxGeometry x="260" y="180" width="90" height="90" as="geometry"/></mxCell>'
        )
    xml = (
        '<mxfile host="test"><diagram id="p" name="bad"><mxGraphModel dx="800" dy="600" '
        'page="1" pageWidth="850" pageHeight="1100"><root><mxCell id="0"/>'
        '<mxCell id="1" parent="0"/>'
        '<mxCell id="a" value="A" style="rounded=0;whiteSpace=wrap;html=1;" vertex="1" parent="1">'
        '<mxGeometry x="100" y="100" width="120" height="60" as="geometry"/></mxCell>'
        '<mxCell id="b" value="B" style="rounded=0;whiteSpace=wrap;html=1;" vertex="1" parent="1">'
        '<mxGeometry x="100" y="300" width="120" height="60" as="geometry"/></mxCell>'
        f"{extra}"
        '<mxCell id="e1" style="edgeStyle=orthogonalEdgeStyle;endArrow=block;html=1;'
        'exitX=0.5;exitY=1;entryX=0.5;entryY=0;" edge="1" parent="1" source="a" target="b">'
        '<mxGeometry relative="1" as="geometry"><Array as="points">'
        + "".join(f'<mxPoint x="{x}" y="{y}"/>' for x, y in pts)
        + "</Array></mxGeometry></mxCell>"
        f'<mxCell id="lab" value="{label}" style="edgeLabel;html=1;fontSize=10;" vertex="1" '
        'connectable="0" parent="e1"><mxGeometry x="0" y="0" relative="1" as="geometry"/></mxCell>'
        "</root></mxGraphModel></diagram></mxfile>"
    )
    path.write_text(xml, encoding="utf-8")
    return path
