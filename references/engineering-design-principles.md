# Engineering diagram design principles

Use this guide for generation, repair and review. For a local repair, apply the
relevant checks to the changed edges and their neighborhood; preserve the rest.
These are agent decision and acceptance rules, not additional implemented DRC
rules. The existing scripts provide geometric evidence, not semantic authority.
The mandatory [change contract](change-contract.md) defines constraint evidence,
scope, dependency invalidation, candidate rejection and acceptance gates. Apply
it when turning the design principles below into actual edits.

## 1. Semantic inventory before geometry

Capture from the request, source artifacts and authored diagram:

| Item | What must be understood or preserved |
|---|---|
| Nodes | Actual role, stable id, parent/container and protected geometry |
| Edges | Source/target, data/control/dependency/call meaning, direction, label |
| Ports | Named pins, fixed sides/positions, freely movable anchors |
| Status | Implemented/enabled, optional/disabled, or illustrative |
| Scope | System boundary, external interfaces, overview versus detail |
| Constraints | Locked objects, unchanged styles/pages, allowed local moves |

Distinguish evidence from inference. A label or geometric position alone does
not establish a module's role, signal type, execution order or port meaning.
Inspect supporting material when available. Leave unresolved meanings as
human-confirmation items; continue independent geometric work where safe.
Do not let a default parser guess silently become a confirmed engineering fact.

Preserve node/edge ids, endpoints, relation meaning, grouping, arrows, status and
user metadata. Do not reverse an edge to agree with a left-to-right layout.
Feedback edges, bidirectional interfaces and undirected relationships may be
intentional. Explain arrow conventions in a legend when multiple relation types
coexist; do not mix dependency and dataflow arrows without distinguishing them.

A crossing is not a junction unless the source explicitly represents one. Jump
arcs are visual aids. Traversing a visual container boundary is legal; entering
an unrelated functional module is a different defect. Check whether a container
encodes a real engineering boundary before treating it as merely decorative.
An internal edge leaving its container is a review item, not permission to
change membership or connectivity.

## 2. Overview first, details on demand

Present system boundaries, core modules, primary data paths, key control and
external interfaces first. Put submodules, secondary connections and local
processes at the next level. Put registers, bit fields, implementation notes,
debug information and secondary configuration in detailed views.

When the overview is too dense at its intended display size, derive an overview
plus detail pages/files. Do not solve density solely by expanding the canvas,
shrinking text or exporting more pixels. Preserve a complete engineering master.
On an existing diagram, retain its pages and authored structure; add derived
views only when consistent with the requested scope.

For every abstracted module/path or paired connector, retain an unambiguous
mapping to the master node/edge ids and the detail page/file. A compact mapping
table in the delivery note is sufficient; it need not clutter the drawing.
Identify aggregated paths as aggregated rather than implying they are a single
physical net. An overview may defer details through explicit links/references;
it must not make critical interfaces disappear. Interface proxy nodes and paired
connection labels belong to derived views, not geometry-only master repairs.

## 3. Couple placement, ports and routing

For new layouts: topology → main flow → groups/layers → placement → ports →
orthogonal routes → conflict checks → local iteration. Revisit upstream choices
when downstream routing exposes a problem. Route every feedback/secondary edge
even if it was excluded from rank assignment.

For an existing diagram, compare these options in order of permitted impact:

| Symptom | Diagnose first | Prefer when valid |
|---|---|---|
| Aligned nodes with a short jog | Anchor alignment, fixed pin/side, obstacles | Snap a free anchor, then simplify |
| Long peripheral detour | Relative placement, port direction, direct channel | Local route, then permitted local placement change |
| Many parallel crossings | Port ordering and neighboring module order | Reorder free anchors before adding bends |
| Crowded labels or lane bundle | Local space, channel width, title/arrow zones | Local offsets/lanes; report when no safe slot exists |
| Widespread congestion | Overview density and grouping | Derived detail views or scoped layout change |

If nodes cannot move, report the placement limitation rather than forcing a
route that changes port meaning. Containers must not become arbitrary routing
obstacles; their title bands remain obstacles for readability. Semantic boundary
constraints, when supplied, take precedence over this geometric default.

