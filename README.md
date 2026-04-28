# pelican-osm

**pelican-osm** is a Pelican plugin that embeds interactive [OpenStreetMap](https://www.openstreetmap.org/) maps into your articles using a simple `{% place %}` shortcode. It integrates with [Leaflet.js](https://leafletjs.com/) and loads place data from YAML files.

## Features

- `{% place %}` shortcode renders an independent interactive map per shortcode
- `{% place_list %}` shortcode renders a sortable table of places with tag filtering, row count, optional grouping, and multi-level collapsible summary headers
- Multi-value fields (e.g. multiple visit dates) render as joined cells with sort behaviour configurable via JSON Schema hints
- YAML files converted to GeoJSON at build time — JS fetches them at runtime
- Flexible spec syntax: single file, single place via `#id`, entire folder, or comma-separated mix
- File-level metadata (anime title, tags, country…) applied as defaults to every place in the file
- Per-place popup with auto-generated OSM and Google Maps links
- `tags` list rendered as clickable badges — click to filter the table by tag
- `urls` list rendered as labelled links in the popup and list table
- All extra YAML fields displayed in the popup automatically
- Horizontal scroll photo gallery in popups with lightbox viewer (swipe on mobile)
- Optional marker clustering via [Leaflet.markercluster](https://github.com/Leaflet/Leaflet.markercluster) (auto-detected)
- Lazy map initialization — maps only load when scrolled into view
- Reset view button (↺) to return to the original map bounds
- Deep linking — link directly to a place via URL hash (e.g. `page.html#place_id`)
- Error/empty state messages when data fails to load
- Auto-detects `<html lang>` for built-in translations (zh, ja), with full override via `window.OSM_I18N`
- Optional [JSON Schema](https://json-schema.org/) validation for place YAML — drop a `_schema.yaml` next to your files and the plugin enforces it at build time
- Fully class-based CSS — every visual detail overridable via custom properties
- Dark mode support

## How it works

```text
content/places/japan/mygo.yaml   →   output/static/places/japan/mygo.geojson
                                              ↑
                              browser fetches at runtime via Leaflet
```

Each YAML file under `OSM_PLACES_ROOT` is converted to a GeoJSON FeatureCollection at build time. The `{% place %}` shortcode emits a `<div>` with `data-geojson` pointing to the corresponding file(s); the bundled JS fetches and renders them.

## Installation

```bash
pip install pelican-osm
```

## Setup

### 1. Add to pelicanconf.py

```python
PLUGINS = ["pelican.plugins.osm"]
```

### 2. Add Leaflet.js and plugin assets to your base template

```html
<link rel="stylesheet" href="https://unpkg.com/leaflet/dist/leaflet.css">
<script src="https://unpkg.com/leaflet/dist/leaflet.js"></script>

<!-- Plugin assets — auto-copied to output/static/pelican_osm/ on build -->
<link rel="stylesheet" href="/static/pelican_osm/css/osm-map.css">
<script src="/static/pelican_osm/js/osm-map.js" defer></script>
```

### 3. Organize your YAML files

Set the root path in `pelicanconf.py` (default: `places/` inside your content folder):

```python
OSM_PLACES_ROOT = "places"  # relative to PATH (content dir), or absolute
```

Both `.yml` and `.yaml` extensions are supported.

```text
content/
└── places/
    ├── taiwan.yml
    └── japan/
        ├── mygo.yaml
        └── ave-mujica.yml
```

## YAML format

### locations format (preferred)

The `locations` key holds the list of places. Every other top-level key becomes a **file-level default** applied to all places in the file — per-place values always win.

```yaml
# content/places/japan/mygo.yaml
anime: BanG Dream! It's MyGO!!!!!
tags: [動畫]

locations:
  - id: normal_park
    name: 豊島区立南池袋第二公園
    lat: 35.7225
    lon: 139.7170
    category: 公園
    notes: "「普通」和「理所當然」是什麼呢？"
    date: 2023-06-29
    country: 日本
    city: 東京
    tags: []       # overrides file-level tags for this place
    images: []
```

Empty strings (`""`) and empty lists (`[]`) are automatically stripped — they won't appear in the popup or GeoJSON.

### Dict of places (also supported)

The reserved `defaults` key spreads shared attributes. Each other top-level key is a place id usable in `#fragment` references.

```yaml
defaults:
  country: Japan

ueno_park:
  name: 上野公園
  lat: 35.7142
  lon: 139.7742
  date: 2024-03-25

shinjuku:
  name: 新宿
  lat: 35.6938
  lon: 139.7034
```

### Bare list (backwards compatible)

```yaml
- name: 台北101
  lat: 25.0337
  lon: 121.5645

- name: 太魯閣
  lat: 24.1558
  lon: 121.6213
```

A leading `{defaults: {...}}` item sets shared attributes for the whole file.

## Shortcode syntax

Each `{% place %}` shortcode renders its own independent map.

| Syntax | Result |
| --- | --- |
| `{% place japan/mygo.yaml %}` | All places in one file |
| `{% place japan/mygo.yaml#normal_park %}` | Single place by id (dict-format key) |
| `{% place japan/mygo.yaml#豊島区立南池袋第二公園 %}` | Single place by name (fallback) |
| `{% place japan/ %}` or `{% place japan %}` | All YAML files in a folder, recursively |
| `{% place . %}` | All YAML files under the root |
| `{% place japan/mygo.yaml, taiwan.yml %}` | Multiple specs on one map |
| `{% place_list japan/mygo.yaml %}` | Renders a table of places from one or more YAML specs. |

```markdown
{% place_list japan/tokyo %}
{% place_list japan/tokyo, japan/kyoto %}
```

> **Note:** Fragment (`#`) syntax filters which places appear in the popup, but the map still fetches the full GeoJSON file. A future version may support per-feature filtering.

## Grouping and summary headers (`place_list`)

`{% place_list %}` accepts kwargs to collapse rows by shared field values and to surface those values as section headers:

```text
{% place_list pilgrimage group_by="country,city" group_summary_at="country,city" aggregate="date:year" %}
```

| Kwarg | Description |
| --- | --- |
| `group_by` | Comma-separated fields. Places sharing the same tuple of these values collapse into one row. The first non-empty value wins for non-aggregated fields; `tags` are unioned. |
| `aggregate` | `field:op` pairs, comma-separated. Currently `year` collects unique years from a date-like field, sorted ascending and comma-joined. |
| `group_summary_at` | A prefix of `group_by`. Listed fields are removed from data-row columns and emitted as section headers above each group. |

When `group_summary_at` lists multiple fields, each level renders as a nested heading: depth-0 most prominent, deeper levels smaller and indented, each with its own background colour. A subtotal place count appears under every level (configurable via `OSM_LIST_GROUP_COUNT_TEMPLATE`).

Headers are interactive:

- **Click** a header (or focus + Enter / Space) to collapse its subtree; click again to expand.
- Each header has a stable `id="osm-group--<slug>"` for deep linking — e.g. `page.html#osm-group--japan--tokyo`. Loading the page with that hash auto-expands all ancestor groups so the target row is visible.

Sorting a column re-orders data rows *within* each leaf group; group-header rows stay pinned in their YAML/define order so the hierarchy is preserved.

## Multi-value fields and schema hints

A YAML field can hold a list — useful for things like multiple visit dates on the same place:

```yaml
- name: 某神社
  lat: 35.7
  lon: 139.7
  date: [2024-01-15, 2025-03-12]
```

The table joins list values into one cell. Two `x-osm-*` keys on the field's schema entry control display and sorting:

```yaml
# content/places/pilgrimage/_schema.yaml
properties:
  date:
    type: array
    items:
      type: string
      format: date
    x-osm-list-join: ", "
    x-osm-list-sort: max
```

| Hint | Values | Effect |
| --- | --- | --- |
| `x-osm-list-join` | any string (default `", "`) | Separator between list items in the cell. |
| `x-osm-list-sort` | `min` / `max` / `first` / `last` | Sets the cell's `data-sort-value` so column sorting picks one canonical key. `max` = most-recent visit drives the sort. |

Both keys use the JSON Schema `x-` extension prefix, so validators ignore them. The schema is loaded at render time for any spec passed to `{% place_list %}` — the same `_schema.yaml` you already use for validation provides these hints. Scalar values still render unchanged (`datetime.date` → ISO string).

## Place fields

| Field | Required | Notes |
| --- | --- | --- |
| `name` | ✅ | Popup title and map caption |
| `lat` | ✅ | Latitude (float) |
| `lon` | ✅ | Longitude (float) |
| `tags` | — | List — rendered as inline badges in the popup |
| `images` | — | List — rendered as a photo gallery in the popup |
| `urls` | — | List — rendered as links in the popup and list table; see below |
| *(any)* | — | All other fields shown as `Key: Value` lines |

OSM and Google Maps links are **always auto-generated** from `lat`/`lon`.

### `urls` field

The `urls` field renders clickable links in both the map popup and the `place_list` table. Three formats are accepted:

```yaml
# plain string
urls: "https://example.com/my-post"

# single object with optional label
urls:
  label: "2024"
  href: "{filename}posts/review/2024/my-post.md"

# list of objects (multiple links)
urls:
  - label: "2023"
    href: "{filename}posts/review/2023/visit.md"
  - label: "2024"
    href: "{filename}posts/review/2024/visit.md"
```

The `label` becomes the link text. When omitted, the link text falls back to the URL's hostname (e.g. `example.com`).

`{filename}` references are resolved to absolute URLs using Pelican's content URL map.

## GeoJSON output

Every YAML file is converted to a GeoJSON FeatureCollection at build time, mirroring the source directory structure:

```text
content/places/japan/mygo.yaml   →   output/static/places/japan/mygo.geojson
content/places/taiwan.yml        →   output/static/places/taiwan.geojson
```

The GeoJSON files are standard [RFC 7946](https://datatracker.ietf.org/doc/html/rfc7946) and can be used with any GeoJSON-compatible tool (QGIS, Mapbox, etc.).

## Configuration

| Setting | Default | Description |
| --- | --- | --- |
| `OSM_SHORTCODE` | `"place"` | Shortcode tag name |
| `OSM_PLACES_ROOT` | `"places"` | Root folder for YAML files (relative to `PATH`) |
| `OSM_MAP_HEIGHT` | `"400px"` | Map height (any CSS length value) |
| `OSM_MAP_TILE` | OSM standard tiles | Leaflet tile URL template |
| `OSM_MAP_ATTRIBUTION` | OSM attribution HTML | Attribution string shown on the map |
| `OSM_STATIC_PREFIX` | `"/static"` | URL prefix for generated GeoJSON files |
| `OSM_LIST_SHORTCODE` | `"place_list"` | Shortcode name |
| `OSM_LIST_FIELDS` | `[]` (auto) | Ordered list of field keys to show as columns. When empty, all non-reserved fields found in the data are used. |
| `OSM_LIST_FIELD_LABELS` | `{}` | Override column header labels, e.g. `{"date": "Visited", "name": "Place"}` |
| `OSM_LIST_GROUP_COUNT_TEMPLATE` | `"{n} places"` | Format string for the per-group subtotal under `group_summary_at` headers. `{n}` is the place count. Set to `""` to omit. |
| `OSM_VALIDATE_SCHEMA_FILENAMES` | `["_schema.yaml", "_schema.yml", "_schema.json"]` | Filenames the validator looks for. Accepts a string or list. |
| `OSM_VALIDATE_STRICT` | `False` | Raise `RuntimeError` on validation failure instead of just logging warnings. |

## Schema validation

Place YAML can be validated against a [JSON Schema](https://json-schema.org/) at build time. Validation is **opt-in by file presence** — drop a `_schema.yaml` (or `.yml`/`.json`) anywhere under `OSM_PLACES_ROOT` and the plugin will validate every place YAML in the same folder and its subfolders. No schema present → no validation runs.

### 1. Install the optional dependency

```bash
pip install "pelican-osm[validate]"
```

If schema files exist but `jsonschema` isn't installed, the plugin logs a warning and skips validation (your build still succeeds).

### 2. Drop a schema next to your YAML files

```text
content/places/
├── _schema.yaml              ← applies to every YAML below
├── restaurant.yaml
└── pilgrimage/
    ├── _schema.yaml          ← overrides the parent for this folder
    ├── yuru-camp.yaml
    └── tamako-market.yaml
```

The plugin uses **nearest-ancestor lookup**: a YAML is validated against the closest `_schema.yaml` walking up the directory tree.

### 3. Example schema

```yaml
# content/places/pilgrimage/_schema.yaml
$schema: "https://json-schema.org/draft/2020-12/schema"
type: object
required: [anime, locations]
properties:
  anime: {type: string}
  tags:  {type: array, items: {type: string}}
  locations:
    type: array
    items:
      type: object
      required: [name, lat, lon, country, city]
      additionalProperties: false
      properties:
        name:     {type: string, minLength: 1}
        lat:      {type: number, minimum: -90,  maximum: 90}
        lon:      {type: number, minimum: -180, maximum: 180}
        category: {type: string}
        notes:    {type: string}
        date:     {type: string, format: date}
        country:  {type: string}
        city:     {type: string}
        tags:     {type: array, items: {type: string}}
        images:   {type: array, items: {type: string}}
```

Files starting with `_` (e.g. `_schema.yaml`) are never loaded as place data — they're skipped by both the resolver and the GeoJSON exporter.

Unquoted dates (`date: 2026-02-22`) are normalized to ISO 8601 strings before validation, so schemas can use `type: string, format: date` even though PyYAML parses them as `datetime.date`.

By default, validation failures are logged as warnings. Set `OSM_VALIDATE_STRICT = True` to raise a `RuntimeError` and fail the build instead.

## Deep linking

Link directly to a specific place by appending its `id` or `name` as a URL hash:

```text
https://example.com/my-post.html#normal_park
https://example.com/my-post.html#豊島区立南池袋第二公園
```

The map will pan to the marker and open its popup automatically. When marker clustering is enabled, the cluster is expanded first.

## Tag filtering

Tag badges are clickable in both maps and tables.

**In `{% place_list %}` tables:** Clicking a tag filters the table to show only rows with that tag. A filter chip appears next to the row count — click it (or click the same tag again) to clear the filter.

**In `{% place %}` maps:** A tag bar appears below the map when places have tags. Click a tag to show only markers with that tag; click again to show all. The map automatically re-fits to the visible markers.

## Marker clustering

[Leaflet.markercluster](https://github.com/Leaflet/Leaflet.markercluster) is automatically loaded from CDN at runtime. Nearby markers are grouped into clusters that expand on click/zoom. No extra setup is needed.

## Customising the CSS

All visual properties are CSS custom properties declared on `:root`. Override in your own stylesheet (loaded after `osm-map.css`):

```css
/* Change map height globally */
:root {
  --osm-map-height: 300px;
}

/* Remove rounded corners and shadow */
.osm-map-block {
  --osm-radius: 0;
  --osm-shadow: none;
}
```

### Available custom properties

| Property | Default | Controls |
| --- | --- | --- |
| `--osm-map-height` | `400px` | Map canvas height |
| `--osm-radius` | `8px` | Block border radius |
| `--osm-shadow` | `0 2px 8px …` | Block drop shadow |
| `--osm-caption-bg` | `#f5f5f5` | Caption bar background |
| `--osm-caption-color` | `#555` | Caption text colour |
| `--osm-caption-font-size` | `0.9em` | Caption font size |
| `--osm-caption-padding` | `0.4em 0.8em` | Caption padding |
| `--osm-caption-border` | `1px solid #ddd` | Caption top border |
| `--osm-popup-min-width` | `200px` | Popup minimum width |
| `--osm-popup-font-size` | `1.3em` | Popup base font size |
| `--osm-popup-line-height` | `1.6` | Popup line height |
| `--osm-popup-name-size` | `1.15em` | Place name font size |
| `--osm-popup-name-weight` | `700` | Place name font weight |
| `--osm-popup-name-gap` | `0.35em` | Gap below place name |
| `--osm-badge-font-size` | `0.82em` | Badge font size |
| `--osm-badge-padding` | `0.15em 0.6em` | Badge padding |
| `--osm-badge-radius` | `999px` | Badge border radius |
| `--osm-badge-tag-bg` | `#e8e8e8` | Tag badge background |
| `--osm-badge-tag-color` | `#444` | Tag badge text colour |
| `--osm-field-gap` | `0.15em` | Vertical gap between field rows |
| `--osm-field-color` | `#333` | Field value colour |
| `--osm-label-color` | `#111` | Field label colour |
| `--osm-label-weight` | `600` | Field label font weight |
| `--osm-links-gap` | `0.65em` | Gap above links row |
| `--osm-links-font-size` | `0.9em` | Links row font size |
| `--osm-links-color` | `#666` | Links row text colour |
| `--osm-links-anchor-color` | `#c0392b` | OSM / Google anchor colour |

## i18n

The plugin auto-detects the page language from `<html lang="...">` and applies built-in translations when available. Currently supported: `zh` (Traditional Chinese), `ja` (Japanese). All other languages fall back to English.

You can override any string by setting `window.OSM_I18N` **before** loading `osm-map.js`. Manual overrides take priority over auto-detected translations.

```html
<script>
window.OSM_I18N = {
  // Map link labels (defaults: "OSM", "Google")
  osmLink:      "OSM",
  googleLink:   "Google",

  // Place count label below tables (receives row count as argument)
  placeCount:   (n) => `${n} 個地點`,

  // Error/empty state messages
  loadError:    "無法載入地圖資料",
  noPlaces:     "找不到地點",

  // Fallback link text for urls entries with no label.
  // Defaults to the URL's hostname (e.g. "example.com").
  // Only used when the hostname cannot be parsed.
  urlLinkLabel: "Link",

  // Field label overrides — YAML key → display label
  // Unlisted keys fall back to capitalised key name (e.g. "category" → "Category")
  fieldLabels: {
    date:     "日期",
    location: "地點",
    category: "分類",
    type:     "分類",
    work:     "作品",
    series:   "系列",
    note:     "備註",
    notes:    "備註",
    anime:    "作品",
    city:     "城市",
    country:  "國家",
  },
};
</script>
<script src="/static/pelican_osm/js/osm-map.js" defer></script>
```

`fieldLabels` is shallow-merged — only list the keys you want to change.

### Available i18n keys

| Key | Default (en) | Description |
| --- | --- | --- |
| `osmLink` | `"OSM"` | OSM link label in popups and tables |
| `googleLink` | `"Google"` | Google Maps link label |
| `placeCount` | `(n) => "N places"` | Row count below tables (function) |
| `loadError` | `"Failed to load map data"` | Shown when all GeoJSON fetches fail |
| `noPlaces` | `"No places found"` | Shown when no markers match |
| `urlLinkLabel` | `"Link"` | Fallback text for unlabelled URLs |
| `fieldLabels` | `{}` | YAML key to display label mapping |

## License

MIT © Wei Lee
