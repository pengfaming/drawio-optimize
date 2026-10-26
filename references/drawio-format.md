# Draw.io XML: what actually survives a round-trip

## Document shape

```xml
<mxfile host="...">
  <diagram id="page-id" name="Page-1">
    <mxGraphModel dx="..." page="1" pageWidth="1600" pageHeight="1200">
      <root>
        <mxCell id="0"/>
        <mxCell id="1" parent="0"/>
        <!-- vertices, edges, labels, groups -->
      </root>
    </mxGraphModel>
  </diagram>
</mxfile>
```

A page body may be **compressed**: base64 → raw deflate (`zlib.decompress(data, -15)`)
→ URL-decode → XML. The reader handles plain, compressed and bare
`<mxGraphModel>` documents. Other pages are preserved byte-for-byte; only the
selected page is rewritten.

## Wrapped cells (`UserObject` / `object`)

Draw.io stores a cell with metadata as a wrapper around the inner cell:

```xml
<UserObject id="ifu" label="" tooltip="npc/vsrc/IFU.v" data-source="npc/vsrc/IFU.v">
  <mxCell style="..." vertex="1" parent="npc">
    <mxGeometry x="32" y="239" width="704" height="1518" as="geometry"/>
  </mxCell>
</UserObject>
```

The **id and label live on the wrapper**; style, parent and geometry live on the
inner `<mxCell>`. Edges may reference the wrapper id. A reader that only scans
bare `<mxCell>` elements silently loses these nodes and disconnects their edges.
The parser handles both `<UserObject>` and lowercase `<object>`, and the writer
re-emits the same wrapper with `tooltip`, `data-source`, links and other unknown
attributes intact. Nested children still use the wrapper id as `parent`.

## Geometry and containers

- `mxGeometry x/y` is relative to the parent cell. Children of a `container=1`
  or `swimlane=1` cell are relative to the container's **top-left** (the title
  band is part of the container's rectangle).
- A `group` cell without geometry contributes no offset; its children keep the
  coordinates they had relative to the group's parent.
- The model stores **absolute** rectangles. The writer converts back to relative
  geometry through the parent chain (`child.x - parent.x`).
- A swimlane's title is drawn in the top 24–28 px. The router treats that band
  (plus clearance) as an obstacle even though the container body is not.

## Edges

```xml
<mxCell id="e1" edge="1" parent="1" source="a" target="b"
        style="edgeStyle=none;orthogonal=1;endArrow=block;exitX=1;exitY=0.5;entryX=0;entryY=0.5;">
  <mxGeometry relative="1" as="geometry">
    <Array as="points">
      <mxPoint x="628" y="70"/>
      <mxPoint x="628" y="196.8"/>
    </Array>
  </mxGeometry>
</mxCell>
```

- `exitX/exitY` and `entryX/entryY` are fractions on the node boundary. For a
  side, one coordinate is 0 or 1 and the other is the fraction. Do **not** write
  a corner fraction (0 or 1) — Draw.io clips the line along the border.
- `edgeStyle=none` means "do not invent a route"; the `Array` waypoints are the
  route. `orthogonal=1` keeps the line orthogonal if the user drags a node, and
  is verified to leave already-perpendicular stubs untouched.
- `edgeStyle=orthogonalEdgeStyle` is intentionally **not** used for generated or
  repaired edges: it may add bends and ignore the exact terminal approach.
- The exported SVG stops the line at the base of the arrowhead (~8 px before the
  anchor) and draws the head as a separate `Z` path. Verification appends the
  head tip before comparing with the model. Bidirectional edges have **two**
  `Z` paths; both are kept, and the tip is the triangle apex (the vertex farthest
  from the base midpoint). Path commands are flattened, so `C`/`Q` jump arcs do
  not make an edge disappear from the checker.

## Labels

- Node label: the vertex `value`; `html=1` + `whiteSpace=wrap`; newlines become
  `<br>` after HTML-escaping the user text.
- Edge label: `value` on the edge (centred), or a sibling `vertex=1;
  connectable=0; parent=<edge>` cell with style `edgeLabel;...` and
  `mxGeometry x=[-1,1] relative=1 y=<px up>`.
- Never put raw user text into an HTML label without escaping it first.

## Styles

Styles are `key=value;` strings. The engine keeps the original string for parsed
nodes and edges (only forcing `html=1`, `whiteSpace=wrap`, `edgeStyle=none`,
`orthogonal=1` where necessary), so colours, fonts, icons and unknown keys
survive. Generated styles use the palette in `util.py` and add two private keys:

- `pnrKind=controller` on nodes (used for size consistency and palette),
- `pnrSignal=control` on edges (used to restore the semantic signal on re-read).

Draw.io ignores unknown style keys and preserves them.

## What is preserved, what is not

Preserved verbatim:

- ids, page names, page attributes, unknown style keys;
- `<UserObject>` / `<object>` wrappers and their tooltip/data-source/link data;
- unsupported cells (images, text-only cells, custom shapes) as raw XML, and
  their rectangles are treated as routing obstacles;
- extra edge labels (a second `edgeLabel` cell);
- other pages.

Rewritten:

- geometry of nodes/edges, waypoints, anchors, label offsets;
- styles of parsed edges to force deterministic routing **in full/hybrid mode**;
  `--layout preserve` and `--repair-only` keep the original node/edge style
  strings and only change the geometry that was explicitly repaired;
- linearised page XML (compressed pages are written plain — valid, larger, and
  much easier to diff).

## Common failure modes this format work avoids

| symptom | cause | fix in this skill |
|---|---|---|
| arrow points along the box border | final segment not perpendicular to the entry side | terminal-stub invariant, R12 |
| line "jumps" when the file is opened | `edgeStyle=orthogonalEdgeStyle` re-routes around waypoints | `edgeStyle=none` + explicit waypoints |
| label disappears | label written as a nested child instead of a sibling | sibling label cells, `parent=<edge>` |
| label lands in the wrong place | assumed `x` was pixels or `y` was a fraction | calibrated `x∈[-1,1]`, `y` px up |
| objects lost after a round-trip | unsupported cells skipped | preserved verbatim + reported |
| page content shifted | forgot container-relative coordinates | absolute↔relative conversion with tests |