## 4. Simplest legal path and traceable connections

Use a single straight segment when endpoints share an axis, the channel is clear
and both endpoint constraints permit it. Otherwise choose the fewest useful
orthogonal bends. Avoid 180-degree reversals, alternating short turns,
unnecessary perimeter routes, long overlap between distinct nets and avoidable
crossings; reduce total length subject to higher-priority constraints.

Check port assignment before deleting waypoints. Same-axis node centers do not
prove that the actual ports align. Named/fixed ports and terminal directions
take precedence over zero bends; never replace a dogleg with a diagonal in an
orthogonal diagram. Verify the whole new path, including endpoint normals,
obstacle clearance, neighboring labels and the arrowhead stub.

Favor clear direction, identifiable anchors, visible arrowheads, adequate lane
spacing and labels close to the wire they describe. A slightly longer path may
be preferable when it makes endpoint tracing substantially easier. Do not merge
distinct nets just because they share a channel. Bus-sharing exemptions require
documented shared-bus meaning, not merely parallel wires or a `bus` label.

Use consistent expressions for cross-region links. Very long links may use
interface proxies, paired connection identifiers or separate detail views when
the scope allows derived representations. Preserve direction and exact
source/target correspondence; do not hide a critical link to lower complexity.

## 5. Balance density and content-aware sizing

Keep similar modules stylistically consistent without requiring identical area.
Allow a complex module room for content and ports; do not inflate every simple
peer to match its largest sibling. Whitespace should provide grouping, readable
spacing, breathing room or a routing channel. Avoid both cramped hotspots and
unexplained empty expanses.

The engine defaults to uniform sizes per kind and default text metrics; these
are heuristics, not evidence that density is balanced. If those defaults hurt
readability, use supported explicit sizes/positions or a reviewed local edit,
then rerender and check geometry. Do not claim that parsed spec options are
honored by the CLI without checking their propagation. In `preserve`/repair
scope, report sizing problems that require changes beyond the request.

## 6. Orthogonal visual encoding and priority

For concrete palettes, contrast targets, icon rules and template compatibility,
read [visual-style.md](visual-style.md) when generating, restyling or reviewing
the visual design. Geometry-only repairs retain the authored style.
Keep the generic engineering contract independent of the active visual profile.
For this user's default, read [t65-reference-style.md](t65-reference-style.md) for
large/medium/small modules and illustration rules, not just leaf colors. Apply
and report the scoped visual pass during repair as authorized. That profile
requires a pale enclosure around the whole titled system; a title strip alone
is insufficient. Preserve the target's meaning when mapping visual roles.

Give each visual attribute one stable meaning. Preserve an existing valid
convention; for a new design, choose a small encoding and keep a legend where
needed. One possible assignment is:

| Attribute | Meaning |
|---|---|
| Region background | Domain or subsystem |
| Edge color | Data, control, clock or other relation type |
| Line pattern | Enabled/current, optional or illustrative status |
| Line width | Visual importance |
| Arrowhead | Direction under the stated relation convention |
| Node border/badge/text | Module status |

This assignment is an example, not a mandated palette. If a template already
uses dash patterns for signal type, do not also use those same patterns for
status with a conflicting meaning. Document or consistently revise the encoding
within scope. Retain text/pattern cues so color alone is not needed to trace a
critical relationship. Same-role nodes and same-type/status edges should agree.

Establish at least three visual levels in a nontrivial diagram:

- **Primary:** core modules and principal paths.
- **Secondary:** support modules and secondary relations.
- **Tertiary:** implementation details and supplementary notes.

Use text size, line width, contrast, spacing and position together. Subtle
container borders, titles and decoration should support the main paths. Do not
make optional/illustrative features look like active implementation or let
decoration dominate the core. Avoid adding empty tiers to a trivial diagram.

## 7. Check readability at the final display scale

Establish screen reading, document embedding, PPT presentation or large-format
engineering inspection. Use the requested target dimensions; if absent, record
a reasonable working assumption rather than silently claiming universal
readability. A large atlas may be intended for zoomed inspection, but its
navigation and overview still need to be usable at a fit-to-view scale.

