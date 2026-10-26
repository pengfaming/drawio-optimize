---
name: drawio-optimize
description: Generate, optimize, minimally repair and DRC-check editable Draw.io (.drawio) engineering diagrams while preserving semantics and topology. Use for architecture diagrams, RTL/SoC blocks, software/API flows, hierarchy and readability improvements, orthogonal routing, port alignment, redundant bends, crossings, dense lanes and label collisions. Existing hand-authored diagrams favor local repair over global rearrangement.
---

# Draw.io Optimize

Minimize visual complexity and reading cost while preserving engineering
semantics and connection topology. Evaluate correctness, readability, hierarchy,
traceability, geometry, visual consistency and editability. An orderly drawing
or zero crossings alone is not sufficient. Deliver a real, editable `.drawio`.

## Quality priorities and design review

Apply these priorities in order; a lower tier must not degrade a higher one:

1. **Correctness** — topology, containment, endpoints, direction, status and meaning.
2. **Readability** — hierarchy, primary flow, grouping, text and traceability.
3. **Geometry** — overlaps, penetration, ports, bends, crossings and wirelength.
4. **Consistency** — stable color, line, size, spacing and label conventions.
5. **Appearance** — balance, compactness, symmetry and decoration.

Before generating or optimizing, read
[engineering-design-principles.md](references/engineering-design-principles.md).
It defines the semantic inventory, overview/detail strategy, coupled layout and
routing decisions, visual encoding, display-scale checks and acceptance review.
Apply it proportionally: a local wire repair needs a local diagnosis, not a
redesign of the whole diagram.

For generation, requested restyling or visual review, also read
[visual-style.md](references/visual-style.md) for palette selection, contrast,
line hierarchy, icon use and export checks. Preserve existing authored styles
in geometry-only repair; apply the authorized personal style pass separately.

## Binding change contract

Read [change-contract.md](references/change-contract.md) before diagram edits.
These are mandatory agent acceptance gates, not newly implemented engine checks:

- Classify constraints by evidence; existing coordinates/routing flags are not
  proof of semantic pins or required channels. Preserve unresolved properties.
- Declare allowed changes and retain a baseline. Recheck the dependency closure
  of every edit, including unchanged neighbors affected by it.
- Compare legal candidates; reject constraint violations before scoring. Do not
  retain a dominated route or excuse stale geometry as an intentional bus.
- Accept only with preservation, measured repair, non-regression, real rendering
  and persistence evidence. Revert failed candidates; missing required evidence
  means incomplete acceptance, even if the CLI summary passes.

Apply the contract within the requested scope. Do not silently expand the task
or claim global optimality. Numeric engine heuristics remain implementation
details; they do not override these decision and acceptance requirements.

## Active personal style profile

The generic change contract is independent of any architecture or palette.
This user's default for generation/restyling is the complete
[T65 profile](references/t65-reference-style.md), using its
[source-verified tokens](references/t65-style-tokens.json). Read that profile for
all module tiers, headers, illustrations and the **real pale enclosure around
the entire system named by the main title**, including routes and footer notes.
A filled title strip or page background alone does not satisfy it.

Apply this authorized style pass separately, including after `--repair-only`
when needed; preserve engineering meaning and functional geometry. Log profile
assignments and any decorative backdrop addition separately. A later explicit
user instruction overrides this personal default without changing the generic
contract. There is no CLI T65 theme option.

## Non-negotiable invariants

1. **Topology is fixed.** Never delete an edge, merge two nets, or change
   `source`/`target` to make a drawing prettier. Repairs are geometry-only.
2. **Check the resolved path against the constraints.** A stored polyline is a
   derived representation, not semantic authority. In orthogonal block diagrams,
   terminal segments must approach the permitted node side perpendicularly.
   Reaching an anchor sideways can make Draw.io clip the path along the border
   and point the arrow the wrong way. Other connection modes require their own
   declared terminal rules; do not apply block-edge rules to unrelated notation.
3. **Never claim visual verification you did not perform.** If the draw.io CLI
   is absent, say the PNG/SVG are builtin previews and that rendered-path DRC was
   skipped. The report records this automatically.
