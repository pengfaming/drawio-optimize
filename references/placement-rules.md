# Placement rules and the layered layout

## Layout pipeline

1. **Sizing** — label text is measured with real fonts; width/height come from the
   wrapped text plus padding. A node also grows to provide port pitch. Nodes of
   one `kind` are made uniform (except locked nodes). This is an engine default,
   not a requirement to equalize simple and complex modules: review content
   density and use explicit sizing/local edits when the default hurts reading.
2. **Container recursion** — children are laid out first, in a local frame; the
   container size is the child bbox + title band + padding. The container then
   participates as a node at the parent level.
3. **Rank assignment** — longest path on a cycle-broken DAG. Cycles are broken by
   DFS back-edges; the back-edges are still routed, just not used for ranking.
4. **Rank projection** — at every scope, an edge whose endpoints live in different
   containers is projected onto the containers themselves (`A_child → B_child`
   becomes `A → B`). Without this, groups full of connected modules are laid out
   as isolated islands and end up stacked.
5. **Long edges** — dummy nodes are inserted at intermediate ranks for ordering
   and crossing counting; they are removed before coordinates are assigned.
6. **Ordering** — barycenter sweeps (down/up, 4 rounds) followed by adjacent-swap
   transpose, with exact inversion counting between adjacent ranks.
7. **Coordinates** — rank bands accumulate the largest node thickness + gap;
   within a rank, desired cross coordinates are the mean of neighbour centres,
   then packed forward/backward with a minimum gap. Position is re-estimated
   after every pack (otherwise branches use stale coordinates).
8. **Chain straightening** — a node with one predecessor and one successor is set
   on their centre line; this is what makes a chain edge perfectly straight.
9. **Normalisation** — content is shifted to the margin and rounded to 1 px.
   RL/BT are produced by mirroring the finished LR/TB layout.

## Rule table

| ID | Severity | Detects | Auto-fix | Notes |
|---|---|---|---|---|
| P01 | error | two unrelated modules overlap | layout / nudge | a child inside its container is legal |
| P02 | warning | centres aligned but anchors/route jog | port snapping | compares node coordinates, anchors and the path |
| P03 | warning | >40% of edges run backwards | none | individual back-edges are legal |
| P04 | warning | gap below `min_node_gap` (14 px) | layout / nudge | only pairs whose projections overlap |
| P05 | info | same-rank nodes not aligned on the rank axis | layout | ranks are scoped per container |
| P06 | warning | port pitch below 13 px | grow/rebalance | reports ports per side |
| P07 | info | same-kind sizes differ >25% | layout | locked nodes excluded |
| P08 | warning | child outside its container, internal edge leaves it | layout | membership is never changed |
| P09 | info | container with ≤1 child or no internal edges | none | grouping is a semantic decision |
| P10 | error | a node moved in `preserve` mode | rollback | nudges explicitly allowed up to 24 px |

## Modes

- **full** (default for specs): every unlocked node is placed.
- **preserve** (default for `.drawio` input): geometry is untouched; only ports,
  routes and labels are recomputed. P01/P04/P05/P08 are reported, not forced.
- **hybrid**: keeps each node's current position except for small local nudges
  (overlaps, spacing); useful for "repair my drawing but do not rearrange it".

Locked nodes always keep their coordinates. The layout records `original` geometry
on load, so displacement can be measured and P10 enforced.

## Ports

Draw.io has no first-class port objects; each edge endpoint is an `exitX/exitY`
or `entryX/entryY` fraction. The allocator treats anchors as a per-node resource:

- side from the declared flow (`LR`: R out / L in; `TB`: B out / T in; mirrored
  for RL/BT), flipped for backward edges;
- a strongly off-axis neighbour overrides to T/B (or L/R in TB): a vertically
  stacked pair should connect vertically even in an LR diagram;
- anchors on one side are ordered by the neighbour's cross coordinate (reversed
  for L/T) to avoid braiding;
- **greedy straight snapping**: the most aligned pair takes the shared coordinate
  first; lower-priority pairs are moved aside by one pitch. Earlier candidates
  may displace later ones, so a perfectly aligned pair is never blocked by a
  merely nearby clock line;
- anchors are clamped to 8 px away from corners. A port at exactly `frac=0` or `1`
  makes Draw.io slide the arrow along the border.

## Containers and routing

Containers are visual groups, not separate routing planes: an edge may cross a
container body (reported as `info`), but its 24 px title band + clearance is a
hard obstacle. Edges internal to a container are allowed to leave it; that is
reported by P08 as a semantic warning, not silently rewired.

These are geometric defaults. If the supplied design assigns real boundary
semantics to a container, preserve those constraints and report what the router
cannot enforce. Never infer a logical connection from a crossing. For placement
versus port/routing decisions and overview/detail scope, follow
[engineering-design-principles.md](engineering-design-principles.md).
