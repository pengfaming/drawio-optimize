# Optimization, scoring and sign-off

## Quality priorities versus the engine score

Follow [engineering-design-principles.md](engineering-design-principles.md)
for semantic diagnosis, hierarchy, traceability, display-scale review and final
acceptance. The agent's priority order is correctness → readability → geometry
→ consistency → appearance. Reject a lower-priority improvement that damages a
higher priority, even when its numeric score is lower.

The current engine uses a weighted geometric heuristic. It does not encode
those tiers lexicographically or verify semantic meaning, status, named pins,
visual hierarchy or final-scale readability. Review input/output semantics and
protected geometry separately; restore edits that violate those constraints.

## Metrics

All terms are made dimensionless before they are added; raw units never mix.

| metric | raw | normalisation |
|---|---|---|
| crossings | pairwise proper crossings | ÷ edges |
| bends | sum of direction changes | ÷ edges |
| wirelength | sum of polyline length | ÷ (edges × 4 × average node size) |
| clearance | R04 violations | ÷ edges |
| alignment | per-rank spread of centres | ÷ (nodes × average node size) |
| labels | label overlaps + node overlaps | ÷ (labels + nodes) |
| displacement | Σ\|Δx\|+\|Δy\|+\|Δw\|+\|Δh\| | ÷ diagram diagonal |

```
Score = w_cross · crossings
      + w_bend  · bends
      + w_wl    · wirelength
      + w_clr   · clearance
      + w_align · alignment
      + w_label · labels
      + w_disp  · displacement
      + 1000 × hard_failures
```

`hard_failures` are DRC `error` findings such as penetration, overlap, reversal
and invalid geometry. The 1000-point term is a finite penalty, not a proof that
an error can never be traded against soft metrics. Topology preservation and
other validation checks are also required. Do not accept new hard failures for
a prettier drawing; inspect actual findings rather than only the total score.

## Presets

| weight | compact | balanced | clean-routing |
|---|---|---|---|
| crossings | 1.0 | 1.0 | 2.0 |
| bends | 0.6 | 1.0 | 1.8 |
| wirelength | 1.2 | 0.8 | 0.5 |
| clearance | 1.0 | 1.2 | 1.5 |
| alignment | 0.4 | 1.0 | 0.5 |
| labels | 1.0 | 1.2 | 1.5 |
| displacement | 0.2 | 1.0 | 1.0 |

Use `balanced` by default. Consider `compact` only if the final display-scale
review still passes; dense slides often need a simpler overview instead.
`clean-routing` favors geometry for reviews/print but does not establish overall
quality. Custom weights can be passed programmatically through `PipelineOptions`.
The spec parser reads `weights:`, but the current generate/optimize CLI does not
forward these to `PipelineOptions`; do not promise spec weight overrides work.

## The loop

```
analyze input (authored waypoints become a provisional path)
  └─ detect → score
       ├─ fix ports / layout / routes / labels
       ├─ re-route, re-place labels
       ├─ detect + score again
       └─ keep if score improves, otherwise restore the snapshot
```

- `max_iterations` (default 3) bounds the loop; a rollback stops it.
- The iteration log records violations, score, accepted/rolled-back and a note.
- Rendering happens after the model loop. The exported SVG is parsed and the
  rendered paths feed a second DRC pass. If that finds new errors, one more fix
  cycle is attempted.
- Every run writes the report even when it fails, so an unfixed defect is visible
  rather than hidden.

## Minimal repair mode

`--repair-only` is a separate, conservative loop for existing hand-authored
drawings. It deliberately does **not** run layout, port assignment or global
re-routing.

| step | action |
|---|---|
| baseline | export the input with draw.io, parse the SVG, use the rendered paths for DRC |
| wire repair | R12/R01 terminal overshoot → remove the backtrack and leave an 18 px perpendicular stub |
| dogleg repair | R01 short penultimate step (4–40 px) → snap the target anchor onto the incoming trunk; max 40 px, only if the target side has a free anchor slot |
| hook repair | R14 outward U-bump before the arrow → move the last trunk to a lane with a full 18 px stub, collapse tiny crumbs, and snap the target anchor only if a lateral dogleg remains; R14 is a hard error |
| endpoint repair | short source staircase (≤40 px per step) or a parallel-ended Z shape → snap the source/target anchor onto the trunk; Z shift max 220 px |
| bus lanes | long parallel vertical runs in the left channel → evenly spaced 36 px lanes; small S-jogs collapsed; a decorative numeric badge + adjacent header and any blocking title bar may move by ≤120 px, reported as `node_shift` |
| label repair | L01/L07 label owner → first try up to 100 px along / 36 px across its edge; if no local slot exists, extend to 300 px along the edge, then sample 15–40% of a long bus edge so the label can sit on a clear horizontal arm; accept only if the label then crosses no wire, label, node or arrowhead |
| write | preserve node/edge styles and untouched waypoints; keep the original style string for repaired anchors; repaired value labels become child label cells |
| verify | re-export, match rendered paths, run the scoped DRC again |

