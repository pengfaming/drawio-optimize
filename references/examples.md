# Examples (commands and measured results)

All commands below are run from the skill root and use the bundled environment
(Python 3.10, draw.io 31.4.5, Pillow, PyYAML). "0 violations" means the report's
`summary.unfixed_error` is 0 and `render.svg_check.verified` is true.

## 1. SoC DFT architecture (the primary acceptance case)

```bash
python3 scripts/generate.py \
  --spec assets/templates/soc_dft.yaml \
  --out-dir out/soc-dft --iterations 2
```

Result on this machine:

```
PASS: 8 modules, 2 containers, 10 edges
violations 0 -> 0, unfixed errors 0
render check: True (0 issues)
```

What the engine produced:

- `JTAG / TAP Domain` → `Scan & MBIST Domain` left-to-right hierarchy;
- `JTAG Port → TAP Controller → Scan/MBIST` control path (orange, dashed);
- `Scan Controller ↔ Scan Chains` data path (blue) with the return edge on its own
  channel; `MBIST → SRAM` data path;
- `Clock / Reset` as a separate source, clocks routed on a lower rail (purple,
  dotted), not through any module;
- every connector orthogonal, no redundant bends, no wire through a box;
- the exported SVG was parsed per edge: all 10 edges matched, 0 diagonal segments,
  0 bend mismatches, 0 endpoint mismatches.

The template is intentionally realistic: two containers, one cycle
(`scan_ctrl → scan_chains → scan_ctrl`), three clock fans, and a cross-container
data path.

## 2. Repair an existing drawing

Given a file whose `e1` has two near-target waypoints (the classic terminal jog),
a wire through an obstacle, an edge label on a cramped back edge, and a CJK label
too large for its 70×30 box:

```bash
python3 scripts/optimize.py \
  --input architecture.drawio \
  --out-dir out/repair \
  --layout preserve --iterations 2
```

Measured result:

```
PASS: 6 nodes, 2 edges
violations 5 -> 0, fixed 5, unfixed errors 0
preserve mode: all node x/y unchanged (the too-small node grew in height for text)
render check: True (0 issues)
```

Fixed in this run: `L04` overflow, `R01` redundant bends, `R05` reversal, `R07`
diagonal waypoint, `R12` sideways entry. The report lists them under
`drc.violations_before` with object ids and locations; `drc.fixed_violations`
shows the same keys resolved.

## 3. Chinese labels, top-to-bottom

```bash
python3 scripts/generate.py --spec assets/examples/cjk_tb.yaml --out-dir out/cjk
```

Measured result: 6 nodes, 6 edges, 0 errors, 3 findings (`P09` grouping info,
one avoidable crossing, one long-detour info). The PNG contains the Chinese text
and `text_backend` reports the real fonts used (Noto Sans CJK + DejaVu Sans).

The spec demonstrates groups named in Chinese, `direction: TB`, and labels such as
`校验令牌`, `创建订单`, `订单数据库（主库）`.

## 4. Microservices / API flow / RTL / CPU templates

```bash
for t in microservices api_flow rtl_datapath cpu_block; do
  python3 scripts/generate.py --spec assets/templates/$t.yaml --out-dir out/$t --iterations 2
done
```

Observed outcomes (no errors, warnings only):

| template | nodes / edges | errors | remaining warnings |
|---|---|---|---|
| microservices | 10 / 10 | 0 | 1 avoidable crossing, 1 grouping info |
| api_flow | 8 / 9 | 0 | 1 label-placement info |
| rtl_datapath | 12 / 14 | 0 | 3 crossings, 1 port-alignment warning, 1 grouping info |
| cpu_block | 13 / 15 | 0 | 8 crossings, 1 port alignment, 1 grouping info |

Crossings are warnings by design; a dense CPU block diagram is not expected to
be crossing-free. Use `--preset clean-routing` when crossings matter most.

## 5. Natural language

```bash
python3 scripts/generate.py \
  --text "请画一个 SoC DFT 架构图，包含 JTAG、TAP Controller、Scan Controller、Scan Chains、MBIST Controller。要求信号从左向右流动，控制信号与数据路径区分颜色。" \
  --out-dir out/nl
```

