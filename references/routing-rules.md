# Routing rules and the orthogonal router

The [change contract](change-contract.md) governs whether a route may change
and whether its result can be accepted. The numeric rules below describe the
current orthogonal engine; they do not establish semantic port/channel meaning
or make historical waypoints mandatory. In particular, the current minimal
repair mode keeps sides fixed and does not implement general side reassignment.
If that limitation preserves a diagnosed detour, report it or use a reviewed
edit within scope; do not claim the detour is resolved by a passing scoped DRC.

Use [engineering-design-principles.md](engineering-design-principles.md) to
diagnose the cause before repairing geometry. For an aligned pair with jogs,
inspect free versus fixed/named ports before deleting waypoints. For a long
detour, compare positions, port directions and channels before adding bends.
Only move anchors whose interface meaning permits it; a fixed side does not
prove every anchor on that side is interchangeable. Prefer the simplest legal,
traceable orthogonal path without changing topology, direction or port meaning.

## The terminal-stub invariant

Every connector is built as:

```
 p0 ──stub── p1 ────────── ... ────────── q1 ──stub── q0
(source port)                                (target port)
```

- `p0`/`q0` are on the node boundary; the stub is perpendicular to that side.
- The first segment leaves along the source normal; the last segment enters
  along the **negative target normal**.
- Draw.io clips the polyline at the node border, so a sideways approach produces
  a line that slides along the border and an arrow pointing along it. That is the
  single most important defect this skill exists to prevent.

`R12` checks the final direction; the router rejects any candidate whose last
segment is not inward.

### Terminal overshoot in an existing file

A common hand-authored defect is a final waypoint that overshoots the anchor and
backtracks, e.g. `… (4460,5140) (4180,5140) (4180,5110) → anchor (4180,5147)`.
Collinear simplification leaves only 7 px between the last bend and the anchor,
so the arrowhead has no room (R12) even though the wire looks long. Minimal
repair removes the backtrack, keeps the same channel and sets an **18 px**
perpendicular stub. It is accepted only if R12 disappears and no new R04
clearance warning appears. A full re-route is never used for this case.

`--repair-only` does not re-run the router on untouched edges. Their anchors,
waypoints, styles and jump arcs remain exactly as authored.

### Terminal dogleg

A second, subtler case is a short sideways step immediately **before** a valid
final stub:

```
… trunk at x=830 ──30px── x=800 ──18px── anchor
```

R12 sees a perpendicular 18 px final segment and passes. The dogleg is caused by
a 30 px mismatch between the incoming trunk and the target anchor. Repair snaps
the target `entryX/entryY` onto the trunk (≤40 px), removes the short step, and
checks that no other edge uses that anchor within 13 px. It is accepted only if
R01/R12 disappear and no new R04 appears. If the target side is crowded, the
dogleg remains and is listed in `unfixed_doglegs`. This is one of the anchor
moves that `--repair-only` performs; source-staircase/Z snaps use the same
safety gate, and the report records every before/after fraction.

### Terminal hook (outward bump before the arrow)

A related but distinct defect is the little U-bump at the end of a wire. The
incoming trunk is too close to the target to leave an 18 px arrow stub, so the
authored path first goes **outward**, steps, and comes back:

```
… trunk at x=4120 ──20px── x=4140 ──10px── x=4140, y-10 ──27px── anchor
```

R12 passes because the final segment is perpendicular and long enough. R01 used
to report only a short dogleg, which let the visible bump survive as a warning.
It is now **R14, a hard error**. Repair moves the whole last trunk to the side's
clean arrow lane (`edge ± 18 px`), collapses sub-4 px crumbs, and re-checks
R14/R01/R12/R13/R04. If a lateral offset still produces a short terminal dogleg
(for example `wire-131`), the target anchor snaps on its free side (30 px in that
case). A hook that cannot pass the gate remains unfixed and therefore **fails the
run**; it is not silently downgraded.

## Fast path, then grid search

