# Garden artwork and photo workflow

## Behavior

- Six realistic plant cutouts replace emoji in the map, editor, picker preview,
  and bed lists: salvia, canna, hybrid tea rose, rudbeckia, daylily, gardenia.
- Locally served transparent WebP assets are cached. Unknown species and failed
  images use neutral markers. The remaining 24 catalog types need bespoke art.
- Both canvases use device pixel density. The public overview shows bed counts;
  tapping a bed or Explore bed reveals plants. Icon size is separate from the
  editor's selected/hovered mature-spread footprint.
- Public order: header, map, all beds, full gallery. Workspace Photos includes
  upload controls, individual bed/plant cards, then the full gallery.
- Upload forms permit multiple beds, species and individual plants. Labels are
  hashtag pills; Enter/Add tag commits a label and its remove button deletes it.
  The app adds # for display and blocks it in the input. Pasted hashes are stripped.
- Select a workspace plant and choose Add photos, or use its detail dialog/bed
  card. Save new planner plants first. Public plant details display that exact
  plant's tagged photos; signed-in owners get a workspace upload link with the
  plant preselected. Public visitors have no upload controls.
- Every uploaded file uses EXIF DateTimeOriginal, then DateTimeDigitized, retaining
  the camera-local date. File modification and upload dates are never substituted.
  Metadata-free photos stay undated. An explicit date override applies to the batch.
- Partial photo updates preserve omitted tags and validate before writing.
  The existing photo-subject table supports these additions; no schema migration.
  API responses add plantings and tags arrays. Install Pillow from requirements.

## Asset provenance

Built-in imagegen created one representative botanical cutout per species on
September 26, 2026. These are illustrations, not photos of the actual garden.
The shared prompt specified a complete plant clump, steep elevated three-quarter
view, realistic leaf/petal texture, subdued natural saturation, diffuse daylight
from upper left, transparent background, no pot/soil/text/border/shadow.
Subjects: violet-blue salvia spikes; green canna with red-orange blooms; red hybrid
tea roses; yellow rudbeckia with dark centers; yellow-orange daylilies; white
August Beauty gardenia flowers with glossy leaves.

Paths: `static/plants/<species-id>/map.webp` (256px) and `thumbnail.webp` (96px).
Thumbnails reuse the elevated-view artwork in this first pass. Exports retain
alpha and use quality 85. Registry: `static/js/plant-assets.js`.

## Verification

- `python -m unittest tests.test_garden_metadata tests.test_garden_travel -q`:
  nine tests passed; seven database tests skipped (no VIRTUWILL_TEST_PG).
- `NODE_PATH=/path/to/jsdom/node_modules node tests/garden_ui.cjs`: passed.
  Covers gallery order, hashtag entry/removal, plant photo detail, multi-bed and
  individual-plant upload payloads, cache deduplication, fallback drawing, aspect
  ratio, and DPR dimensions. This is a DOM test, not visual browser testing.
- JavaScript syntax, Python compilation, and whitespace checks passed.
- The six generated cutouts were visually inspected in the earlier work session.

Before merging, run the PostgreSQL tests and review mobile/desktop rendering,
lightboxes, upload failure/retry, pinch zoom and editor placement in a browser.
Chromium installation failed in the earlier session (invalid/truncated download),
so browser validation remains pending.

JPEG, PNG, WebP and GIF remain supported; HEIC conversion is not added. Old upload
 dates are not automatically rewritten. Cultivar-specific art, remaining species,
full-screen map controls and music-player layout changes are follow-up work.