4. **Preserve user objects.** Existing ids, styles (except the separately logged
   authorized personal style pass), pages, unknown cells and
   `<UserObject>` / `<object>` wrappers (with `tooltip`, `data-source`, links)
   are kept. `--layout preserve` (the default for `.drawio` input) does not move
   functional nodes; `--repair-only` changes only the listed wire/label geometry
   plus, when unavoidable, a reported decorative chapter badge/header shift.
5. **Semantics exceed topology.** Preserve roles, membership, signal meaning,
   arrow conventions, implementation/optional/illustrative status and fixed
   ports. Geometry cannot establish these meanings; unresolved meaning is a
   human-confirmation item. Do not equate a crossing with a junction or a legal
   container-boundary crossing with module penetration.
6. **Ports are not interchangeable.** Snap a freely positioned geometric anchor
   only within its allowed side/range. Do not move a named or fixed interface to
   another pin to remove bends. The repair script does not establish pin meaning;
   exclude/revert affected edits when that meaning is fixed or unknown.
7. **Retain the engineering master.** Overview/detail views and paired connectors
   are derived representations with an explicit mapping to master node/edge ids.
   Never simplify the master by deleting detail or hiding a critical connection.
   Existing style preservation takes precedence over general restyling
   recommendations; apply the authorized personal profile separately.

## Workflow

Use **Detect → Diagnose → Plan → Fix → Render → Validate**. The agent supplies
semantic diagnosis and review; the scripts automate only part of this workflow.

1. **Detect:** inventory roles, edges, relation types, direction, containers,
   status and protected objects; classify constraint evidence, declare the allowed
   changes and record the baseline before edits. Establish the
   intended audience and final display size from the request, or state a working
   assumption if absent. Record unknown semantics without inventing them.
2. **Diagnose:** determine the main flow and groups. For a detour, inspect module
   position, port constraints and available channels before adding waypoints.
   For aligned endpoints, check port assignment before deleting waypoints.
3. **Plan:** choose the smallest permitted change. New diagrams use topology →
   main flow → grouping → placement → ports → orthogonal routing → conflicts →
   local iteration. Existing diagrams default to local repair; use `preserve`
   for broader wire changes, `hybrid` for justified local node moves, and `full`
   only when rearrangement fits the request. Modes are not equivalent scopes.
4. **Fix:** keep a separate output and compare with the baseline; recheck affected
   dependencies and neighbors under the change contract. Never accept
   changed semantics, protected geometry or a higher-priority regression just
   because the weighted score improved. Restore the affected change if needed.
5. **Render:** export with draw.io when available; inspect the actual image at
   its intended display size and an enlarged detail scale. Builtin previews
   help inspection but do not establish draw.io render fidelity.
6. **Validate:** check topology/ids, containment, fixed ports, arrows/status,
   paths, labels, scale readability and reopening/editability. Report automated
   results separately from visual review and unresolved semantic questions.

Commands below run from the skill root; in other directories use absolute script
paths. For complex natural language, prepare an explicit spec instead of relying
on the small `--text` parser to infer the engineering design.

```bash
python3 scripts/doctor.py                     # environment + optional tools
python3 scripts/generate.py --spec my.yaml --out-dir out/
python3 scripts/optimize.py --input architecture.drawio --out-dir out/
python3 scripts/optimize.py --input architecture.drawio --layout hybrid --out-dir out/
python3 scripts/optimize.py --input large-atlas.drawio --layout preserve --repair-only --out-dir out/
python3 scripts/drc.py --input diagram.drawio --out-dir drc/
python3 scripts/validate.py --input out/diagram.drawio
```

`generate` and `optimize` run the full loop and write `diagram.drawio`,
`diagram.png`, `diagram.svg`, `drc-report.json`, `drc-report.md`. Read
`drc-report.json.summary` for the automated pass/fail, and
`drc.violations_after` for anything that could not be fixed. `summary.passed`
is not semantic or visual acceptance. See the final review below.

### Minimal repair for a hand-authored atlas

Large atlases often have 500+ nodes, deliberate dense text and long bus routes.
Re-running the full layout/router on them is destructive. Use:

```bash
python3 scripts/optimize.py --input atlas.drawio --layout preserve \
  --repair-only --out-dir atlas-optimized/
```

`--repair-only` preserves functional node geometry, containers and untouched
edges/styles. Its permitted edits are:

- Terminal overshoots, short doglegs and outward hooks: preserve a perpendicular
  18 px arrow stub; unresolved R14 hooks remain hard errors.
