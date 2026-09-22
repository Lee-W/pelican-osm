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
- Non-reserved YAML fields displayed in the popup automatically
- Horizontal scroll photo gallery in popups with lightbox viewer (swipe on mobile)
- Optional marker clustering via [Leaflet.markercluster](https://github.com/Leaflet/Leaflet.markercluster) (auto-detected)
- Lazy map initialization — maps only load when scrolled into view
- Reset view button (↺) to return to the original map bounds
- Deep linking — link directly to a place via URL hash (e.g. `page.html#place_id`)
- Error/empty state messages when data fails to load
- Per-component English, Traditional Chinese and Japanese UI, translated labels and optional place-data translations; see [i18n](#i18n)
- Optional [JSON Schema](https://json-schema.org/) validation for place YAML — drop a `_schema.yaml` next to your files and the plugin enforces it at build time
- Class-based CSS with custom properties for map, popup and table styling
- Dark mode support

## How it works

```text
content/places/japan/mygo.yaml   →   output/static/places/japan/mygo.geojson
                                              ↑
                              browser fetches at runtime via Leaflet
```

Each non-private place YAML file under `OSM_PLACES_ROOT` is converted to a GeoJSON FeatureCollection at build time. The `{% place %}` shortcode emits a `<div>` with `data-geojson` pointing to the corresponding file(s); the bundled JS fetches and renders them.

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
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>

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

The `locations` key holds the list of places. Every other top-level key becomes a **file-level default** applied to all places in the file — per-place values override defaults, except `tags`, which are combined without duplicates (file tags first). An empty `tags: []` does not clear inherited tags.

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
    tags: []       # still inherits the file-level 動畫 tag
    images: []
```

Empty strings (`""`) and empty lists (`[]`) are automatically stripped — they won't appear in the popup or GeoJSON.

### Dict of places (also supported)

The reserved `defaults` key spreads shared attributes. Each other top-level key supplies the place ID for `#fragment` references unless that record explicitly sets its own `id`.

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

A standalone `{defaults: {...}}` item sets defaults for subsequent places. A later defaults item replaces those defaults for the records that follow it.

## Shortcode syntax

Each `{% place %}` shortcode renders its own independent map.

| Syntax | Result |
| --- | --- |
| `{% place japan/mygo.yaml %}` | All places in one file |
| `{% place japan/mygo.yaml#normal_park %}` | Places matching an explicit `id` or an inferred dict-format key |
| `{% place japan/mygo.yaml#豊島区立南池袋第二公園 %}` | Places matching the name |
| `{% place japan/ %}` or `{% place japan %}` | All YAML files in a folder, recursively |
| `{% place . %}` | All YAML files under the root |
| `{% place japan/mygo.yaml, taiwan.yml %}` | Multiple specs on one map |
| `{% place_list japan/mygo.yaml %}` | Renders a table of places from one or more YAML specs. |

```markdown
{% place_list japan/tokyo %}
{% place_list japan/tokyo, japan/kyoto %}
```

> **Note:** A fragment matches either `id` or `name`; all matching places are included. The map fetches the full GeoJSON file, then filters features before creating markers. Captions and `place_list` rows are filtered at build time. This controls presentation, not access to the other records in the GeoJSON file.

## Grouping and summary headers (`place_list`)

`{% place_list %}` accepts kwargs to bucket rows under shared field values and to surface those values as section headers:

```text
{% place_list pilgrimage group_by="country,city" group_summary_at="country,city" aggregate="date:year" %}
```

| Kwarg | Description |
| --- | --- |
| `group_by` | Comma-separated fields. Places sharing the same tuple of these values are bucketed contiguously so the table reads as a tree. Rows are preserved as-is unless `aggregate` is also given. |
| `aggregate` | `field:op` pairs, comma-separated. Setting this opts into SQL-style collapse: rows sharing a `group_by` tuple merge into one, with the listed fields aggregated (e.g. `year` collects unique years, sorted ascending and comma-joined), other fields taking first-non-empty, and `tags` unioned. |
| `group_summary_at` | A prefix of `group_by`. Listed fields are removed from data-row columns and emitted as section headers above each group. |

When `group_summary_at` lists multiple fields, each level renders as a nested heading: depth-0 most prominent, deeper levels smaller and indented, each with its own background colour. A subtotal place count appears under every level (configurable via `OSM_LIST_GROUP_COUNT_TEMPLATE`).

Headers are interactive:

- **Click** a header (or focus + Enter / Space) to collapse its subtree; click again to expand.
- Each header has a stable `id="osm-group--<slug>"` for deep linking — e.g. `page.html#osm-group--japan--tokyo`. Loading the page with that hash auto-expands all ancestor groups so the target row is visible.

Sorting a column re-orders data rows *within* each leaf group; group-header rows stay pinned in their YAML/define order so the hierarchy is preserved.

## Schema-driven `place_list` hints

The same `_schema.yaml` you use for validation also drives several display behaviours of `{% place_list %}`. The schema is loaded at render time for whatever spec the shortcode targets, so you only need to declare each hint once next to the field definition.

```yaml
# content/places/pilgrimage/_schema.yaml
$schema: "https://json-schema.org/draft/2020-12/schema"
type: object
properties:
  date:
    type: array
    items: { type: string, format: date }
    title: 日期               # ← column header
    x-osm-list-join: ", "     # ← separator for list cells
    x-osm-list-sort: max      # ← canonical sort key (most-recent visit)
  category:
    type: string
    title: 分類
  internal_order:
    type: integer
    x-osm-list-hidden: true   # ← loaded but never rendered as a column
```

| Hint | Values | Effect |
| --- | --- | --- |
| `title` | any string | Column header text. Standard JSON Schema keyword. |
| `x-osm-list-hidden` | `true` / `false` | Drop this field from the table. It's still loaded, so `group_by` / `aggregate` / sort can use it. Works for `tags` / `urls` too. |
| `x-osm-list-join` | any string (default `", "`) | Separator between list items when a field holds a list (e.g. multiple visit dates). |
| `x-osm-list-sort` | `min` / `max` / `first` / `last` | Sets the cell's `data-sort-value` so column sorting picks one canonical key. `max` = most-recent visit drives the sort. |
| `x-osm-list-i18n` | `{ title: { <lang>: <string> } }` | Per-language overrides for `title`. Looked up by the article's `Lang` (or `DEFAULT_LANG`) — exact match first, then compatible same-script resources. Falls through to `title` when nothing matches. |

```yaml
hall:
  type: string
  title: 影廳                    # ← default-locale fallback
  x-osm-list-i18n:
    title:
      en: Hall
      ja: スクリーン
```

Precedence for column labels: `x-osm-list-i18n.title.<lang>` → `schema.title` → `OSM_LIST_FIELD_LABELS` → auto-derived from key.

These plugin-specific display hints do not change schema validation constraints. Scalar values render unchanged — `datetime.date` becomes ISO string, lists are joined per `x-osm-list-join`.

### Nested items: one place, many sub-rows

When a place has multiple variants that share its location — halls within a cinema, seasonal menus at a restaurant, courses on a trail — declare them under an `items:` list. The map renders **one pin per place** (items ignored); `{% place_list %}` flattens, emitting **one row per item** with parent fields cascaded in.

```yaml
# content/places/theaters/taiwan.yaml
vieshow-songren:
  name: 松仁威秀影城
  lat: 25.0368737
  lon: 121.5679503
  address: 台北市信義區松仁路58號10樓
  country: 臺灣
  city: 臺北
  district: 信義
  items:
    - hall: "6 廳（TITAN）"
      format: 一般 2D 廳
      recommended_rows: G
      hall_note: 一般 2D 最好的廳
    - hall: "2 廳"
      format: 一般 2D 廳
      recommended_rows: E
```

**Items contract:** `name`, `lat`, and `lon` are parent-only — they describe the shared place identity and location, so by definition every item under one parent shares them. If an item dict supplies any of these, the plugin warns and drops it (parent's value wins). Items wanting their own identity column should use a distinct field (`hall`, `course`, `season`, …); other fields cascade with item winning on collision, and `tags` are unioned (parent first).

The canonical pattern for rendering a tree where each parent is a section header:

```text
{% place_list theaters group_by="country,city,name" group_summary_at="country,city,name" %}
```

You get headers at country / city / theater, one row per hall under each theater header, and a single map pin per cinema on the map. Without `aggregate`, `group_by` only buckets rows for tree rendering — items keep their own values per row.

When `name` lives in `group_summary_at`, the plugin hoists it into the section header (alongside the place's 🗺️·📍 map links) and drops the `Name` column from data rows — each row is then identified by its item-specific field (`hall` etc.) instead of repeating the parent name.

JSON Schema for items lives under `properties.items.items.properties` — those `title` / `x-osm-list-*` hints are merged in alongside parent-level hints (item-level wins on collision).

### Multi-value fields

Want to record multiple visits to the same place? Just write the field as a list:

```yaml
- name: 某神社
  lat: 35.7
  lon: 139.7
  date: [2024-01-15, 2025-03-12]
```

The cell joins to `2024-01-15, 2025-03-12`. With `x-osm-list-sort: max`, sorting "Date" descending puts your most-recent visit first.

## Marker icons

Map pins can show an emoji instead of the default Leaflet pin. Precedence: per-place `icon:` field (override) > schema `x-osm-icon` category lookup > default pin.

`x-osm-icon` is a schema hint declared on the field you categorize places by (e.g. `category`); its value is a `{category value: emoji}` map:

```yaml
# content/places/food/_schema.yaml
$schema: "https://json-schema.org/draft/2020-12/schema"
type: object
properties:
  locations:
    type: array
    items:
      type: object
      properties:
        category:
          type: string
          x-osm-icon:
            ramen: 🍜
            cafe: ☕
```

```yaml
# content/places/food/kyoto.yaml
locations:
  - name: 拉麵店
    lat: 35.0
    lon: 135.7
    category: ramen   # → 🍜 pin, resolved via x-osm-icon
  - name: 特別的店
    lat: 35.1
    lon: 135.8
    category: ramen
    icon: 🎪           # per-place override wins over the schema mapping
```

If the place's category value isn't in the `x-osm-icon` map (or the place has neither an `icon:` field nor a matching category), the pin falls back to the default Leaflet marker — nothing crashes, nothing is required.

Marker icons only affect `{% place %}` map pins; `{% place_list %}` table columns are unaffected (`icon` is never auto-detected as a column).

## Place fields

| Field | Required | Notes |
| --- | --- | --- |
| `name` | ✅ | Popup title and map caption |
| `lat` | ✅ | Latitude (float) |
| `lon` | ✅ | Longitude (float) |
| `tags` | — | List — rendered as inline badges in the popup |
| `images` | — | List — rendered as a photo gallery in the popup |
| `urls` | — | List — rendered as links in the popup and list table; see below |
| `icon` | — | Emoji shown as the map marker for this place. Overrides any `x-osm-icon` schema mapping. See [Marker icons](#marker-icons). |
| `osm_type` | — | `node`, `way`, or `relation`. Together with `osm_id`, points the 🗺️ link at the OSM entity page. Not shown as a popup field or list column. |
| `osm_id` | — | The OpenStreetMap element id. Only used when `osm_type` is also set. Not shown as a popup field or list column. |
| *(any non-reserved field)* | — | Shown as `Key: Value` lines; identity, coordinate and internal fields are excluded |

OSM and Google Maps links are **always auto-generated** — from `lat`/`lon` by default, or from `osm_type`/`osm_id` for the 🗺️ link when both are set (see below).

When a place has both a valid `osm_type` (`node` / `way` / `relation`) and an `osm_id`, the 🗺️ link in the popup and the `place_list` table points at the OpenStreetMap entity page (`https://www.openstreetmap.org/<osm_type>/<osm_id>`) instead of a coordinate link. If either field is missing or `osm_type` is not one of the three valid values, the link falls back to the coordinate form. The 📍 Google Maps link is always coordinate-based.

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

Non-private place YAML files under `OSM_PLACES_ROOT` are converted to GeoJSON FeatureCollections at build time, mirroring the source directory structure. Invalid places are logged and omitted:

```text
content/places/japan/mygo.yaml   →   output/static/places/japan/mygo.geojson
content/places/taiwan.yml        →   output/static/places/taiwan.geojson
```

The GeoJSON files are standard [RFC 7946](https://datatracker.ietf.org/doc/html/rfc7946) and can be used with any GeoJSON-compatible tool (QGIS, Mapbox, etc.).

Feature properties may include an internal `_osm_icon` key — the computed final marker icon (see [Marker icons](#marker-icons)). It's derived at export time; you don't need to (and shouldn't) write this key directly in your YAML.

## Configuration

| Setting | Default | Description |
| --- | --- | --- |
| `OSM_SHORTCODE` | `"place"` | Shortcode tag name |
| `OSM_PLACES_ROOT` | `"places"` | Root folder for YAML files (relative to `PATH`) |
| `OSM_MAP_HEIGHT` | `"400px"` | Map height (any CSS length value) |
| `OSM_MAP_TILE` | OSM standard tiles | Leaflet tile URL template |
| `OSM_MAP_ATTRIBUTION` | OSM attribution HTML | Attribution string shown on the map |
| `OSM_STATIC_PREFIX` | `SITEURL + "/static"` | URL prefix for generated GeoJSON files |
| `OSM_LIST_SHORTCODE` | `"place_list"` | Shortcode name |
| `OSM_LIST_FIELDS` | `[]` (auto) | Ordered list of field keys to show as columns. When empty, all non-reserved fields found in the data are used. |
| `OSM_LIST_FIELD_LABELS` | `{}` | Override column header labels, e.g. `{"date": "Visited", "name": "Place"}` |
| `OSM_LIST_GROUP_COUNT_TEMPLATE` | auto-detected from `Lang` / `DEFAULT_LANG` | Format string for the per-group subtotal under `group_summary_at` headers. `{n}` is the place count. Built-in defaults: `"{n} 個地點"` (`zh*`), `"{n} 件"` (`ja*`), `"{n} places"` (other). Set to `""` to omit, or to any string to override. |
| `OSM_VALIDATE_SCHEMA_FILENAMES` | `["_schema.yaml", "_schema.yml", "_schema.json"]` | Filenames the validator looks for. Accepts a string or list. |
| `OSM_VALIDATE_STRICT` | `False` | Raise `RuntimeError` on validation failure instead of just logging warnings. |
| `OSM_DISABLE_MARKDOWN_PROTECTION` | `False` | Disable the Markdown preprocessor that shields `{% ... %}` shortcodes from the `attr_list` extension. Only set this if you have a conflicting extension at preprocessor priority 25 — without protection, two shortcodes on adjacent lines can be silently mangled by `attr_list` (bundled with `markdown.extensions.extra`). |

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
        id:       {type: string}
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

Automatic directory discovery and GeoJSON export skip files whose names start with `_` (e.g. `_schema.yaml` and `_common.yaml`). An explicitly named file can still be loaded by the shortcode resolver, but no GeoJSON is exported for it; keep actual place data in non-private files.

Validation checks the source document before file defaults or nested items are expanded. A schema must describe that source shape, including where shared fields are declared.

Unquoted dates (`date: 2026-02-22`) are normalized to ISO 8601 strings before validation, so schemas can use `type: string, format: date` even though PyYAML parses them as `datetime.date`.

By default, validation failures are logged as warnings. Set `OSM_VALIDATE_STRICT = True` to raise a `RuntimeError` and fail the build instead.

### Sharing definitions across schemas with `$ref`

Most place schemas share a large common core — `name`, `lat`, `lon`, `city`, `country`, `date`, `tags`, `urls`, and so on — with only a handful of fields differing per category. Rather than repeating that core in every `_schema.yaml`, factor it into one shared file and reference it with `$ref`:

```yaml
# content/places/_common.yaml — not a schema by itself, just a $defs library
$defs:
  base_location:
    type: object
    required: [name, lat, lon]
    properties:
      name:    {type: string, minLength: 1, title: "名稱"}
      lat:     {type: number, minimum: -90,  maximum: 90}
      lon:     {type: number, minimum: -180, maximum: 180}
      country: {type: string}
      city:    {type: string}
      date:    {type: string, format: date}
      tags:    {type: array, items: {type: string}}
```

```yaml
# content/places/pilgrimage/_schema.yaml
$schema: "https://json-schema.org/draft/2020-12/schema"
type: object
required: [anime, locations]
properties:
  anime: {type: string}
  locations:
    type: array
    items:
      allOf:
        - $ref: "../_common.yaml#/$defs/base_location"
      properties:
        # fields specific to pilgrimage locations
        category: {type: string, title: "分類"}
        notes:    {type: string}
```

Both reference forms are supported:

- **Same-file**: `$ref: "#/$defs/base_location"` — resolves within the current schema document.
- **Cross-file**: `$ref: "../_common.yaml#/$defs/base_location"` — the file part is resolved **relative to the directory of the schema file containing the `$ref`**, not the place YAML being validated.

`$ref` can appear inside `allOf` as above. With the draft 2020-12 schema used in these examples, it can also appear alongside `properties`:

```yaml
items:
  $ref: "../_common.yaml#/$defs/base_location"
  properties:
    category: {type: string}
```

For **display hints**, referenced properties form the shared layer, and locally declared properties take precedence. For example, a category schema can override the shared `name` title. The hint collector walks `allOf`/`anyOf`/`oneOf`; it does not select a validation branch based on each record.

For **validation**, the schema composition rules still apply. `allOf` requires every subschema to pass; a local property does not replace or relax a constraint from the referenced schema.

Reference resolution is used in both paths:

- **Display hints** (`title`, `x-osm-list-hidden`, `x-osm-list-sort`, `x-osm-list-i18n`, `x-osm-icon`) collected for `place_list` columns — resolved fully, including across files.
- **Validation** — the `jsonschema` validator needs `$ref` targets to actually resolve; the plugin wires up a [`referencing`](https://github.com/python-jsonschema/referencing) registry (a transitive dependency of `jsonschema>=4.18`, so no extra install is needed) so cross-file relative refs work the same way here as they do for display hints.

The display-hint collector warns and skips missing targets, unresolved pointers and cycles. Validation separately reports unresolved references as errors; with `OSM_VALIDATE_STRICT = True`, those errors fail the build. Display-hint recovery does not guarantee that an invalid or cyclic schema can be validated successfully.

## Deep linking

Link directly to a specific place by appending its `id` or `name` as a URL hash:

```text
https://example.com/my-post.html#normal_park
https://example.com/my-post.html#豊島区立南池袋第二公園
```

The map will pan to the marker and open its popup automatically. When marker clustering is enabled, the cluster is expanded first.

### Table row anchors

Each row rendered by `{% place_list %}` carries an `id="osm-place-<slug>"` anchor, where `<slug>` is derived from the place's `id` field (preferred) or a slug of its `name`. This lets external links jump straight to a row:

```text
https://example.com/my-post.html#osm-place-normal_park
```

When a map and a table for the same place coexist on the page, the marker's popup includes a "View in table" link that scrolls to the matching row, and clicking it also opens the popup on the map (via `hashchange`).

Rows from `items:` expansion (e.g. one cinema with multiple halls) carry a suffix derived from the first item-distinguishing field, so anchors stay readable — `osm-place-cinema-h1`, `osm-place-cinema-h2`, … rather than `cinema-2`, `cinema-3`. Each expanded row also carries `data-osm-parent-slug="<parent>"`, so a marker popup that only knows the parent slug can still route to one of the rows. Genuine duplicate slugs fall back to a numeric `-2`, `-3`, … suffix.

## Tag filtering

Tag badges are clickable in both maps and tables.

**In `{% place_list %}` tables:** Clicking a tag filters the table to show only rows with that tag. A filter chip appears next to the row count — click it (or click the same tag again) to clear the filter.

**In `{% place %}` maps:** A tag bar appears below the map when places have tags. Click a tag to show only markers with that tag; click again to show all. The map automatically re-fits to the visible markers.

## Marker clustering

[Leaflet.markercluster](https://github.com/Leaflet/Leaflet.markercluster) is automatically loaded from CDN at runtime. Nearby markers are grouped into clusters that expand on click/zoom. No extra setup is needed.

## Customising the CSS

Map and popup styles expose CSS custom properties on `:root`. Override them in your own stylesheet (loaded after `osm-map.css`):

```css
/* Remove rounded corners and shadow */
.osm-map-block {
  --osm-radius: 0;
  --osm-shadow: none;
}
```

Set `OSM_MAP_HEIGHT = "300px"` in `pelicanconf.py` to change map height globally. Generated maps set `--osm-map-height` inline, so changing that variable on `:root` alone does not override it.

### Common custom properties

| Property | Default | Controls |
| --- | --- | --- |
| `--osm-map-height` | `400px` | Map canvas height; set inline from `OSM_MAP_HEIGHT` |
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

Both `{% place %}` and `{% place_list %}` accept `lang="ja"`. Language priority
is shortcode → article/page `Lang` → this build's `DEFAULT_LANG` → English.
The selected language applies to table labels/counts, captions, maps, popups,
layer controls and photo lightboxes, including multiple languages on one page.

English, Traditional Chinese and Japanese catalogs share tabular's locale and
plural helpers. Matching preserves scripts: `zh-Hans` falls back to English,
while `zh-TW`, `zh-Hant` and legacy bare `zh` use Traditional Chinese.

`OSM_MESSAGES` configures messages at build time. Existing `window.OSM_I18N`
overrides and function-valued `placeCount` remain supported. Schema labels and
`OSM_LIST_FIELD_LABELS` apply to both table headers and popup fields.

`OSM_TRANSLATIONS` optionally projects allowlisted place text (such as `name`
and `note`) into the selected language. IDs, coordinates, source-name fragments
and photo identities stay stable. Translated GeoJSON uses separate locale paths.

See [configuration and migration](docs/i18n.md).

## License

MIT © Wei Lee


## Shared table core

OSM depends on `pelican-tabular>=0.9.0` for component localization, table grouping, aggregation,
sorting, tag filtering, group collapse and table CSS. OSM retains place data,
schema hints, map links and photo lightboxes. Both plugins may be enabled;
shared assets are registered once, including when only OSM is enabled.

Keep loading the existing `osm-map.css` and `osm-map.js` URLs. At build time,
OSM includes tabular's shared table engine and legacy table CSS in these files.
Existing themes and plain tables need no new assets or configuration. There is
only one maintained table engine; it is guarded against duplicate initialization
when a page also selects an optional tabular database view. Database controls
are excluded from the OSM bundle.

Install development dependencies with `uv sync --locked`. The lockfile uses
tabular 0.9.0 from PyPI, which provides the shared i18n API; a sibling source
checkout is not required.

CI checks out the browser fixtures at the locked tabular version's tag and
runs them with OSM's environment, so rendering and assets use the installed
release rather than the fixture checkout's Python source. The release workflow
uses `uv sync --locked --no-sources --no-dev` to verify the registry dependency
and lockfile before publishing.
