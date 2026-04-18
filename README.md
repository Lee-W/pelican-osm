# pelican-osm

**pelican-osm** is a Pelican plugin that embeds interactive [OpenStreetMap](https://www.openstreetmap.org/) maps into your articles using a simple `{% place %}` shortcode. It integrates with [Leaflet.js](https://leafletjs.com/) and loads place data from YAML files.

## Features

- `{% place %}` shortcode renders an independent interactive map per shortcode
- `{% place_list %}` shortcode renders a table of places from one or more YAML specs
- YAML files converted to GeoJSON at build time — JS fetches them at runtime
- Flexible spec syntax: single file, single place via `#id`, entire folder, or comma-separated mix
- File-level metadata (anime title, tags, country…) applied as defaults to every place in the file
- Per-place popup with auto-generated OSM and Google Maps links
- `tags` list rendered as inline badges in the popup
- All extra YAML fields displayed in the popup automatically
- Fully class-based CSS — every visual detail overridable via custom properties
- i18n via `window.OSM_I18N`

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
    photos: []
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

```yaml
{% place_list japan/tokyo %}
{% place_list japan/tokyo, japan/kyoto %}
```

> **Note:** Fragment (`#`) syntax filters which places appear in the popup, but the map still fetches the full GeoJSON file. A future version may support per-feature filtering.

## Place fields

| Field | Required | Notes |
| --- | --- | --- |
| `name` | ✅ | Popup title and map caption |
| `lat` | ✅ | Latitude (float) |
| `lon` | ✅ | Longitude (float) |
| `tags` | — | List — rendered as inline badges in the popup |
| `photos` | — | List — rendered as a gallery of images |
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

All user-visible strings default to English. Set `window.OSM_I18N` **before** loading `osm-map.js`:

```html
<script>
window.OSM_I18N = {
  // Map link labels (defaults: "OSM", "Google")
  osmLink:      "OSM",
  googleLink:   "Google",

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

## License

MIT © Wei Lee