The prompt matches the `soc_dft` template (≥2 domain keywords). Explicit
`A -> B [label]` relations are merged; if a relation uses a human label that
already exists in the template (`"Scan Controller"`), it is resolved to that
node's id instead of creating a duplicate.

## 6. DRC only

```bash
python3 scripts/drc.py --input out/soc-dft/diagram.drawio --out-dir out/drc
python3 scripts/drc.py --input out/soc-dft/diagram.drawio --svg out/soc-dft/diagram.svg --out-dir out/drc
```

The second form checks the *rendered* paths. Without `--svg`, the model's
orthogonal paths are re-derived from the file's anchors and waypoints.

## 7. Inspect and validate

```bash
python3 scripts/analyze.py --input out/soc-dft/diagram.drawio --json
python3 scripts/validate.py --input out/soc-dft/diagram.drawio
```

`analyze` prints structure, ports, congestion, label statistics, text backend and
the current DRC. `validate` checks parse → write → parse stability, id preservation
and (when draw.io is present) re-export.

## 8. Test suite

```bash
python3 scripts/selfcheck.py
# Ran 49 tests ... OK
```

The suite covers: straight LR/TB connections, anchor snapping, terminal-jog
repair, terminal-dogleg anchor snap, source-staircase and Z-shape endpoint snaps,
dense vertical-bus lane assignment with a reported decorative badge shift,
obstacle penetration, crossing removal, round-trip/topology/id stability,
unknown-cell preservation, CJK label fitting, all 33 DRC rule registrations,
scoring/normalisation, CLI artifacts, real SVG path verification (skipped when
draw.io is absent), wrapped `UserObject` cells, jump-arc/bidirectional-arrow SVG
parsing, minimal terminal repair, moved-label geometry, all four layout
directions, container containment, self-loops, disconnected components, and the
natural-language path.

## 9. Minimal repair of a large atlas

```bash
python3 scripts/optimize.py \
  --input 24070015一生一芯全架构一览图.drawio \
  --out-dir atlas-optimized \
  --layout preserve --repair-only --direction TB
```

The input is a 585-node / 136-edge, 7820×6920 atlas with `UserObject` wrappers,
bidirectional arrows, jump arcs and long hand-authored buses. The run:

- fixed 5 terminal overshoots (`wire-14/72/74/128/151`) with 18 px arrow stubs;
- removed 28 source-staircase/Z detours, including `wire-8` (`④ main 里调用`) and
  the three `soc-xbar2` outputs, by snapping one endpoint anchor per edge;
- collapsed six long vertical bus runs onto evenly spaced 36 px lanes; the decorative `04` badge and the `ysyxSoCFull`/`ysyxSoCASIC`/`NPC TOP` title bars moved right as needed (120/120/88/55 px, with the title widths reduced to stay inside their containers) and are listed in `input.options.node_changes`;
- moved 28 edge labels that were crossed or occluded, including labels that had
  to follow a long bus onto a clear horizontal arm;
- reduced scoped DRC findings from 107 to 15, with **0 errors** and 93 findings
  fixed; the R13 dense-parallel-wire rule found 14 tight pairs and the bus/dense
  passes cleared all 14 (R13 14 → 0); four outward terminal hooks
  (`wire-31/115/131/153`) were cleared under hard rule R14, keeping an 18 px
  arrow stub and snapping only one target anchor (`wire-131`, 30 px) where a
  lateral offset remained;
- preserved topology, functional node geometry, all node styles and all edge
  styles except reported anchor fractions; only the repaired wires, endpoint
  anchors, bus lanes, four decorative node shifts and labels changed.

Artifacts: `diagram.drawio`, `diagram.png`, `diagram.svg`, `drc-report.json`,
`drc-report.md`. The report’s `input.options` lists every changed edge with
before/after waypoints, anchor fractions or label offsets, plus every
`node_shift`;
`unfixed_labels` / `unfixed_doglegs` / `unfixed_bundle` list findings that had
no safe slot within the budget.
