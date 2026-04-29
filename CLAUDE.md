# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Common commands

All tasks run via `uv` + `poe` (poethepoet). Tasks are defined in `pyproject.toml` under `[tool.poe.tasks]`.

```bash
uv run poe format         # ruff check --fix && ruff format
uv run poe lint           # ruff check && mypy
uv run poe test           # pytest -n auto --dist=loadfile
uv run poe cover          # test + coverage report
uv run poe check-commit   # commitizen check on commits since origin/main
uv run poe all            # format → lint → check-commit → cover
uv run poe ci             # check-commit → prek run --all-files → cover
uv run poe setup-pre-commit  # install pre-commit hooks via prek
```

Single test: `uv run pytest tests/test_osm.py::TestClassName::test_name`. Tests run in parallel with `--dist=loadfile`, so file-level fixtures stay on the same worker.

Releases: `cz bump` updates the version in `pyproject.toml` and regenerates `CHANGELOG.md` from conventional-commit history. Don't hand-edit `CHANGELOG.md` — it's regenerated.

## Architecture

The plugin is a single-file implementation: `src/pelican/plugins/osm/osm.py`. Bundled static assets (`static/css/`, `static/js/`) are copied into Pelican's output at build time.

### Signal flow

`register()` connects four Pelican signals — order matters because each stage depends on state set up by the previous one:

1. `signals.initialized` → `_init_resolver`: reads `OSM_*` settings, builds the `PlaceResolver`, optionally registers the schema validator (when `_schema.yaml` files exist under `OSM_PLACES_ROOT` and `jsonschema` is installed), and registers the Markdown preprocessor extension.
2. `signals.content_object_init` → `_process_article`: per-article HTML-stage substitution. Walks the rendered HTML and replaces shortcode placeholders with the generated `<div>` markup.
3. `signals.finalized` → `_copy_static`: copies bundled CSS/JS to `output/static/pelican_osm/`.
4. `signals.finalized` → `_export_geojson`: writes one `.geojson` per source YAML to `output/<OSM_STATIC_PREFIX>/<places_root>/...`.

### Two-stage shortcode handling

Shortcodes are processed in two stages because Markdown's `attr_list` extension (bundled with `markdown.extensions.extra`) treats `{...}` as attribute syntax and would mangle `{% place ... %}` and `{% place_list ... %}`:

1. **Markdown preprocessor** (`_ShortcodePreserver`, priority **25**): matches `{% (place|place_list) ... %}` and pushes each match into `md.htmlStash` so the body survives Markdown conversion verbatim.
   - The priority **must** stay between `NormalizeWhitespace` (30) and `html_block` (20). NormalizeWhitespace strips STX/ETX control chars from raw input; running before it would erase the stash placeholder's control chars and corrupt every shortcode. Running after `html_block` would let the HTML extension re-tokenize the placeholders.
   - Auto-registers in `_init_resolver`. Set `OSM_DISABLE_MARKDOWN_PROTECTION = True` in `pelicanconf.py` only if a downstream extension claims priority 25.
2. **Post-Markdown HTML substitution** (`_process_content`, called from `_process_article`): regex-finds the same `{% ... %}` patterns in the final HTML (now standing alone after `htmlStash` restoration) and swaps each for the rendered map/list `<div>`.

The shortcode regex used by `_process_content` and the one built by `_build_shortcode_pattern` (used by the preprocessor) target the same shortcode names — when adding a new shortcode, update both call sites.

### Data pipeline

YAML → GeoJSON happens once per build at `finalized`. The browser fetches the GeoJSON at runtime via the bundled JS:

```
content/places/japan/mygo.yaml  →  output/static/places/japan/mygo.geojson
                                          ↑
                          osm-map.js fetches via Leaflet
```

`_load_yaml_file` accepts three YAML shapes (see README → "YAML format"):
- `locations`-based (preferred): top-level keys are file-level defaults; `locations:` holds the place list.
- Dict of places: each non-`defaults` top-level key is a place id usable in `#fragment` references.
- Bare list: optional leading `{defaults: {...}}` item sets shared attributes.

Files starting with `_` (e.g. `_schema.yaml`) are skipped by both the resolver and the GeoJSON exporter.

`PlaceResolver` maps a shortcode spec (file path, folder, `#id` fragment, comma-separated list) to a concrete set of places. The same resolver is used by both shortcodes; `place` emits a map, `place_list` emits a sortable/groupable table.

### Schema validation (opt-in by file presence)

Validation runs only when a `_schema.yaml` (or `.yml` / `.json`) is found under `OSM_PLACES_ROOT`. Lookup is **nearest-ancestor**: walk up the directory tree from each YAML, validate against the closest schema. The same schema also drives `place_list` column hints (`title`, `x-osm-list-hidden`, `x-osm-list-join`, `x-osm-list-sort`, `x-osm-list-i18n`). If schema files exist but `jsonschema` isn't installed, the build logs a warning and skips validation. `OSM_VALIDATE_STRICT = True` flips warnings into a `RuntimeError`.

## Conventions

- **Conventional commits** are required — `cz check` runs in pre-commit (and via `poe check-commit`). `cz bump` reads commit history to choose the version bump and write the changelog entry.
- **Type checking**: mypy runs on `src` and `tests` with `disallow_untyped_decorators`, `warn_return_any`, etc. The `markdown` package is typed via `types-markdown`; the `pelican.*` modules are not, so missing imports are ignored for that namespace only.
- **Pre-commit**: `prek` (a faster pre-commit drop-in) is the runner. Hooks live in `.pre-commit-config.yaml` and include `commitizen`, `codespell`, `blacken-docs`, `taplo`, plus local `poe format` / `poe lint` entries.
