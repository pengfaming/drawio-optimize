# Change contract and acceptance gates

Read before generating, modifying or accepting a diagram. This contract governs
agent decisions; it does not describe new implemented CLI options or DRC rules.
MUST / MUST NOT are acceptance requirements. A skipped required check is not a
pass. Apply checks to the task's affected scope; do not turn a local edit into
an unrelated global redesign.

## 1. Establish authority before editing

Classify each relevant property before changing it:

| Class | Evidence | Required treatment |
|---|---|---|
| Engineering constraint | User instruction, interface specification, source-supported relation, explicitly locked object | Preserve unless the requested engineering change explicitly replaces it |
| Presentation constraint | Requested scope, named style profile, display target, explicit placement/channel restriction | Enforce within scope; it cannot override engineering correctness |
| Derived representation | Geometric anchor, bend coordinates, label placement, automatically chosen size/layout | Recompute or retain according to current constraints and acceptance gates |
| Unresolved | Available evidence cannot distinguish semantic meaning from presentation | Preserve the uncertain property; continue safe independent work and report what evidence is missing |

For each constraint that decides a repair, record the affected ids/property,
allowed values or bounds, evidence source and whether it is confirmed or
unresolved. A short note is sufficient for a small repair. Do not infer a fixed
interface from its coordinates, a fixed channel from existing waypoints, or a
shared electrical net from overlapping lines. `entryX/entryY`, `exitX/exitY`,
`noEdgeStyle`, routing style, color, and the word “bus” alone establish none of
those engineering constraints. Conversely, absence of a port name does not
prove an anchor is freely movable. Honor explicit locks.

When evidence conflicts, explicit instructions for the current task override
profile defaults. A style preference cannot authorize an engineering change.
An unresolved conflict affecting correctness blocks that dependent edit, not
all independent work. Never choose the interpretation that merely makes a
shorter route possible.

## 2. Fix the scope and baseline

Before mutation, retain the source and record the properties needed for the
input/output comparison: pages, functional ids, endpoints, relation labels,
directions, status, containment, protected geometry and user metadata. Record
pre-existing defects separately from regressions. Save edits to a separate
candidate until the gates pass; replace the requested target only after review.

Declare which property classes may change: styles, labels, intermediate routes,
free anchor positions/sides, local placement, or global layout. Do not infer
permission to move nodes from permission to repair a wire. Existing pipeline
modes are narrower implementation capabilities, not substitutes for this scope.
If the permitted mode cannot perform a required repair, use a reviewed scoped
edit or report the limitation; never silently enlarge the scope.

## 3. Invalidate dependent results

A previous successful check becomes stale when one of its inputs changes.
“Recheck” means compute or inspect again; it does not require changing a valid
object.

| Changed input | Minimum dependent checks |
|---|---|
| Node position, size, shape or orientation | Incident endpoints, terminal normals, complete paths, attached labels; nearby obstacle/clearance/crossing interactions |
| Anchor/port assignment or route | Complete owning path, arrow direction, labels, all objects intersecting its old/new routing neighborhood |
| Text, font, wrapping or title size | Actual text bounds, overflow, local hierarchy, container/title clearance, affected routing space |
| Container bounds or membership | Containment semantics, internal/cross-boundary links, title bands, enclosure coverage |
| Fill/text/line style or stroke width | Contrast, encoding, legend consistency, effective visibility; stroke/arrow clipping and clearances when extents change |
| Page scale or delivery dimensions | Effective text size, arrow visibility, lane separation and overview navigation |

Follow dependencies until no newly affected object remains unchecked. Preserve
unaffected objects. A moved route must be checked against unchanged neighbors;
checking only the changed edge's coordinates is insufficient. Pure color edits
do not require rerouting when geometry and extents remain unchanged.

## 4. Diagnose and compare legal candidates

For each finding, or coherent batch with the same cause, record: affected ids,
observed defect, cause supported by evidence, applicable constraints and allowed
repair scope. A waypoint count is not a bend count; inspect the resolved path.
If the cause is uncertain, identify the hypothesis and the check that tests it.

For a detour or redundant bend, compare the baseline with the simplest feasible
route under the current anchors. If that does not resolve the issue, evaluate
legal anchor alignment/side choices when evidence permits, then layout/channel
changes only within the declared scope. For an obstacle-avoidance failure,
evaluate a clear alternative channel. A candidate ruled out without rendering
must have a concrete reason, such as a fixed pin, blocked segment or scope bound.
Do not treat the current side as mandatory solely because it is serialized.

Reject an illegal candidate before scoring it. Among legal candidates, use the
priority order in `SKILL.md`; record bends, length, crossings/overlap, clearance,
traceability and edit impact where relevant. If a candidate is no worse on all
applicable measures and strictly better on at least one, with no higher-priority
regression, do not retain the dominated candidate. An unobstructed, aligned
single segment satisfying terminal and semantic constraints must replace a
more complex route when it has no traceability or neighborhood regression.

