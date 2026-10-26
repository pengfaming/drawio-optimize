# Color, illustration and visual style

Use for new diagrams, requested restyling and visual review. These are design
instructions for the agent; the current CLI has no theme switch or automatic
contrast/icon audit. During geometry-only repair, preserve authored styles
except the separate authorized personal style pass below; report other material
legibility issues instead of silently recoloring the diagram.

## Active personal style profile

The entire visual system follows the user's T65 reference, including large,
medium and small modules, header tiers, panels, typography, outlines and native
illustrations. Read [t65-reference-style.md](t65-reference-style.md) before
styling and use the exact profiles in [t65-style-tokens.json](t65-style-tokens.json).
These replace all older partial palette preferences in this skill. This is a
personal default, separate from the generic engineering/change contract; an
explicit profile requested for the current task overrides it.

The main and primary-domain title bars are deep blue; medium-module headers
use the reference's lighter colored bands with dark text. The complete system
has a pale enclosing body, middle modules have pale bodies, data cells have
muted colored fills, and operator-detail/neutral-card roles use their distinct
reference profiles. Preserve meaningful image content and engineering topology.

Do not substitute a generic palette or carry forward a rule that all headers
must be dark. The exact T65 reference controls; the rest of this file provides
contrast, rendering and asset guidance within that style.

## Current engine palette and its limits

The bundled defaults are defined in `scripts/drawio_optimize/util.py`. The
following are engine defaults, not the final personalized appearance. Map all module tiers to the T65 reference profiles in the separate style
pass; do not treat these generic defaults as the requested final appearance:

| Node kind | Fill | Text |
|---|---|---|
| Interface | `#DAE8FC` | `#10243A` |
| Controller | `#D5E8D4` | `#102A12` |
| Memory | `#FFF2CC` | `#3D2C00` |
| Datapath | `#E1D5E7` | `#2B0F38` |
| Clock | `#F8CECC` | `#3B0A08` |
| External | `#F5F5F5` | `#1F1F1F` |
| Container | `#FBFCFE` | `#343A40` |

The current edge defaults are:

| Signal | Stroke | Pattern |
|---|---|---|
| Data | `#1D4ED8` | Solid |
| Control | `#B45309` | Dashed (`6 3`) |
| Clock | `#6D28D9` | Dotted (`2 3`) |
| Bus | `#0F766E` | Solid |
| Power | `#B91C1C` | Short dotted (`1 3`) |
| Default/unspecified | `#334155` | Solid |

These dash patterns already encode signal type. Do not reuse dashes for
optional/disabled status in the same drawing; use an explicit status label or
badge instead. The red clock fill and red power edge do not imply an error.
Reserve red for errors only in a profile where it has not already been assigned
another meaning. Do not infer actual role/type from these colors alone.

Choosing a profile in prose does not configure the engine. The minimal spec
supports `kind` and `signal`, but not arbitrary palette or icon fields. To apply
a different approved theme, use a reviewed edit of the generated `.drawio`
styles, then rerender and validate; do not modify global palette constants for
one diagram. Inspect the final export rather than assuming a fallback preview
renders every style identically to draw.io.

## Contrast and color-independent reading

As internal design targets, aim for text/background contrast of at least 4.5:1
and essential lines/arrows/boundaries against their backgrounds of at least 3:1.
These targets are not a certification claim. Check actual adjacent colors,
including every region crossed by a long edge and the effective background of
transparent labels. Decorative container borders can be subtler when they carry
no essential meaning. Keep tertiary notes legible instead of using very pale text.

When measuring contrast, use linearized sRGB relative luminance:
`ratio = (L_lighter + 0.05) / (L_darker + 0.05)`; use a proper calculator or
implementation, not a ratio of raw hex/RGB values. Do not claim a numeric check
unless it was performed. Inspect final-size exports as well: a numerically
sufficient color still fails if its line or type is too small to see.

Do not rely on red versus green alone. Repeat critical distinctions through
labels, shapes, patterns or status text according to the chosen encoding.
Check a grayscale preview for main-path tracing; if color is essential to a
distinction, provide another cue before declaring the diagram readable in print.
Grayscale inspection alone does not establish color-vision accessibility.

Edge labels should use dark text. On a busy crossing or tinted region, a small
opaque background matching the local surface may improve reading, but it must
not conceal another connection, arrow or junction. Reposition the label or route
when needed rather than masking the conflict with a large white rectangle.

## Line, text and emphasis

Use a restrained scale. At source scale, a useful starting point is 2–2.5 px for
primary paths, 1.25–1.75 px for secondary paths and 0.75–1 px for decorative
container boundaries. Adjust for final display size and the renderer. These
values are design suggestions, not current automatic engine settings.

Keep arrow shapes/sizes consistent within a relation type. Use thicker strokes
for priority only if width is not already encoding another engineering quantity;
otherwise emphasize the main flow through placement and spacing. Keep decorative
boundaries and icons subordinate to functional paths. Do not make tertiary
functional edges so faint that they disappear. For text scale and overview/detail
splitting, follow `engineering-design-principles.md`.

## Icons and illustrations

Add a visual only when it helps identify a module, interface or physical context.
Use a coherent family of simple vector icons: consistent stroke, optical size
and perspective. Native editable Draw.io shapes are preferred for engineering
objects. Avoid mixing emoji, photorealistic images, 3D illustrations and line
icons in a block diagram. A small diagram may need no icons at all.

- Keep the module name as editable text; an icon must not replace a required
  label, pin, state or connection. Repeated icons need consistent meaning.
- Place icons inside a dedicated area of the node/header with padding. Keep
  them away from ports, arrowheads, title bands and routing corridors; verify
  that resizing does not cover the label or change apparent connectivity.
- Use logos only when the named product/vendor is actually relevant. Do not
  invent a logo or imply a vendor/service dependency absent from the source.
- Reserve photos, screenshots and complex illustrations for explanatory insets
  or detail pages where they add evidence. Keep them separate from the editable
  engineering graph and label their role; do not rasterize the graph to add art.
- Use self-contained assets when external images are necessary. Record source
  and usage terms for third-party assets; do not depend on a fragile remote URL.
  Inspect actual exported appearance and disclose which assets remain raster.

## Visual acceptance

For the actual delivered render, compare all module tiers against
[t65-reference-style.md](t65-reference-style.md): pale outer enclosure, deep-blue
main/primary headers, medium-light middle headers, matched pale bodies,
reference data-cell fills, neutral nested cards and white operator-detail cells.
Check exact fill/outline/text tokens and the reference's flat shape language.
Confirm that the backdrop lies behind all content and functional relationships,
positions, memberships and meaningful illustrations remain intact.
Then check that the palette has a stable legend,
primary paths stand out, text/essential edges remain visible on each background,
optional states are explicit, and icons add meaning without competing with the
graph. Inspect final display size and dense detail, plus grayscale when print
or color-independent use matters. Record numerical contrast checks only when
performed. Report style limitations separately from DRC and semantic results.