Scoped rules: R12/R14 (errors), R01/R06/R13/L01/L07 (warnings/info), R04 as a warning
guard. Disabled and reported as out of scope: P01/P04–P10, L02/L04, R02/R03,
R09/R10/R11. The reason is that these encode deliberate atlas choices (dense
text, tight clearances, jump-arc crossings, draw.io auto-routed short edges) and
cannot be "fixed" without moving modules or rewriting the design.

Safety properties enforced by the code and the tests:

- topology signature (edge id/source/target/label/signal) must be unchanged;
- functional node geometry and ids must be unchanged; the only permitted node
  moves are the reported decorative badge/header shifts by ≤120 px, with their
  widths reduced only enough to stay inside the parent container;
- node/edge styles are unchanged except reported anchor fractions;
- a label move that does not strictly reduce L01+L07 is rolled back;
- a dogleg/endpoint snap that keeps R01/R12, adds R04, or has no free anchor is rolled back;
- a bus-lane pass that increases hard failures or R04 on any changed wire is
  rolled back as a whole, including its decorative node shift;
- a terminal repair that introduces a new R04, keeps R12 or leaves R14 is rolled back, so an unresolved hook fails the run;
- every changed wire/label/node is listed in `input.options.wire_changes`,
  `label_changes` or `node_changes`, with before/after values;
- `input.options.unfixed_doglegs` / `unfixed_bundle` list findings that had no
  safe slot.

On a 585-node, 136-edge atlas this fixed 93 scoped findings (107 → 15, 0 errors):
28 endpoint/Z anchor snaps, 4 terminal hooks, 6 vertical-bus lanes, 2 post-bus
dense lanes, 4 reported decorative node shifts, 28 labels and 14
dense-parallel-wire findings (R13 14 → 0). Topology, functional node geometry, node/edge styles and
signals are unchanged except the reported anchor fractions and four decorative
header/badge shifts (with title widths reduced to stay inside their containers).

```
summary.passed               true only when no error and validation ok
summary.violations_before    defects in the input/authors waypoints
summary.violations_after     defects in the output
summary.fixed                before-keys absent from after
drc.by_rule                  per-rule count/fixed/severity/description
drc.unfixed_violations       actionable list with object ids + locations
metrics.before/after         raw metrics
metrics.score_before/after   normalised breakdown + hard failures
iterations                   the loop log
render                       renderer, PNG liveness, SVG path verification
validation                   round-trip topology/ids/structure
limitations                  what was *not* verified
files                        sizes and sha256 of every artifact
```

`summary.passed` is the automated scoped-check signal, not full engineering
sign-off. A warning-only result can pass these checks; inspect residual findings
for accepted trade-offs (e.g. an unavoidable crossing in a dense block diagram).
Complete the separate semantic and visual acceptance review before claiming
the diagram is fully verified. Skipped and out-of-scope checks remain limits.

## Options that matter

| option | effect |
|---|---|
| `--preset compact\|balanced\|clean-routing` | weight profile |
| `--layout full\|preserve\|hybrid` | whether nodes may move |
| `--direction LR\|RL\|TB\|BT` | flow and port convention |
| `--clearance N` | routing corridor width (default 14) |
| `--min-clearance N` | DRC threshold (default 8) |
| `--iterations N` | fix/score cycles |
| `--no-drawio` | builtin preview only; rendered DRC marked unavailable |

## When to stop tuning

- If the same warnings persist across iterations, they are structural (topology
  or density) or an engine limitation; repetition alone does not diagnose the
  cause. Recheck positions, port constraints and channels. Use permitted local
  spacing changes or derive overview/detail views while retaining the master.
  The spec parser reads `constraints.h_gap/v_gap`, but the current pipeline
  builds a fresh `LayoutConfig` without forwarding those values. Use reviewed
  explicit positions/sizes or programmatic layout when custom gaps are needed.
- A `fallback` route (visible in `route_method` programmatically) means no legal
  path was found. DRC reports it; do not claim it is fixed.
- Do not chase zero crossings on a dense architecture. Zero crossings is a goal,
  not a hard constraint; the report says so.
