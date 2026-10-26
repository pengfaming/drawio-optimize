# Label rules and text measurement

Apply [engineering-design-principles.md](engineering-design-principles.md)
for hierarchy, content density and final display-scale review. Text fitting its
source box does not prove readability after shrinking the whole figure. Prefer
a derived overview/detail split to shrinking labels in a dense master. Inspect
actual rendered labels; font measurements and connector SVG checks are partial
evidence, not visual acceptance.

## Measuring text honestly

The size of a label is measured with real font files, not character counts:

- Latin runs: DejaVu Sans / Liberation Sans (the Helvetica fallback on Linux).
- CJK runs: Noto Sans CJK (or WenQuanYi / PingFang / Microsoft YaHei).
- Per-run measurement, then a 6% safety factor.
- If Pillow or the fonts are unavailable, a per-character-class estimate is used
  and `doctor` / the report shows `text_backend: heuristic`. Do not present that
  as exact.

Draw.io inserts its own label padding; the model reserves 14 px horizontally and
10 px vertically per node, so a label that fits the measured box still fits the
rendered box.

## Where labels live in Draw.io

A node label is centred by Draw.io. An **edge** label is a sibling cell:

```xml
<mxCell id="e1" ... value="" />
<mxCell id="l1" value="scan_en" style="edgeLabel;html=1;..." vertex="1"
        connectable="0" parent="e1">
  <mxGeometry x="0" y="26.4" relative="1" as="geometry"/>
</mxCell>
```

Calibrated against a real headless export:

- `x ∈ [-1, 1]` is the position along the whole edge (`-1` = source, `1` = target).
- `y` is a **pixel** offset perpendicular to the page, positive = up.
- The label is not rotated with the segment; it stays horizontal.

The writer uses the edge's `value` when the label sits at the centre, and a child
cell only when it must be offset. Parsing must read *sibling* label cells — they
are not nested inside the edge element. When a value label is moved by
`--repair-only`, the writer clears the edge value and creates a child cell with
the **edge's** `fontFamily`, `fontSize`, `fontColor`, `labelBackgroundColor` and
`labelWidth`, so the visual style does not change.

Measure edge labels with the edge style's `fontSize` (often 14 in hand-authored
atlases), not the 10 px generated default. Using 10 px underestimates CJK widths
and hides real L01 collisions.

## Placement algorithm

For each labelled edge (longest first):

1. candidate fractions `0.5, 0.42, 0.58, 0.35, 0.65, 0.28, 0.72, 0.2, 0.8`;
2. candidate offsets `0, ±(h/2+4), ±(h+10), ±(1.7h+8)` px;
3. score = 1000 × collisions + 20 × |t−0.5| + 0.6 × |offset|;
4. take the best; if the winner is the exact centre, write it as the edge value.

In `--repair-only`, a label with no collision-free local slot may search up to
300 px **along its own edge**; if the edge is a long bus, it may also sample
15–40% of the edge length so the label can follow the wire onto a clear
horizontal arm. It is never allowed outside the page or more than 36 px across
the edge. Moving a value label converts it to a child cell but keeps the edge's
font size, colour and background.

Collisions include node boxes (except a short endpoint when the edge is <80 px),
already placed labels, other edges' segments, and its own arrowhead zone.

## Rule table

| ID | Severity | Detects | Auto-fix | Notes |
|---|---|---|---|---|
| L01 | error | a connector crosses any text box | re-route / move label | own edge label exempt |
| L02 | error | two label boxes overlap | move label | node and edge labels together |
| L03 | warning | edge label >40 px from its own edge | re-centre | |
| L04 | error | text wider/taller than the node's inner box | wrap/grow | locked nodes are reported, not grown |
| L05 | info | same-signal labels at inconsistent fractions | normalise | stdev > 0.18 |
| L06 | warning | label overlaps its own arrowhead zone | shift label | |
| L07 | warning | edge label sits on an unrelated module | move label | |

## Limits

- Browser font fallback may differ by a few pixels from the measured font,
  especially for emoji, rare CJK glyphs and mixed-script labels.
- Draw.io's HTML labels can wrap differently from the greedy wrapper here when a
  word is wider than the node. The node is grown to the measured wrapped width,
  which makes this rare but not impossible.
- The SVG export renders labels as `foreignObject`; rendered-path DRC therefore
  checks connectors only and uses measured metrics for text geometry. This is
  recorded in `render.svg_check.limitations`.
