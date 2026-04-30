# Garden Planner — Requirements & Specification

**Project:** VirtuWill Garden Planner  
**Location:** 2709 Regal Road · Plano, TX · Lot 25, Block 3  
**Access:** Admin-only (`/garden-planner` nav item, visible only when signed in)  
**Route:** `page-planner` — separate from the public Garden Gallery (`page-garden`)

---

## 1. Access & Authentication

| Requirement | Detail |
|---|---|
| Admin-only | Planner is only accessible when signed in via the 🐙 admin gate |
| Nav visibility | "⚙ Garden Planner" nav item is hidden from the drawer until admin session is active |
| Visitor preview | Admin can toggle a "Visitor Preview" mode from the toolbar to see the planner as a visitor would |
| Public garden page | The main `/garden` page is a public photo gallery — the planner is entirely separate |

---

## 2. Canvas & Coordinate System

| Requirement | Detail |
|---|---|
| Lot dimensions | 80 ft × 120 ft (plat-calibrated to 2709 Regal Road) |
| Grid resolution | 3-inch dot grid (0.25 ft per cell) displayed inside each active bed |
| Scale bar | Live scale bar rendered on-canvas showing 1 ft at the current zoom level |
| North indicator | Compass N indicator rendered in the upper-right corner |
| Satellite imagery | Illustrated aerial watercolor of the property loaded as canvas background |
| Fallback background | Dark green fill if illustration fails to load |
| Zoom | Mouse wheel zoom; zoom controls (+, fit, −); max zoom 120× in bed edit, 20× in overview |
| Pan | Click and drag on empty canvas space; middle mouse button always pans |

---

## 3. Beds

### 3.1 Default Bed Set (14 beds, plat-calibrated)

| ID | Name | Color |
|---|---|---|
| 1 | NW Corner | Teal `#5DCAA5` |
| 2 | West Entry | Blue `#378ADD` |
| 3 | East Front Strip | Pink `#D4537E` |
| 4 | Tree Circle | Orange `#EF9F27` (circular polygon, 20 vertices) |
| 5 | NE Rear Right | Purple `#7F77DD` |
| 6 | NE Rear Strip | Green `#1D9E75` |
| 7 | NE Corner | Red `#E24B4A` |
| 8 | Rear Center | Olive `#639922` |
| 9 | NE Rear | Teal `#5DCAA5` |
| 10 | Rear West Strip | Blue `#378ADD` |
| 11 | Front West | Pink `#D4537E` |
| 12 | Front East | Orange `#EF9F27` |
| 13 | East Side Strip | Purple `#7F77DD` |
| 14 | West Side Strip | Green `#1D9E75` |

### 3.2 Overview (grounds map)

| Requirement | Detail |
|---|---|
| All beds visible | All 14 beds rendered simultaneously over the satellite image |
| Hover highlight | Hovering a bed increases fill opacity and border weight; shows tooltip with name, sq ft, plant count |
| Selected bed | Currently active bed shown with thicker solid border and brighter fill |
| Cursor | `grab` cursor when hovering a bed; `grabbing` while dragging |
| Bed drag | Click and hold a bed to drag its entire polygon to a new position on the canvas |
| Drag moves plants | All plants and season boundaries move with the bed when dragged |
| Click to edit | A click (no meaningful drag) opens the bed editor for that bed |
| Empty space pans | Clicking and dragging on empty canvas space pans the overview camera |
| Plant count badge | Each bed shows a green badge with plant count if > 0 |
| LineDash isolation | Each bed's stroke is fully reset before draw to prevent style leaking between beds |

### 3.3 Bed editor

| Requirement | Detail |
|---|---|
| Active bed in focus | Active bed rendered at full brightness; all other beds dimmed (low fill + low stroke opacity) |
| Vignette | Dark overlay applied outside the active bed shape using `destination-out` composite |
| Other beds visible | Other beds remain visible but clearly subordinate — they do not disappear |
| Click outside to exit | Clicking anywhere outside the active bed polygon returns to the overview without saving |
| Escape to exit | `Escape` key navigates back to the overview |
| Save is explicit | Only the Save button persists data to the server — navigation away does not auto-save |
| 3-inch dot grid | Grid of dots rendered inside the active bed at 0.25 ft spacing |
| Bed boundary | Active bed boundary drawn with 2px solid stroke in the bed's color |

---

## 4. Boundary Tools

