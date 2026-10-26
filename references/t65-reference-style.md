# T65 reference: complete engineering-diagram style

Authoritative source: the reference atlas `T65_架构图_参考图复刻.drawio` (kept
locally, not bundled in this repository) and
its actual PNG render. This profile supersedes the earlier all-pale, all-dark
header, and leaf-only matching rules. Reproduce the reference's complete
visual hierarchy, not a mixture of those earlier interpretations.

The extracted, source-verified style tokens and sample cell ids are in
[t65-style-tokens.json](t65-style-tokens.json). Use its exact colors rather than
inventing lighter/darker variants. The JSON records the source hash; refresh
the extraction when the user requests a match to a changed reference. If that
local file is unavailable, the recorded profiles remain usable.

## Map structural roles before applying styles

| Role | Reference profile | Treatment |
|---|---|---|
| Entire diagram enclosure | `system_body` | `#F3F6FA` fill, `#3E607F` outline, 6 px reference width |
| Main title bar | `system_title` | `#102B46` fill, no outline, white text |
| Primary system/domain block | `major_body` + `major_header` | `#E7EEF7` body with `#153D64` outline; `#163F68` title band, white title |
| Typed middle module | Family `*_body` + `*_header` | Pale body, medium-light colored title band, dark text |
| Nested slot/detail card | `detail_card` | `#F6F9FC` fill, `#7088A1` outline, dark text |
| Explanatory panel / legend | `info_body` + `info_header` | `#F6F9FC` body, `#3E607F` outline; `#BCCFE2` header, dark text |
| Word/register/bit or compact functional block | Family `*_leaf` | Muted colored fill with darker outline and `#102D49` text |
| Individual operator/function-choice detail | `operator_detail` | White fill, `#67488E` outline, dark text, as reference G1–G7 cells |
| Actual status badge | `status_badge` | `#17675E` fill, white text; only for a real stated status |

Classify by role and hierarchy, not rectangle area alone. Reference primary
execution blocks have dark bars; middle modules do **not** all have dark bars.
Do not darken every heading or every leaf. Reference white operator cells and
neutral nested cards are valid exceptions to uniform colored leaves.

For a different architecture, map its existing roles to these visual roles.
Do not copy T65's module names, hardware structure or role meanings into it.
Record the mapping so a reviewer can distinguish a primary block from a middle
module and a data cell from an operator-detail cell. An ambiguous semantic role
should retain a conservative neutral profile rather than a fabricated meaning.

## Middle-module body and header pairs

All rows use dark text `#102D49`, body outline width 3 px as a reference default,
and a title band with no separate outline. Actual body/header colors are:

| Family | Body fill | Body outline | Header fill |
|---|---|---|---|
| Blue | `#E7F0FA` | `#285B87` | `#B7CFE9` |
| Green | `#E2F1EA` | `#247263` | `#B4D9CB` |
| Gold | `#FFF0D9` | `#965611` | `#E9BE83` |
| Purple | `#EDE5F6` | `#67488E` | `#C8B8E0` |
| Slate | `#EEF3F8` | `#3D5974` | `#CBD7E3` |

Preserve the target diagram's established hue meanings when selecting a family.
A primary grouping can use the reference's uniform blue enclosure without
changing the engineering meaning of its internal colored blocks. Do not turn
every large module into a strongly tinted region: the reference relies on
very light bodies with a separate, more visible header.

## Small-module palette

| Family | Fill | Outline | Text |
|---|---|---|---|
| Blue | `#9DC5E7` | `#285B87` | `#102D49` |
| Green | `#A6D5C2` | `#247263` | `#102D49` |
| Gold | `#EFCE8D` | `#946019` | `#102D49` |
| Purple | `#BDA9DA` | `#5E4384` | `#102D49` |
| Slate | `#CBD7E3` | `#3D5974` | `#102D49` |

Keep fill, outline and text tokens separate. A dark outline is not a leaf fill.
Do not darken the fill to support white text. Use the reference's dark text.
Use the white operator-detail profile for an actual operator-choice detail,
not for all small data cells or as a way to erase the reference color hierarchy.

## Shape language, typography and illustrations

- Use flat native rectangles: `rounded=0`, `arcSize=0`, `shadow=0`. Avoid
  gradients, bevels, large corner radii, glossy fills, emoji or 3D decoration.
- Use `Noto Sans CJK SC` with editable text. Main and primary titles use white
  on dark blue; middle headers, leaves and notes use dark `#102D49`.
- Reference border hierarchy is 6 px for the outer/primary frames, 3 px for
  middle modules, 2.4 px for detail cards/data cells, and 2.8 px for explanatory
  panels. Keep existing coordinates, anchors and text size unless an actual
  layout/readability change is requested; check that wider strokes do not
  occlude arrows. Scaling an entire exported figure also scales these widths.
- Reference data illustrations are native, flat word/vector/register shapes
  with concise labels. Reuse that shape language for equivalent information.
  Existing relevant screenshots/photos remain evidence in separate pale panels;
  do not recolor their pixels, substitute unrelated T65 data art, or add logos
  and pictures merely to decorate the architecture.
- Existing icon marks should use the same flat geometry, semantic stroke
  families and pale surfaces; keep them subordinate to labels and main paths.
  Do not remove a meaningful object solely because the reference lacks it.

## Complete outer background

The system named by the main title includes the whole atlas, not just its title
strip. Use the reference's `system_body` enclosure around the title, legend,
navigation, all domains, cross-domain wires, images and footer notes, with
padding. A page/editor background or a filled title strip alone is insufficient.

Reuse an existing decorative outer backdrop. If absent, add one native editable,
non-connectable shape behind all existing content and mark its decorative role.
Do not reparent existing cells, move them, connect an edge to the backdrop or
count the backdrop as a new functional module or routing obstacle. Report its
id and bounds. Prevent duplicate enclosures on later runs.

## Apply and verify

1. Inspect reference/source styles and inventory target roles. Record exact
   profile assignments before editing. The bundled CLI has no T65 theme flag.
2. Apply fill, outline, text, flat-shape and relevant border-width tokens in a
   separate visual pass. In swimlanes, set the title and body fills independently;
   for separate header cells, style those cells according to their hierarchy.
3. Preserve functional ids, geometry, grouping, engineering text, image data,
   source metadata, endpoints, anchors and waypoints. Update only a styling
   legend when its old explanation conflicts with the new presentation.
4. Check typography/contrast on the actual local backgrounds. Resolve annotation
   text against its parent surface rather than making every annotation a box.
5. Render with draw.io, compare native topology and rendered connector paths,
   run scoped DRC and round-trip/re-export checks. Stroke changes may alter
   subpixel border clipping; inspect and report any material path/arrow change.
6. Review the complete overview, dense nested modules, small cells and the
   bottom enclosure. Confirm tier-specific headers, exact color tokens, flat
   shape language, readable text and preserved content. Deliver the source,
   preview, profile assignment/change log and actual verification limits.

Matching this visual system does not require copying the reference's topology,
canvas dimensions, font sizes verbatim or rearranging the user's engineering
master. Keep the engineering information intact while matching its presentation.