Estimate effective text size from the final physical/rendered placement, not
the XML font size alone: `effective size = source size × displayed scale` in
consistent units. For example, a 1600 px-wide diagram shown at 800 px halves a
20 px label to 10 px. The same shrink applies to lane gaps and arrowheads.
Use the target artifact for a real check; do not compare px and pt without
accounting for the rendering/physical conversion.

Inspect both the final-size overview and enlarged dense regions: can the reader
read principal labels, distinguish arrows and follow connections? If not,
simplify the derived overview, split details or revise visual hierarchy before
increasing export resolution. More pixels do not fix small displayed text.

## 8. Diagnose before automatic repair

Follow **Detect → Diagnose → Plan → Fix → Render → Validate** for each repair
or coherent batch. Record the finding, likely cause, constraints, smallest fix
and verification evidence. Existing wire/label/node change logs provide the
geometry audit trail; add rationale in the delivery note when material.

For redundant bends, inspect endpoints → port constraints → obstacles → direct
path feasibility → minimal permitted edit. Render, check terminal approach and
neighbors, and compare topology. For long detours, compare local rerouting with
permitted local relocation after inspecting positions, ports and channels.

Evaluate changes by the priority order in `SKILL.md`. Restore a change that
damages meaning, protected objects or higher-priority readability even when the
weighted geometric score improves. The score is a heuristic, not a semantic
verifier or a strict lexicographic implementation of those priorities. Do not
relax hard constraints or downgrade findings merely to obtain a passing report.

Local repair is the default for existing authored diagrams. If the same issue
cannot be fixed within the allowed scope, report it and its cause. Consider a
broader layout only when it fits the request; do not keep adding waypoints to
compensate for a layout problem or silently rearrange the engineering master.

## Acceptance and evidence

Use the checklist below in the final review. Record **pass**, **issue** or
**not checked** with evidence; use **needs human confirmation** for semantics
the supplied material cannot establish. For small repairs, a concise delivery
response is enough. For a complex diagram, place this review beside the artifacts
in a separate note rather than altering the script report schema.
Apply the [acceptance gates](change-contract.md#6-accept-transactionally-or-roll-back)
first: a skipped essential check or unresolved correctness constraint prevents
full scoped acceptance. Do not replace an accepted target with an unverified
candidate while describing the change as complete.

| Check | Evidence / practical limit |
|---|---|
| File opens | Actual draw.io open/export when available; XML parsing alone is insufficient |
| Connections complete | Compare input/output ids, endpoints and relation labels; inspect preserved pages/objects |
| Semantic consistency | Source-supported roles, containment, arrow meaning, interface constraints and status agree |
| Primary structure clear | Final-size overview shows boundary, core flow, groups and interfaces |
| Paths traceable | Rendered arrows/anchors, lanes, labels and cross-region pairs identify both ends |
| Geometry sound | Scoped DRC plus image review for penetration, overlap, congestion and redundant bends |
| Labels clear | Rendered text has no occlusion/overflow and is readable at the intended size |
| Encoding consistent | Same types/statuses use consistent visual rules; decoration stays subordinate |
| Round-trip stable | Reparse/save/reopen or re-export and compare geometry/connectivity; specify what was done |
| Editable | Real nodes/edges remain; when available, open and select/edit a representative object in a scratch copy |
| Master retained | Derived views map back to complete source ids; no detail or critical relationship lost |

Read the actual validation checks and render fields; an overall `ok`/`passed`
does not prove that every optional check ran. A successful export or round-trip
does not by itself prove interactive editing was tested. Rendered-path DRC
checks connectors; HTML labels still require image inspection.

Separate automated geometry/structure results, visual review and semantic
confirmation. State the renderer used, inspected scales and skipped checks.
If draw.io is unavailable, identify builtin previews and skipped independent
rendered-path checks. If only geometry checks ran, explicitly say visual
acceptance is incomplete. Unresolved critical semantics prevent a claim of
full engineering acceptance even if DRC passes.

Final standard: the reader can understand the main structure and relationships
without repeatedly hunting endpoints, guessing arrow meanings or relying on
notes that correct a misleading visual structure.