| Tool | Behaviour |
|---|---|
| **Perimeter** | Click individual vertices to draw a polygon. Click the first point (or Ctrl+D) to close. Minimum 3 vertices. |
| **Highlighter** | Freehand paint strokes over the bed area. Brush size adjustable via slider (shown in inches). Press Apply (or Ctrl+Enter) to convert the painted area to a convex hull boundary. Clear button resets strokes without applying. |
| **Reshape** | Each boundary vertex shown as a draggable dot. Drag to move. Double-click on an edge to insert a new vertex. Empty-space click starts a pan instead. |

All three tools update `season.boundary` in memory. A toast message instructs the user to "click Save to persist." The save button writes to the server.

---

## 5. Plant Tools

| Tool | Behaviour |
|---|---|
| **Select** | Default mode. Hover a plant → emphasis ring + name highlight. Single click → selects + starts drag (move to new grid position on release). Empty-space click → deselects. |
| **Place** | Drops a new plant of the selected species at the nearest grid point on click. If an existing plant is clicked, selects it instead of placing a new one. |
| **Erase** | Click on a plant to remove it from the season. |

### 5.1 Plant interaction

| Interaction | Result |
|---|---|
| Hover | Bright outer ring, label emphasis, cursor → pointer |
| Single click | Selects plant; begins drag if held and moved |
| Click + drag | Plant follows cursor snapped to 3-inch grid; released at new position |
| Double-click | Opens detail popup (canvas-rendered overlay) |
| Popup | Shows health status, mature diameter, spacing, age, notes, "Edit details →" button |
| Popup → Edit | Opens full plant detail modal |

### 5.2 Plant detail modal

Fields editable per plant:

- Display name (free text)
- Species (linked to inventory)
- Description / notes
- Age (years)
- Footprint diameter (inches, converted to feet internally)
- Health status (slider: Dead / Struggling / Growing / Blooming / Full Bloom)
- Photo gallery (upload per-plant photos)

---

## 6. Plant Inventory (30 species)

Species are grouped by category in the sidebar dropdown. Each species carries:

| Field | Example |
|---|---|
| `id` | `hybrid-tea-rose` |
| `name` | Hybrid Tea Rose |
| `cat` | Rose / Perennial / Annual / Bulb / Shrub / Tropical / Biennial / Annual Vine |
| `matureDia` | Mature diameter in feet |
| `spacing` | Recommended spacing in feet |
| `emoji` | Display emoji on canvas |

### Categories & species

| Category | Species |
|---|---|
| Rose | Hybrid Tea Rose, Floribunda Rose, Climbing Rose |
| Perennial | Salvia Farinacea, Rudbeckia, Shasta Daisy, Day Lily, Cone Flower, Columbine, Lantana, Blanket Flower, MilkWeed |
| Annual | Marigold, Mammoth Sunflower, Teddybear Sunflower, Petunia, Rocket Larkspur, Zinnia |
| Bulb | Dahlia Baja Aztec, Daffodil, Dutch Iris, Gladiolus, Asiatic Lily |
| Biennial | Holly Hock |
| Tropical | Canna, Plumeria |
| Shrub | August Beauty Gardenia, Hydrangea, Hybiscus |
| Annual Vine | Morning Glory |

### Species selector

- Displayed as a `<select>` dropdown in the bed sidebar, grouped by category
- Each option shows: `{emoji} {name} ({matureDia}ft)`
- Selecting a species automatically switches to Place mode
- Click on bed grid to place the selected species

---

## 7. Canvas Plant Rendering

| Element | Detail |
|---|---|
| Footprint circle | Semi-transparent fill; green if inside bed boundary, orange if outside |
| Centre dot | Health-colour dot at the plant centre |
| Emoji | Plant emoji rendered at zoom ≥ 4× |
| Name label | Plant name rendered below circle at zoom ≥ 4×; outlined for legibility |
| Warning badge | ⚠️ emoji shown if plant centre is outside bed boundary |
| Selected state | Bright white outer ring, thicker stroke |
| Hover state | Secondary outer ring, brighter name label |
| Dragging state | Reduced opacity (0.55 fill alpha), plant follows cursor |
| Outside-bed warning | Orange fill + ⚠️ badge; plant still allowed to exist there |

---

## 8. Health Status System

| Level | Icon | Label | Color |
|---|---|---|---|
| 0 | 💀 | Dead | Dark red |
| 1 | 🥀 | Struggling | Brown |
| 2 | 🌱 | Growing | Green (default) |
| 3 | 🌸 | Blooming | Pink |
| 4 | 🌺 | Full Bloom | Red |

---

## 9. Season Snapshots

| Requirement | Detail |
|---|---|
| Multiple seasons per bed | Each bed stores a list of season snapshots |
| Season data | Label, date, notes, boundary polygon, plant list, photo array |
| New season | Prompt for label; copies current plants into the new season |
| Switch season | Select from dropdown in sidebar; renders that season's plants and boundary |
| Season history tab | Reverse-chronological list of all seasons with date and plant count |
| Active season indicator | Current active season highlighted in history list |
| Season inheritance | New seasons copy the previous season's plant layout as a starting point |