When measures conflict, state the concrete tradeoff and evidence supporting
the higher-priority choice. A weighted score alone is insufficient. Establish
tolerances in consistent units before comparing results; do not adjust them
after a candidate fails. Do not claim a global optimum from a local search.

## 5. Choose representation by behavior

Automatic, manual and hybrid routing are permitted. None is universally
required or exempt from validation. If preserving a corridor, record its actual
purpose, extent/order/clearance constraints and evidence. Preserve that
constraint while recalculating obsolete turns and endpoint approaches as needed;
do not preserve stale coordinates merely because the edge is a bus.

When serialization disables routing or fixes anchors/waypoints, inspect the
resolved result against the current diagram. Do not fix detours by blindly
clearing all waypoints, removing all anchors, or enabling automatic routing for
every edge. Do not use jump arcs, hidden edges or merged nets to disguise an
unresolved connection problem. A diagnosed stale constraint must be replaced
within scope or explicitly reported unresolved; “manual path retained” alone
is not a diagnosis.

## 6. Accept transactionally or roll back

Accept an edit/batch only when every applicable gate below passes:

1. **Preservation:** input/output comparison confirms all protected semantics,
   ids, connectivity, membership, metadata and geometry. Differences must be
   authorized and enumerated; a round-trip against the output itself is not
   preservation evidence.
2. **Repair:** the targeted defect is eliminated or demonstrably reduced by
   the stated acceptance measure. Do not count a flag/style change as a fix
   without checking the resulting path or image.
3. **Non-regression:** no new correctness violation or higher-priority defect
   exists in the affected dependency closure. Compare findings by stable rule
   and object identities plus measured severity, not aggregate counts alone.
   An old violation moving to another edge is a new affected-object finding.
4. **Rendering:** actual draw.io output matches intended endpoints, terminal
   directions, paths, labels and enclosure coverage. Inspect the final-size view
   and relevant enlarged regions. A parser or a builtin preview cannot establish
   independent visual acceptance.
5. **Persistence:** save/reload preserves connectivity and intended geometry;
   re-export checks renderer stability. For an automated transformation, a second
   application with unchanged inputs/settings must not introduce cumulative
   shifts, style drift or duplicate objects. Compare normalized relevant
   properties, not timestamps or raw file bytes.

If a gate fails, revert the responsible candidate/batch and try a legal
alternative within scope. If one cannot pass, retain the safe baseline and
report the unresolved defect and limiting constraint. Do not downgrade rules,
hide objects, widen tolerances, or trade one correctness defect for another to
obtain a passing summary. A deliberately incomplete candidate may be supplied
for review only when clearly identified; it must not replace the accepted
target or be presented as a verified fix.

## 7. Bound the conclusion by the evidence

Use a concise acceptance record beside complex outputs (the existing CLI report
does not populate these fields automatically):

| Record | Required contents |
|---|---|
| Baseline and scope | Source, permitted properties, affected ids/dependency checks |
| Decision | Constraint evidence, cause, candidates, rejection/tradeoff reasons |
| Result | Before/after measures, semantic/property comparison, remaining defects |
| Validation | Renderer, inspected scales/regions, actual checks, failures/skips |
| Disposition | Accepted for stated scope, limited/unverified candidate, or reverted |

Full scoped acceptance requires all applicable gates with evidence. If an
essential check cannot run or a correctness-dependent meaning is unresolved,
mark acceptance incomplete and name the missing evidence. Do not claim that
all geometry is verified when the chosen DRC mode excludes relevant rules.
Pre-existing out-of-scope findings may remain, but must not be described as
fixed. Global engineering acceptance additionally requires the engineering
review and display-scale checks in
[engineering-design-principles.md](engineering-design-principles.md#acceptance-and-evidence).

## Boundary examples

| Situation | Required decision |
|---|---|
| Aligned modules, free ports, clear path, stale side creates bends | Compare legal facing anchors; choose the straight path if non-regressing |
| Same geometry, but source material fixes a physical side/pin | Keep the pin; route legally around it rather than substituting a nearer side |
| An object blocks the direct path | Reject that path; compare clear channels, not just bend totals |
| Existing waypoints with no documented corridor meaning | Treat them as historical geometry; do not invent a fixed-bus exemption |
| Port meaning remains unknown | Preserve it, report the limitation; repair independent parts safely |
| Repaired edge clears a box but creates a neighboring collision | Reject or revise the candidate; lower total warning count cannot excuse it |
| Pure recoloring leaves endpoints and extents unchanged | Recheck style/contrast; retain valid routes |
| Model DRC passes, but renderer/visual inspection was unavailable | Report model-only results and incomplete visual acceptance |