- Source staircases/Z detours: snap free anchors on their existing sides within
  the bounded budgets; do not reinterpret a named/fixed pin.
- Dense vertical-bus lanes: use 36 px pitch where possible. Decorative chapter
  badges/headers/title bars may shift by at most 120 px, with reported changes.
- L01/L07 edge-label collisions: search along/across the owning edge; retain and
  report the label when no safe slot exists.

Before this mode, read [optimization.md](references/optimization.md#minimal-repair-mode)
for numeric budgets, rollback gates and scoped/out-of-scope rules; read
[routing-rules.md](references/routing-rules.md) when diagnosing endpoint defects.
The report lists every changed wire/anchor/label/decorative node and unresolved
finding. Scoped success does not mean all placement, penetration, crossing or
text-density findings were checked or resolved. Review protected port semantics
and all changes against the baseline before accepting the result.

`--text` handles `A -> B` lines and a few domain keywords.

```bash
python3 scripts/generate.py \
  --text "SoC DFT 架构图：JTAG -> TAP Controller [TCK/TMS]; TAP Controller -> Scan Controller [scan_en]; TAP Controller -> MBIST Controller [mbist_en]" \
  --out-dir out/
```

## Minimal spec

```yaml
title: SoC DFT Architecture
direction: LR            # LR | RL | TB | BT
preset: balanced         # compact | balanced | clean-routing
groups:
  - id: scan_domain
    label: Scan & MBIST Domain
nodes:
  - id: tap
    label: TAP Controller
    kind: controller
  - id: scan_ctrl
    label: Scan Controller
    kind: controller      # interface/controller/memory/datapath/clock/external/default
    group: scan_domain
  - id: scan_chains
    label: Scan Chains
    kind: datapath
    group: scan_domain
edges:
  - from: scan_ctrl
    to: scan_chains
    label: scan_in
    signal: data          # data | control | clock | bus | power
  - from: tap.R
    to: scan_ctrl.L       # "node.R" / "node.L" / "node.T" / "node.B" pins a side
    label: scan_en
    signal: control
```

Node ids are stable and shared with groups. Custom spec gap/weight settings
are not currently forwarded by the CLI; see `references/optimization.md`.
A group's children are laid out
first; the container is then placed by the parent layout using projected
cross-container edges.

## What the engine does

- **Placement**: layered rank assignment, dummy nodes for long edges, barycenter
  ordering + transpose, degree-2 chain straightening, uniform size per kind
  (an engine default, not a content-aware quality requirement),
  container recursion. Same-layer alignment is checked, not assumed.
- **Ports**: side selection from the declared flow (with a vertical/horizontal
  override for strongly off-axis pairs), ordering by neighbour position, greedy
  anchor snapping so aligned pairs become one straight segment.
- **Routing**: straight / single-bend first, then A* on a Hanan grid with turn,
  crossing, overlap and clearance costs; rip-up and reroute; micro-jog cleanup.
  Containers are not obstacles, but swimlane **title bands** are.
- **DRC**: 33 rules (R01–R14 routing, P01–P10 placement/ports, L01–L07 labels,
  S01–S02 structure). Auto-fix runs only where a safe geometric repair exists.
- **Verification**: when draw.io is installed, its SVG is parsed per
  `data-cell-id`; jump arcs (`C`/`Q` commands) are flattened, both start and end
  arrowheads are recovered, and rectilinearity, endpoint error and bend count are
  compared with the model. The rendered paths are used for a second DRC pass.

## Artifacts

| file | purpose |
|---|---|
| `diagram.drawio` | editable source of truth (mxGraph XML) |
| `diagram.png` | preview (draw.io export, else builtin) |
| `diagram.svg` | vector preview / verification input |
| `drc-report.json` | machine-readable metrics, rules, fixes, limits |
| `drc-report.md` | human-readable version of the same |

## Tool availability

| tool | needed for | if missing |
|---|---|---|
| Python ≥ 3.9 | everything | — |
| Pillow + fonts | real text metrics (CJK) | heuristic estimate, reported as such |
| PyYAML | YAML specs | use JSON |
| draw.io CLI | independent render + rendered-path DRC | builtin preview; report says so |
| Graphviz | nothing (optional cross-check) | no impact |

Run `python3 scripts/doctor.py --json` before promising PNG/SVG fidelity.

## Limitations (state these, do not hide them)

- Text is measured with real font files and a 6% safety margin; browser line
  breaking can still differ for very long labels.
- Cross-container edges are projected for placement, but routing inside a
  container is not a nested floorplan with its own channels.
- Containers are visual groups: edges may cross their bodies (reported as info),
  but not their title text.
- `preserve` mode cannot fix node overlap or uneven spacing without moving
  something; it reports those instead. Use `--layout hybrid` for small nudges.
- Rendered-path DRC checks connectors; label geometry comes from measured fonts,
  because draw.io emits labels as HTML `foreignObject`.
- `--repair-only` deliberately does not clear every warning. Tight module
  clearances, intentional crossings, channel staircases and labels with no free
  slot remain in the report; fixing them would require moving modules or
  rewriting the atlas layout.
- The bus-lane pass only widens a channel by shifting a **decorative numeric
  badge and its adjacent header**; it never moves a functional module. If even
  that shift is not allowed, disable `bundle_shift_decorations` and some lanes
  will stay at the old pitch.
- Endpoint/Z snaps preserve the declared port side. A snap is rolled back if it
  would create an R04 clearance regression or a non-perpendicular arrow.
- SVG jump arcs are flattened to their endpoints, so the decorative 8 px bump is
  not measured; the wire’s topology and clearance are.

## References

- [Engineering design principles](references/engineering-design-principles.md) —
  read before diagram work; semantic review, hierarchy, routing decisions,
  presentation scale and the final acceptance checklist.
- [Change contract](references/change-contract.md) — mandatory constraint
  evidence, dependency checks, candidate comparison, rollback and acceptance.
- [Visual style](references/visual-style.md) — general contrast, line/icon rules
  and actual engine limits.
- [Complete T65 style](references/t65-reference-style.md) and
  [source tokens](references/t65-style-tokens.json) — mandatory personalized
  reference for all module tiers and illustration style.
- `references/routing-rules.md` — R01–R14, router costs, terminal-stub invariant.
- `references/placement-rules.md` — P01–P10, layered layout and containers.
- `references/label-rules.md` — L01–L07, text metrics and draw.io label offsets.
- `references/drawio-format.md` — mxGraph XML, compression, anchors, labels.
- `references/optimization.md` — score, presets, iteration/rollback, report schema.
- `references/examples.md` — DFT, repair, CJK/TB examples with commands.

## Final review and delivery

Read the acceptance checklist in
[engineering-design-principles.md](references/engineering-design-principles.md#acceptance-and-evidence).
Apply the binding gates in [change-contract.md](references/change-contract.md);
the checklist and automated summary cannot waive a failed or skipped gate.
Deliver the editable master, previews and DRC reports; include derived views and
their source mapping when created. In the delivery response (or a review note
for complex diagrams), distinguish:

- **Automated:** topology/ids, round-trip, scoped DRC findings, actual renderer
  and rendered-path checks. A round-trip checks the output against itself; also
  compare input and output to establish preservation.
- **Visual:** what was actually inspected, at what display size/scales, and any
  remaining readability or path issues. An SVG parser is not visual inspection.
- **Semantic:** meanings supported by source material, unresolved questions and
  effects on acceptance; do not claim to have confirmed unknown pin meaning.
- **Changes and limits:** local repairs, moved anchors/nodes/labels, untouched
  out-of-scope findings and any skipped reopening/editability checks.

The current scripts do not automatically review meaning, hierarchy, visual
encoding, final-scale legibility or master/detail mappings. Perform these as
agent review; do not invent CLI flags, DRC rules or report fields for them.
If only geometry was checked, state that visual acceptance is incomplete. The
reader should be able to follow the primary structure without guessing arrow
meaning, hunting endpoints or relying on notes that contradict the drawing.

## Install and verify

```bash
cp -r drawio-optimize ~/.codex/skills/            # or symlink it
python3 ~/.codex/skills/drawio-optimize/scripts/doctor.py
python3 ~/.codex/skills/drawio-optimize/scripts/selfcheck.py   # 49 tests
python3 ~/.codex/skills/drawio-optimize/scripts/generate.py \
  --spec ~/.codex/skills/drawio-optimize/assets/templates/soc_dft.yaml --out-dir /tmp/demo
```

The bundled test suite uses stdlib `unittest`; pytest is not required.