---

## 10. Sidebar (Bed Edit Mode)

Four tabs:

| Tab | Content |
|---|---|
| **Bed** | Bed name (editable), season notes, season selector, species dropdown, plant list |
| **Plants** | (Legacy — replaced by species dropdown in Bed tab) |
| **Photos** | Per-bed photo grid; admin can upload; clicking opens lightbox |
| **History** | Season history list; click to switch active season |

Plant list items (Bed tab):

- Emoji + health color chip
- Name and health label
- Age if set
- ⋯ button opens the full plant detail modal
- Click → selects on canvas; double-click → opens detail modal

---

## 11. Photo Management

| Requirement | Detail |
|---|---|
| Bed photos | Photos can be attached to a season (tab in bed sidebar) |
| Plant photos | Photos can be attached to individual plants (inside plant detail modal) |
| Upload | File picker triggered from sidebar; supports multiple files |
| Storage | Blob URLs used in-session; persisted to `/api/garden/photo` if server accepts |
| Lightbox | Click any photo thumbnail to open full-screen lightbox |
| Blob URL stripping | Blob URLs are stripped from saved data (they are session-only) |

---

## 12. Persistence

| Requirement | Detail |
|---|---|
| Backend endpoint | `POST /api/garden` — saves full bed array as JSON |
| Load endpoint | `GET /api/garden` — loads saved bed data on init |
| Fallback | If server load fails, defaults to the 14 hard-coded beds |
| Explicit save | User must press "💾 Save garden" — no auto-save |
| Toast on save | Success or error toast displayed after save attempt |
| Blob URL cleanup | Blob URLs for photos are stripped before posting to the server |
| nid tracking | Numeric IDs are tracked and incremented to avoid collisions on new plants/seasons |

---

## 13. Toolbar & Controls

### Overview toolbar
- Grounds label
- 💾 Save button
- 👁 Visitor Preview toggle

### Bed edit toolbar
- Breadcrumb: `🌿 Grounds › Bed Name`
- **Boundary:** Perimeter | Highlight | Reshape
- **Plants:** Select | Place | Erase
- Pan button
- 💾 Save button
- Highlighter sub-controls: brush size slider + Apply + Clear
- Perimeter sub-controls: vertex instruction text + Close button

### Zoom controls (canvas overlay)
- `+` zoom in (×1.25)
- `▶` fit to screen
- `−` zoom out (×0.80)

---

## 14. Keyboard Shortcuts

| Shortcut | Action |
|---|---|
| `Escape` | Close popup → cancel draw mode → return to overview |
| `Ctrl + D` | Finish perimeter (close polygon) |
| `Ctrl + Enter` | Apply highlighter as boundary |

---

## 15. Data Model

```json
{
  "beds": [
    {
      "id": 1,
      "name": "NW Corner",
      "color": "#5DCAA5",
      "polygon": [{ "x": 28, "y": 7 }, ...],
      "activeSeason": 201,
      "seasons": [
        {
          "id": 201,
          "label": "Spring 2025",
          "date": "2025-03-01",
          "note": "Planted roses and salvia.",
          "boundary": [{ "x": 28, "y": 7 }, ...],
          "plants": [
            {
              "id": 301,
              "x": 29.5,
              "y": 8.25,
              "species": "hybrid-tea-rose",
              "displayName": "Hybrid Tea Rose",
              "emoji": "🌹",
              "footprintFt": 4,
              "matureDia": 4,
              "spacing": 3,
              "age": 1,
              "health": 3,
              "notes": "Planted March 2025",
              "photos": []
            }
          ],
          "photos": []
        }
      ]
    }
  ]
}
```

---

## 16. Known Constraints & Decisions

| Item | Decision |
|---|---|
| Coordinate space | World units = feet. Canvas rendered via `ctx.scale(cam.z) + ctx.translate(cam.x, cam.y)` |
| No auto-save | Deliberate — prevents accidental overwrites when navigating between beds |
| Plant photos | Session-only blob URLs unless server endpoint `/api/garden/photo` is reachable |
| Visitor preview | Toolbar hidden in visitor mode; reshape vertices hidden; plant popup still available |
| Boundary vs polygon | `bed.polygon` = original shape (always present). `season.boundary` = overrides polygon when set |
| Convex hull | Highlighter tool generates a convex hull — concave areas of painted strokes are filled in |
| Drag threshold | Overview click vs drag distinguished by 5px pixel movement threshold |
