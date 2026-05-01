## 0.12.0 (2026-05-01)

### Feat

- add x-osm-map-layer annotation for per-layer map filtering

### Fix

- refine map layer filter UX and fix popup behaviour

## 0.11.0 (2026-04-30)

### Feat

- image indicator column in place_list and multi-date support

### Fix

- date sorting

## 0.10.0 (2026-04-29)

### Feat

- auto-translate group_summary_at count line via article Lang

### Fix

- protect {% ... %} shortcodes from Markdown attr_list extension
- resolve per-place schema hints for all YAML shapes; add i18n column titles

## 0.9.0 (2026-04-29)

### Feat

- items syntax for parent/sub-row modeling

## 0.8.0 (2026-04-28)

### Feat

- nested group_summary_at, schema-driven list cells
- dark-mode + i18n for place_list group summary header
- add group_by, aggregate, group_summary_at kwargs to place_list
- shortcode kwarg parsing and tag append-merge in YAML loader
- add opt-in JSON Schema validation for place YAML

### Fix

- make dark mode follow host theme's class-based toggle

## 0.7.0 (2026-04-25)

### Feat

- add reset view, map tag filtering, lazy init, deep linking, and more
- add marker clustering, loading spinner, tag filtering, and mobile UX fixes

## 0.6.0 (2026-04-19)

### Feat

- use sticky table and add osm, google map link to table

## 0.5.1 (2026-04-18)

### Fix

- correct photos→images field name, update docs for urls rename
- rename url → urls, fix {filename} resolution, improve link display

## 0.5.0 (2026-04-18)

### Feat

- add place_list shortcode and url field support in popups

## 0.4.2 (2026-03-26)

### Fix

- tooltip scrolling

## 0.4.1 (2026-03-26)

### Fix

- fix fullscreen behavior

## 0.4.0 (2026-03-25)

### Feat

- add full screen and images support

## 0.3.1 (2026-03-14)

### Fix

- use # to specify single location not working

## 0.3.0 (2026-03-14)

### Feat

- rewrite the whole structure and add multiple file support

## 0.2.0 (2026-02-17)

### Feat

- initialize pelican-osm for generate OSM HTML