1. **Direct**: same axis, facing anchors, no obstacle → 0 bends.
2. **Single bend**: one L-shaped corner, both terminal directions respected.
3. **A\*** on a Hanan grid: candidate lines are obstacle boundaries (inflated by
   clearance), endpoint coordinates, midlines, node centres and outside lanes.
   Turn cost 46, crossing cost 190, overlap cost 420 (per 100 px). The goal may be
   reached from any direction except *into* the target (`nt`), which would
   overshoot and reverse.
4. **Micro-jog cleanup**: replace avoidable dog-legs with a single bend when the
   shortcut is obstacle-free and does not add a crossing. Terminal stubs are never
   rewritten.
5. **Rip-up and reroute**: edges with crossings are removed and rerouted against
   the remaining board; accepted only when the combined
   `crossings*1000 + bends*30 + length*0.05` decreases.

Containers are **not** obstacles, but each container's title band (24 px +
clearance) is: an edge crossing a swimlane title makes the group name unreadable.

## Rule table

| ID | Severity | Detects | Auto-fix | Notes |
|---|---|---|---|---|
| R01 | warning | more bends than the straight/L lower bound; terminal jog; short dogleg just before the arrow (hooks are R14) | yes (dogleg may snap the target anchor) | otherwise compares the same anchors; a dogleg snap is at most 40 px and reported |
| R02 | error | segment through an unrelated module | yes | source/target, ancestors/descendants and containers exempt |
| R03 | warning | crossing between two edges | yes (best effort) | zero crossings is not required; a crossing is a warning, not a hard failure |
| R04 | warning | clearance below `min_clearance` (default 8 px) | yes | reroutes with the violated distance + 6 |
| R05 | error | 180° reversal | yes | direction quantised to 1 px so sub-pixel noise is not a reversal |
| R06 | warning | staircase with ≥3 short segments / ≥4 bends | yes | prefers one long run per direction |
| R07 | error | segment with both Δx and Δy | yes | orthogonal mode only |
| R08 | warning | collinear overlap of two different nets | yes | buses can be exempted with `allow_bus_share` |
| R09 | error | path detached from the node, or no path | conditional | never guesses a missing topology |
| R10 | warning | side contradicts the flow convention | yes | vertical pairs may use T/B in an LR diagram; locked sides exempt |
| R11 | info | length far above Manhattan lower bound | yes | upgraded to warning at >2.5× |
| R12 | error | sideways approach, <10 px for the arrow, arrow crossed | yes | rendered path checked too |
| R13 | warning | two edges run parallel closer than 24 px over ≥40 px | yes (bus lanes) | measured on the routed path; ignores same-edge pairs and collinear overlaps (R08) |
| R14 | error | outward U-bump immediately before an arrow | yes (last trunk / anchor) | moved off R01 because an unresolved hook must fail the run; bump 4–60 px, step 4–40 px |

### Dense parallel wires (R13)

R13 complements R06/R08. R08 catches two nets sharing the **same** line; R13
catches two nets running **side by side** with too little visual separation,
for example a 10 px pitch over 80 px. Minimal repair groups long vertical runs
in one channel and assigns them lanes at 36 px pitch, moving only intermediate
waypoints. If a decorative numeric badge narrows the channel, the badge and its
adjacent header move together and the move is reported; functional nodes never
move.

## Clearance and stubs

- Default clearance 14 px; minimum 8 px. Stubs are 18 px and shrink only when a
  neighbouring shape blocks them, never below 14 px.
- If a stub cannot be made legal, the candidate is rejected; the caller falls
  back to a marked `fallback` path and DRC reports it. Fallbacks are visible in
  `route_method` (programmatic) and as violations.

## Rendered-path verification

When the draw.io CLI is available, `render` exports SVG, `svgcheck` walks the
`data-cell-id` groups and recovers the crop translation from node rectangles.
The line path is read, the arrow tip is appended, and:

- every segment must be rectilinear (`diagonal_segments == 0`);
- the start/end must be within ~4 px of the model anchors;
- the bend count must match the model.

The same rendered polylines then feed a second DRC pass (`R02`, `R04`, `R07`,
`R12`). If this cannot run, the report sets `render.svg_check.verified=false` and
states the limitation; the model-only DRC still runs.
