"""pelican-osm plugin: embed OpenStreetMap maps via {% place %} shortcodes."""

from __future__ import annotations

import datetime
import html
import json
import logging
import re
import shlex
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

import yaml
from pelican.contents import Article, Page

from pelican import signals  # type: ignore[attr-defined]

try:
    import jsonschema

    _HAS_JSONSCHEMA = True
except ImportError:
    jsonschema = None  # type: ignore[assignment]
    _HAS_JSONSCHEMA = False

try:
    import markdown as _markdown

    _HAS_MARKDOWN = True
except ImportError:
    _markdown = None  # type: ignore[assignment]
    _HAS_MARKDOWN = False

log = logging.getLogger(__name__)

DEFAULT_SHORTCODE = "place"
DEFAULT_LIST_SHORTCODE = "place_list"
DEFAULT_PLACES_ROOT = "places"  # relative to Pelican's PATH (content dir)
DEFAULT_MAP_HEIGHT = "400px"
DEFAULT_MAP_TILE = "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
DEFAULT_MAP_ATTRIBUTION = (
    '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> '
    "contributors"
)
DEFAULT_SCHEMA_FILENAMES = ("_schema.yaml", "_schema.yml", "_schema.json")
# Template for the count line under each group_summary_at header. ``{n}`` is
# replaced with the number of original places in the group. Override via
# ``OSM_LIST_GROUP_COUNT_TEMPLATE`` for translation; set to ``""`` to suppress.
DEFAULT_GROUP_COUNT_TEMPLATE = "{n} places"

# Built-in count templates keyed by BCP-47 primary subtag, mirroring the JS
# i18n defaults in static/js/osm-map.js. Picked automatically based on the
# article's ``Lang:`` (or ``DEFAULT_LANG``) when ``OSM_LIST_GROUP_COUNT_TEMPLATE``
# isn't set explicitly.
BUILTIN_GROUP_COUNT_TEMPLATES = {
    "zh": "{n} 個地點",
    "ja": "{n} 件",
}

# Fields never shown as regular columns in the list table.
# ``_places`` is the synthesised back-reference attached to a row by
# ``_collapse_places`` so summary rendering can count originals.
# ``items`` is the nested sub-row container; flattened away by
# ``_expand_items`` before rendering, so it never appears as a column.
_LIST_RESERVED = frozenset(
    [
        "name",
        "lat",
        "lon",
        "id",
        "images",
        "urls",
        "tags",
        "_places",
        "_item_slug_parts",
        "items",
    ]
)


def _slugify(value: str) -> str:
    """Slug for an HTML id / URL fragment. Keeps letter chars (incl. CJK)
    and digits, swaps whitespace for hyphens, drops everything else."""
    s = re.sub(r"\s+", "-", str(value).strip())
    out = [ch for ch in s if ch == "-" or ch.isalnum()]
    return "".join(out).lower() or "group"


_SAFE_URL_SCHEMES = re.compile(r"^(?:https?:|mailto:|tel:|/|\.|#|\?)", re.IGNORECASE)


def _safe_url(value: Any) -> str:
    """Return ``value`` if its URL scheme can't execute script, else ``"#"``.

    Blocks ``javascript:``, ``data:``, ``vbscript:`` and friends. Schemeless
    relative URLs (``/foo``, ``./bar``, ``#anchor``, ``?q=1``) are allowed.
    """
    if value is None:
        return "#"
    s = str(value).strip()
    if not s:
        return "#"
    return s if _SAFE_URL_SCHEMES.match(s) else "#"


def _place_anchor_slug(place: dict[str, Any]) -> str:
    """Stable slug for a place's row/marker anchor.

    Prefers ``id`` over ``name``. Returns an empty string when neither
    yields a usable slug, so callers can skip emitting an anchor entirely.
    Page-scoped uniqueness (e.g. items expansion or duplicate names) is the
    caller's responsibility — this helper only computes the base slug.
    """
    raw = place.get("id") or place.get("name")
    if raw is None or not str(raw).strip():
        return ""
    return _slugify(str(raw))


def _is_place_yaml(path: Path) -> bool:
    """True if ``path`` is a place YAML (not a schema/private underscore file)."""
    return path.suffix in (".yml", ".yaml") and not path.name.startswith("_")


def _merge_place(
    file_defaults: dict[str, Any], per_place: dict[str, Any]
) -> dict[str, Any]:
    """Merge file-level defaults with a per-place dict.

    Most fields use override semantics (per-place wins). The ``tags`` field is
    a special case: file-level and per-place tags are merged into a deduplicated
    list preserving order (file tags first). This lets a YAML file declare e.g.
    ``tags: [動畫]`` once at the top and add per-location status tags like
    ``["已歇業"]`` without losing the file-level tag.
    """
    result = {**file_defaults, **per_place}
    file_tags = file_defaults.get("tags") or []
    place_tags = per_place.get("tags") or []
    if file_tags or place_tags:
        merged: list[Any] = []
        seen: set[Any] = set()
        for tag in (*file_tags, *place_tags):
            if tag not in seen:
                merged.append(tag)
                seen.add(tag)
        result["tags"] = merged
    return result


_ITEM_FORBIDDEN_FIELDS = ("name", "lat", "lon")


def _expand_items(places: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flatten places by expanding nested ``items`` lists into per-item rows.

    A place may carry an ``items: [...]`` list of sub-rows that share the
    parent's location/context. For each parent with items, this function emits
    one row per item where parent fields cascade (item wins on collision) and
    ``tags`` are unioned. Places without ``items`` pass through unchanged
    (with the ``items`` key dropped if it was present but empty/invalid).

    Item dicts must NOT carry ``name``, ``lat``, or ``lon``: those describe
    the *parent* place's identity and location, which by definition is shared
    by all items. If an item supplies one of these, we warn and drop it
    (parent's value wins). Items wanting their own identity column should use
    a distinct field (e.g. ``hall``, ``course``, ``season``).

    The map / GeoJSON path does **not** call this — pins remain at the
    parent level (one pin per place, regardless of item count).
    """
    result: list[dict[str, Any]] = []
    for place in places:
        items = place.get("items")
        parent_fields = {k: v for k, v in place.items() if k != "items"}
        if not isinstance(items, list) or not items:
            result.append(parent_fields)
            continue
        parent_id = place.get("id") or place.get("name") or "<unknown>"
        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            cleaned = {k: v for k, v in item.items()}
            for field in _ITEM_FORBIDDEN_FIELDS:
                if field in cleaned:
                    log.warning(
                        "pelican-osm: item %s[%d] has forbidden '%s' field; "
                        "dropping (lat/lon/name belong to the parent place)",
                        parent_id,
                        idx,
                        field,
                    )
                    del cleaned[field]
            merged = _merge_place(parent_fields, cleaned)
            # Item-distinguishing values (first non-empty) drive the row's
            # anchor suffix downstream, so anchors read as `cinema-h1` rather
            # than the opaque `cinema-2` produced by plain collision dedup.
            slug_parts = [
                str(cleaned[k]) for k in cleaned if cleaned.get(k) not in (None, "", [])
            ]
            if slug_parts:
                merged["_item_slug_parts"] = slug_parts
            result.append(merged)
    return result


def _parse_shortcode_args(raw: str) -> tuple[list[str], dict[str, str]]:
    """Parse a shortcode argument string into (positional_specs, kwargs).

    Backwards compatible: when no ``=`` appears in ``raw``, falls back to the
    legacy comma-split behavior so existing shortcodes like
    ``{% place japan/tamako.yml, taiwan.yml %}`` are unchanged.

    With kwargs, uses shlex so quoted values can contain commas/spaces::

        pilgrimage group_by="anime,country,city" sort=date:asc
        japan/tamako.yml, taiwan.yml group_by=anime
    """
    if "=" not in raw:
        return ([s.strip() for s in raw.split(",") if s.strip()], {})

    positional: list[str] = []
    kwargs: dict[str, str] = {}
    for tok in shlex.split(raw):
        if "=" in tok:
            key, _, value = tok.partition("=")
            kwargs[key.strip()] = value.strip()
        else:
            for s in tok.split(","):
                s = s.strip()
                if s:
                    positional.append(s)
    return positional, kwargs


def _parse_csv_kwarg(raw: str) -> list[str]:
    """Parse a comma-separated kwarg value into a list of stripped tokens."""
    return [s.strip() for s in raw.split(",") if s.strip()]


def _parse_aggregate_kwarg(raw: str) -> dict[str, str]:
    """Parse an ``aggregate`` kwarg value into a ``{field: op}`` mapping.

    Format: ``field:op[,field:op]*`` — e.g., ``date:year`` or
    ``date:year,visits:sum``. Tokens without ``:`` are skipped with no error.
    """
    spec: dict[str, str] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        field, _, op = part.partition(":")
        spec[field.strip()] = op.strip()
    return spec


def _extract_year(value: Any) -> int | None:
    """Best-effort extraction of a year integer from a date-like value."""
    if isinstance(value, datetime.datetime):
        return value.year
    if isinstance(value, datetime.date):
        return value.year
    if isinstance(value, int):
        return value if 1000 <= value <= 9999 else None
    if isinstance(value, str) and len(value) >= 4 and value[:4].isdigit():
        return int(value[:4])
    if isinstance(value, list):
        for item in value:
            year = _extract_year(item)
            if year is not None:
                return year
    return None


def _extract_years(value: Any) -> list[int]:
    """Extract all years from a date or list of dates."""
    if isinstance(value, list):
        years: list[int] = []
        for item in value:
            year = _extract_year(item)
            if year is not None:
                years.append(year)
        return years
    year = _extract_year(value)
    return [year] if year is not None else []


def _aggregate_field(op: str, field: str, places: list[dict[str, Any]]) -> Any:
    """Aggregate ``field`` across ``places`` according to ``op``.

    Currently supports:
      * ``year`` — collect unique years from a date-like field, sorted
        ascending, comma-joined as a string. Returns ``""`` if no place has
        a usable year. Supports list-valued date fields.
    """
    if op == "year":
        seen: set[int] = set()
        ordered: list[int] = []
        for p in places:
            for year in _extract_years(p.get(field)):
                if year not in seen:
                    seen.add(year)
                    ordered.append(year)
        ordered.sort()
        return ", ".join(str(y) for y in ordered)
    log.warning("pelican-osm: unknown aggregate op %r for field %r", op, field)
    return ""


def _collapse_places(
    places: list[dict[str, Any]],
    group_by: list[str],
    aggregate: dict[str, str],
) -> list[dict[str, Any]]:
    """Group places by ``group_by`` for tree-style rendering.

    Two modes, controlled by whether ``aggregate`` is non-empty:

    * **No aggregate** (the common case): each input place becomes one
      output row. Rows are merely re-ordered so that everything sharing
      a group-key tuple is contiguous, preserving first-appearance order
      across keys and original order within a key. Each row carries
      ``_places: [self]`` so summary count math still adds up.

    * **With aggregate** (SQL-style collapse): all places sharing a group
      key merge into a single row. Aggregate fields are computed via
      ``_aggregate_field``; for other fields the first non-empty value
      wins; ``tags`` are unioned (parent first). The merged row's
      ``_places`` lists every collapsed place.

    The first mode is what most ``group_by`` users want — they're asking
    for a visual tree, not for set-style aggregation. Auto-merging without
    an explicit ``aggregate`` directive silently destroys per-row data
    (e.g. nested ``items`` rows would collapse into one).
    """
    order: list[tuple] = []
    if not aggregate:
        buckets: dict[tuple, list[dict[str, Any]]] = {}
        for place in places:
            key = tuple(place.get(g, "") for g in group_by)
            if key not in buckets:
                buckets[key] = []
                order.append(key)
            buckets[key].append(place)
        return [{**p, "_places": [p]} for key in order for p in buckets[key]]

    rows: dict[tuple, dict[str, Any]] = {}
    for place in places:
        key = tuple(place.get(g, "") for g in group_by)
        if key not in rows:
            rows[key] = {**place, "_places": [place]}
            order.append(key)
            continue

        existing = rows[key]
        existing["_places"].append(place)
        for k, v in place.items():
            if k == "tags" or k in aggregate:
                continue
            if not existing.get(k) and v:
                existing[k] = v

        merged_tags: list[Any] = list(existing.get("tags") or [])
        seen_tags = set(merged_tags)
        for t in place.get("tags") or []:
            if t not in seen_tags:
                merged_tags.append(t)
                seen_tags.add(t)
        if merged_tags:
            existing["tags"] = merged_tags

    result: list[dict[str, Any]] = []
    for key in order:
        row = rows[key]
        for field, op in aggregate.items():
            row[field] = _aggregate_field(op, field, row["_places"])
        result.append(row)
    return result


def _load_yaml_file(path: Path) -> list[dict[str, Any]]:
    """Load a YAML file and always return a list of place dicts.

    Three formats are supported, in priority order:

    1. **locations-based** (preferred for real use):
       Top-level keys other than ``locations`` become file-level defaults
       applied to every place. Per-place values override file-level values,
       except for ``tags``, which are unioned (see ``_merge_place``)::

           anime: BanG Dream! It's MyGO!!!!!
           tags: [動畫]
           locations:
             - name: 豊島区立南池袋第二公園
               lat: 35.7225
               lon: 139.7170
               visited: true
               date: 2026-02-22

    2. **dict-of-places** (convenient for hand-authored files):
       The reserved ``defaults`` key spreads shared attributes::

           defaults:
             country: Japan
           ueno_park:
             name: 上野公園
             lat: 35.7142
             lon: 139.7742

    3. **bare list** (backwards compatibility)::

           - name: 上野公園
             lat: 35.7142
             lon: 139.7742
    """
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if data is None:
        return []

    # ── Format 1: locations key ───────────────────────────────────────────
    if isinstance(data, dict) and "locations" in data:
        locations = data.get("locations") or []
        if not isinstance(locations, list):
            log.warning("'locations' in %s is not a list", path)
            return []

        # Everything except 'locations' becomes file-level defaults
        file_defaults = {k: v for k, v in data.items() if k != "locations"}

        places = []
        for item in locations:
            if not isinstance(item, dict):
                continue
            places.append(_merge_place(file_defaults, item))
        return places

    # ── Format 2: dict-of-places ──────────────────────────────────────────
    if isinstance(data, dict):
        file_defaults = {}
        if "defaults" in data and isinstance(data["defaults"], dict):
            file_defaults = data["defaults"]

        places = []
        for key, val in data.items():
            if key == "defaults":
                continue
            if isinstance(val, dict):
                entry = _merge_place(file_defaults, val)
                entry.setdefault("id", key)
                places.append(entry)
        return places

    # ── Format 3: bare list ───────────────────────────────────────────────
    if isinstance(data, list):
        file_defaults = {}
        places = []
        for item in data:
            if not isinstance(item, dict):
                continue
            if tuple(item.keys()) == ("defaults",) and isinstance(
                item["defaults"], dict
            ):
                file_defaults = item["defaults"]
                continue
            places.append(_merge_place(file_defaults, item))
        return places

    log.warning("Unexpected YAML structure in %s", path)
    return []


def _validate_place(place: dict[str, Any], source: str) -> bool:
    """Check that required fields exist."""
    for field in ("name", "lat", "lon"):
        if field not in place:
            log.warning(
                "Place missing '%s' in %s: %s", field, source, list(place.keys())
            )
            return False
    return True


def _schema_filenames(settings: dict[str, Any]) -> list[str]:
    val = settings.get("OSM_VALIDATE_SCHEMA_FILENAMES")
    if val is None:
        return list(DEFAULT_SCHEMA_FILENAMES)
    if isinstance(val, str):
        return [val]
    return list(val)


def _find_schema_for(yaml_path: Path, root: Path, names: list[str]) -> Path | None:
    """Walk up from ``yaml_path``'s directory to ``root``, return first matching schema.

    Returns ``None`` if no schema is found at any ancestor up to and including ``root``.
    """
    try:
        root_resolved = root.resolve()
        current = yaml_path.parent.resolve()
    except OSError:
        return None
    while True:
        for name in names:
            candidate = current / name
            if candidate.is_file():
                return candidate
        if current == root_resolved:
            return None
        parent = current.parent
        if parent == current:
            return None
        current = parent


def _coerce_for_validation(value: Any) -> Any:
    """Recursively coerce non-JSON types to JSON-friendly equivalents.

    PyYAML parses unquoted dates as ``datetime.date``; JSON Schema validates JSON,
    so we normalize dates/datetimes to ISO 8601 strings. Schemas can then use
    ``type: string`` with ``format: date``.
    """
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _coerce_for_validation(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_coerce_for_validation(v) for v in value]
    return value


def _validate_yaml_files(root: Path, settings: dict[str, Any]) -> None:
    """Validate every place YAML under ``root`` against any nearest-ancestor schema.

    Validation is presence-driven: files with no ancestor schema are skipped.
    Errors are logged. When ``OSM_VALIDATE_STRICT`` is true, a ``RuntimeError``
    is raised after the full sweep so authors see all problems at once.
    """
    schema_names = _schema_filenames(settings)
    strict = bool(settings.get("OSM_VALIDATE_STRICT", False))

    pairs: list[tuple[Path, Path]] = []
    for yaml_path in root.rglob("*"):
        if not _is_place_yaml(yaml_path):
            continue
        schema_path = _find_schema_for(yaml_path, root, schema_names)
        if schema_path is not None:
            pairs.append((yaml_path, schema_path))

    if not pairs:
        return

    if not _HAS_JSONSCHEMA:
        log.warning(
            "pelican-osm: schema files found but `jsonschema` is not installed; "
            "skipping validation. Install with `pip install pelican-osm[validate]`."
        )
        return

    schema_cache: dict[Path, Any] = {}
    errors: list[str] = []

    for yaml_path, schema_path in pairs:
        if schema_path not in schema_cache:
            try:
                with open(schema_path, encoding="utf-8") as f:
                    schema_cache[schema_path] = yaml.safe_load(f)
            except (OSError, yaml.YAMLError) as e:
                log.error("pelican-osm: cannot load schema %s: %s", schema_path, e)
                schema_cache[schema_path] = None
        schema = schema_cache[schema_path]
        if schema is None:
            continue

        try:
            with open(yaml_path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except (OSError, yaml.YAMLError) as e:
            log.error("pelican-osm: cannot load %s: %s", yaml_path, e)
            continue
        if data is None:
            continue

        try:
            jsonschema.validate(
                _coerce_for_validation(data),
                schema,
                format_checker=jsonschema.FormatChecker(),
            )
        except jsonschema.exceptions.SchemaError as e:
            msg = f"invalid schema {schema_path}: {e.message}"
            log.error("pelican-osm: %s", msg)
            errors.append(msg)
        except jsonschema.exceptions.ValidationError as e:
            path_str = "/".join(str(p) for p in e.absolute_path) or "<root>"
            msg = f"{yaml_path}: {e.message} (at {path_str})"
            log.warning("pelican-osm schema validation: %s", msg)
            errors.append(msg)

    if errors and strict:
        raise RuntimeError(
            f"pelican-osm: {len(errors)} schema validation error(s); "
            "see logs for details."
        )


def _walk_schema_properties(schema: Any, out: dict[str, Any]) -> None:
    """Walk a JSON schema and collect every per-property entry that could
    describe a place field, regardless of which YAML shape the schema models.

    Handles all three loader formats:
      * **locations-based**: ``properties.locations.items.properties.*``
      * **dict-of-places**: ``additionalProperties.properties.*``
      * **bare list / nested items**: ``items.properties.*`` and
        ``properties.items.items.properties.*``

    Outer fields are collected first, then deeper fields override them — so an
    item-level ``title`` or ``x-osm-list-hidden`` wins over a parent-level
    entry of the same name, matching the row-merge precedence in
    ``_expand_items``.
    """
    if not isinstance(schema, dict):
        return

    props = schema.get("properties")
    if isinstance(props, dict):
        for k, v in props.items():
            if isinstance(v, dict):
                out[k] = v
        for v in props.values():
            _walk_schema_properties(v, out)

    item = schema.get("items")
    if isinstance(item, dict):
        _walk_schema_properties(item, out)

    ap = schema.get("additionalProperties")
    if isinstance(ap, dict):
        _walk_schema_properties(ap, out)


def _resolve_schema_properties(
    specs: list[str],
    resolver: PlaceResolver,
    settings: dict[str, Any],
) -> dict[str, Any]:
    """Find and merge JSON schema ``properties`` for the given specs.

    Used at render time so cell rendering can read display hints
    (``x-osm-list-sort``, ``x-osm-list-join``, ``x-osm-list-hidden``,
    ``x-osm-list-i18n``). Multiple distinct schemas merge with later-wins
    semantics. Returns ``{}`` if no schema is found.
    """
    schema_names = _schema_filenames(settings)
    seen: dict[Path, dict[str, Any]] = {}
    for spec in specs:
        for path in resolver.resolve_to_paths(spec):
            sp = _find_schema_for(path, resolver.root, schema_names)
            if sp is None or sp in seen:
                continue
            try:
                with open(sp, encoding="utf-8") as f:
                    seen[sp] = yaml.safe_load(f) or {}
            except (OSError, yaml.YAMLError) as e:
                log.warning("pelican-osm: cannot load schema %s: %s", sp, e)
                seen[sp] = {}

    merged: dict[str, Any] = {}
    for sch in seen.values():
        _walk_schema_properties(sch, merged)
    return merged


class PlaceResolver:
    """Resolves shortcode arguments to lists of place dicts."""

    def __init__(self, root: Path):
        self.root = root

    def resolve_to_paths(self, spec: str) -> list[Path]:
        """
        Resolve a spec to a list of YAML file Paths (without loading them).
        Used by _process_content to build GeoJSON URLs.
        """
        spec = spec.strip()

        if spec in ("", "."):
            return self._list_dir(self.root)

        # No fragment support for path resolution (fragment selects within a file)
        spec_no_frag = spec.split("#")[0].strip()
        target = self.root / spec_no_frag

        if target.is_dir():
            return self._list_dir(target)

        if target.is_file():
            return [target]

        for ext in (".yml", ".yaml"):
            t = target.with_suffix(ext)
            if t.is_file():
                return [t]

        if spec_no_frag.rstrip("/") == self.root.name:
            return self._list_dir(self.root)

        return []

    def _list_dir(self, directory: Path) -> list[Path]:
        return sorted(f for f in directory.rglob("*") if _is_place_yaml(f))

    def resolve(self, spec: str) -> list[dict[str, Any]]:
        """Resolve a spec to a list of place dicts (loads YAML files).

        Spec formats:
          japan/tokyo.yml          - all places in that file
          japan/tokyo.yml#ueno     - single place matching id or name
          japan/                   - all .yml files in that folder
          japan                    - same (trailing slash optional)
          .  or  (empty)           - all .yml files under the root
        """
        spec = spec.strip()

        log.debug(
            "pelican-osm: resolve('%s'), root=%s (exists=%s)",
            spec,
            self.root,
            self.root.exists(),
        )

        # Empty spec or explicit "." → entire root
        if spec in ("", "."):
            return self._load_dir(self.root)

        # Check for fragment (#place_name)
        fragment: str | None = None
        if "#" in spec:
            spec, fragment = spec.split("#", 1)
            fragment = fragment.strip()

        target = self.root / spec

        log.debug(
            "pelican-osm: target=%s (is_dir=%s, is_file=%s)",
            target,
            target.is_dir(),
            target.is_file(),
        )

        # Is it a directory?
        if target.is_dir():
            if fragment:
                log.warning(
                    "Fragment '#%s' ignored for directory spec '%s'", fragment, spec
                )
            return self._load_dir(target)

        # Try as-is (yaml file)
        if target.is_file():
            places = _load_yaml_file(target)
            if fragment:
                places = self._filter_by_fragment(places, fragment, spec)
            return places

        # Try adding .yml / .yaml extension
        for ext in (".yml", ".yaml"):
            target_yml = target.with_suffix(ext)
            if target_yml.is_file():
                places = _load_yaml_file(target_yml)
                if fragment:
                    places = self._filter_by_fragment(places, fragment, str(target_yml))
                return places

        # Last chance: spec might be the root folder's own name
        # e.g. OSM_PLACES_ROOT="places" and {% place places %}
        log.debug(
            "pelican-osm: root.name=%s, spec.rstrip='%s'",
            self.root.name,
            spec.rstrip("/"),
        )
        if spec.rstrip("/") == self.root.name:
            return self._load_dir(self.root)

        log.warning(
            "pelican-osm: could not resolve spec '%s' "
            "(root=%s, target_checked=%s, exists=%s)",
            spec,
            self.root,
            target,
            target.exists(),
        )
        return []

    def _load_dir(self, directory: Path) -> list[dict[str, Any]]:
        places = []
        for yml_file in self._list_dir(directory):
            loaded = _load_yaml_file(yml_file)
            log.debug("pelican-osm: %s → %d places", yml_file.name, len(loaded))
            places.extend(loaded)
        return places

    def _filter_by_fragment(
        self, places: list[dict[str, Any]], fragment: str, source: str
    ) -> list[dict[str, Any]]:
        # id (dict key) takes priority over name match
        matched = [
            p
            for p in places
            if str(p.get("id", "")) == fragment or str(p.get("name", "")) == fragment
        ]
        if not matched:
            log.warning("No place matching '#%s' found in '%s'", fragment, source)
        return matched


_MAP_COUNTER = 0  # module-level counter for unique map IDs per process


def _geojson_url(yaml_path: Path, root: Path, static_prefix: str) -> str:
    """Return the URL for the GeoJSON file corresponding to a YAML path."""
    rel = yaml_path.relative_to(root)
    return (
        f"{static_prefix.rstrip('/')}/places/{rel.with_suffix('.geojson').as_posix()}"
    )


def _resolve_image_url(
    image_path: str, siteurl: str, content_path: Path | None = None
) -> str:
    """Resolve image path to absolute URL.

    Supports:
    - Absolute URLs (http://, https://)
    - Relative Pelican paths (images/photo.jpg)
    - Absolute paths (/static/images/photo.jpg)
    """
    image_path = image_path.strip()

    # Already absolute URL
    if image_path.startswith(("http://", "https://")):
        return image_path

    # Remove leading slash for relative path handling
    if image_path.startswith("/"):
        return siteurl.rstrip("/") + image_path

    # Relative Pelican path - prepend SITEURL
    return siteurl.rstrip("/") + "/" + image_path.lstrip("/")


def _render_place_html(
    geojson_entries: list[dict],
    names: list[str],
    map_height: str,
    tile_url: str,
    attribution: str,
    images_map: dict[str, list[str]] | None = None,
    layer_field: str | None = None,
    field_labels: dict[str, str] | None = None,
) -> str:
    """Render a single map block that fetches one or more GeoJSON URLs.

    geojson_entries: list of {"url": str, "fragment": str | None}
    images_map: dict of {place_id_or_name: [image_urls]}
    field_labels: dict of {field_name: localized_title} for popup field labels
    """
    global _MAP_COUNTER
    _MAP_COUNTER += 1
    map_id = f"osm-map-{_MAP_COUNTER}"

    if not geojson_entries:
        return "<!-- pelican-osm: no valid places -->"

    def _attr(value: str) -> str:
        """Escape a string for use inside a double-quoted HTML attribute."""
        return value.replace("&", "&amp;").replace('"', "&quot;")

    entries_attr = _attr(json.dumps(geojson_entries))
    tile_attr = _attr(tile_url)
    attribution_attr = _attr(attribution)

    # Add images data if provided
    images_attr = ""
    if images_map:
        images_attr = f' data-images="{_attr(json.dumps(images_map))}"'

    layer_field_attr = ""
    if layer_field:
        layer_field_attr = f' data-osm-layer-field="{_attr(layer_field)}"'

    field_labels_attr = ""
    if field_labels:
        field_labels_attr = (
            f' data-osm-field-labels="'
            f'{_attr(json.dumps(field_labels, ensure_ascii=False))}"'
        )

    CAPTION_MAX = 3
    if len(names) <= CAPTION_MAX:
        captions = ", ".join(names)
    elif names:
        captions = (
            ", ".join(names[:CAPTION_MAX]) + f" and {len(names) - CAPTION_MAX} more"
        )
    else:
        captions = ""

    return (
        f'<div class="osm-map-block">\n'
        f'  <div id="{map_id}" class="osm-map" '
        f'style="--osm-map-height:{map_height};" '
        f'data-geojson="{entries_attr}" '
        f'data-tile="{tile_attr}" '
        f'data-attribution="{attribution_attr}"'
        f"{images_attr}"
        f"{layer_field_attr}"
        f"{field_labels_attr}>"
        f'<div class="osm-map-loading"><div class="osm-map-spinner"></div></div>'
        f"</div>\n"
        f'  <div class="osm-map-caption">{captions}</div>\n'
        f"</div>"
    )


def _format_scalar(value: Any) -> str:
    """Render a scalar value for a table cell. ``datetime.date`` becomes ISO."""
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    return str(value)


def _render_list_cell(
    value: list[Any],
    field_props: dict[str, Any],
) -> tuple[str, str | None]:
    """Render a list-valued field. Returns ``(display_html, sort_value)``.

    Honors two ``x-osm-*`` schema hints:
      * ``x-osm-list-join``: separator (default ``", "``)
      * ``x-osm-list-sort``: ``min``/``max``/``first``/``last`` — used to set
        ``data-sort-value`` so a multi-visit cell sorts deterministically.
    """
    join_sep = field_props.get("x-osm-list-join", ", ")
    sort_op = field_props.get("x-osm-list-sort")

    items = [_format_scalar(v) for v in value]
    display = join_sep.join(items)
    sort_value: str | None = None
    if items:
        if sort_op == "min":
            sort_value = min(items)
        elif sort_op == "max":
            sort_value = max(items)
        elif sort_op == "first":
            sort_value = items[0]
        elif sort_op == "last":
            sort_value = items[-1]
    return display, sort_value


def _resolve_group_count_template(settings: dict[str, Any], lang: str | None) -> str:
    """Pick the count-line template for ``group_summary_at`` headers.

    Precedence: explicit ``OSM_LIST_GROUP_COUNT_TEMPLATE`` setting wins (even
    when set to ``""`` to suppress) → built-in template matching the article's
    lang (full tag first, then primary subtag) → English default.
    """

    if "OSM_LIST_GROUP_COUNT_TEMPLATE" in settings:
        return cast(str, settings["OSM_LIST_GROUP_COUNT_TEMPLATE"])
    if lang:
        full = lang.lower()
        primary = full.split("-", 1)[0]
        for key in (full, primary):
            if key in BUILTIN_GROUP_COUNT_TEMPLATES:
                return BUILTIN_GROUP_COUNT_TEMPLATES[key]
    return DEFAULT_GROUP_COUNT_TEMPLATE


def _resolve_i18n_title(props: dict[str, Any], lang: str | None) -> str | None:
    """Pick a ``title`` from ``x-osm-list-i18n.title.<lang>`` if present.

    Tries an exact case-insensitive match on the full lang tag first
    (``zh-tw``), then the primary subtag (``zh``). Returns ``None`` if no
    match — caller should fall back to the plain ``title`` keyword.
    """
    if not lang:
        return None
    i18n = props.get("x-osm-list-i18n")
    if not isinstance(i18n, dict):
        return None
    titles = i18n.get("title")
    if not isinstance(titles, dict):
        return None
    lower = {k.lower(): v for k, v in titles.items() if isinstance(k, str)}
    candidate = lower.get(lang.lower())
    if not isinstance(candidate, str) or not candidate:
        primary = lang.split("-", 1)[0].lower()
        if primary != lang.lower():
            candidate = lower.get(primary)
    return candidate if isinstance(candidate, str) and candidate else None


def _build_popup_field_labels(
    field_schema: dict[str, Any], lang: str | None
) -> dict[str, str]:
    """Return ``{field_name: localized_title}`` for the map popup.

    Same precedence as the table's column headers:
    ``x-osm-list-i18n.title.<lang>`` falls back to the schema's plain
    ``title``. Fields with neither are omitted so the JS side can apply
    its built-in label or the capitalized-key default.
    """
    out: dict[str, str] = {}
    for field, props in field_schema.items():
        if not isinstance(props, dict):
            continue
        localized = _resolve_i18n_title(props, lang)
        title = localized or props.get("title")
        if isinstance(title, str) and title:
            out[field] = title
    return out


def _render_place_list_html(
    places: list[dict[str, Any]],
    fields: list[str],
    field_labels: dict[str, str],
    group_by: list[str] | None = None,
    aggregate: dict[str, str] | None = None,
    group_summary_at: list[str] | None = None,
    group_count_template: str = DEFAULT_GROUP_COUNT_TEMPLATE,
    field_schema: dict[str, Any] | None = None,
    lang: str | None = None,
    siteurl: str = "",
) -> str:
    """Render an HTML table for a list of places.

    ``fields`` controls which columns appear (in order) between the Name and
    Post columns.  If empty, every non-reserved field found in the data is
    used automatically.

    Optional grouping/aggregation:
      * ``group_by``: bucket places sharing the same tuple of these fields
        so they render contiguously (see ``_collapse_places``). Rows are
        preserved as-is unless ``aggregate`` is also given.
      * ``aggregate``: opt-in SQL-style collapse. With this set, all places
        sharing a ``group_by`` tuple merge into one row; per-field combiners
        (e.g. ``{"date": "year"}``) decide how aggregate fields are computed.
      * ``group_summary_at``: a prefix of ``group_by``. When set, those fields
        are removed from data row columns and instead emitted as synthetic
        ``<tr class="osm-group-header">`` rows at each group boundary.
      * ``group_count_template``: format string used for the count line below
        each header. ``{n}`` is the number of underlying places. Set to ``""``
        to omit the count entirely.
      * ``field_schema``: JSON Schema ``properties`` dict (or any per-field
        config dict). Recognized hints: ``x-osm-list-join``, ``x-osm-list-sort``.
    """
    field_schema = field_schema or {}
    if not places:
        return "<!-- pelican-osm: no places for list -->"

    # Flatten nested ``items`` sub-rows into per-item rows. Places without
    # ``items`` pass through. The map shortcode does NOT do this (one pin
    # per parent place, items ignored).
    places = _expand_items(places)
    if not places:
        return "<!-- pelican-osm: no places for list -->"

    group_by = group_by or []
    aggregate = aggregate or {}
    group_summary_at = group_summary_at or []

    if group_summary_at and not group_by:
        log.warning("pelican-osm: group_summary_at requires group_by; ignoring summary")
        group_summary_at = []
    if group_summary_at and group_by[: len(group_summary_at)] != group_summary_at:
        log.warning(
            "pelican-osm: group_summary_at must be a prefix of group_by; "
            "ignoring summary"
        )
        group_summary_at = []

    if group_by:
        rows = _collapse_places(places, group_by, aggregate)
    else:
        rows = [{**p, "_places": [p]} for p in places]

    summary_set = set(group_summary_at)

    # Auto-detect columns when none are configured
    def is_hidden(field: str) -> bool:
        # Schema-level escape hatch: ``x-osm-list-hidden: true`` keeps the
        # field loaded (so group_by / sort / aggregate can still use it) but
        # drops it from the rendered table — useful for ordering keys, raw
        # geo fields, etc. that exist only to drive layout.
        props = field_schema.get(field)
        return bool(isinstance(props, dict) and props.get("x-osm-list-hidden") is True)

    if not fields:
        seen: list[str] = []
        for row in rows:
            for k in row:
                if k not in _LIST_RESERVED and k not in seen and not is_hidden(k):
                    seen.append(k)
        fields = seen
    else:
        fields = [f for f in fields if not is_hidden(f)]

    # Fields that live in summary header rows are removed from data row columns
    data_fields = [f for f in fields if f not in summary_set]

    def col_header(f: str, fallback: str | None = None) -> str:
        # Precedence: ``x-osm-list-i18n.title.<lang>`` (locale-specific) →
        # JSON Schema ``title`` (default-locale fallback) → OSM_LIST_FIELD_LABELS
        # → provided fallback or auto-derived from the key. ``title`` is a
        # standard JSON Schema keyword; ``x-osm-list-i18n`` is our extension
        # for per-language overrides without duplicating whole schemas.
        props = field_schema.get(f)
        if isinstance(props, dict):
            localized = _resolve_i18n_title(props, lang)
            if localized:
                return localized
            title = props.get("title")
            if isinstance(title, str) and title:
                return title
        if f in field_labels:
            return field_labels[f]
        return fallback if fallback is not None else f.replace("_", " ").capitalize()

    def render_tags(tags: Any) -> str:
        if not tags:
            return ""
        if isinstance(tags, str):
            tags = [tags]
        return " ".join(
            f'<span class="osm-badge osm-badge--tag">{html.escape(str(t))}</span>'
            for t in tags
        )

    def render_urls(urls: Any) -> str:
        if not urls or not isinstance(urls, list):
            return ""

        def link_text(u: dict) -> str:
            if label := u.get("label"):
                return str(label)
            parsed = urlparse(u["href"])
            return parsed.netloc or "Link"

        return " ".join(
            f'<a href="{html.escape(_safe_url(u["href"]), quote=True)}">'
            f"{html.escape(link_text(u))}</a>"
            for u in urls
            if isinstance(u, dict) and u.get("href")
        )

    def render_map_links(lat: Any, lon: Any) -> str:
        """Render the 🗺️·📍 link span. Empty string when lat/lon missing.

        Used both inside the data row's name cell and inside the summary
        header for ``name`` when name is hoisted via ``group_summary_at``.
        CSS class ``osm-list-map-links`` is ``display: block`` so the icons
        appear on a new line below their sibling text.
        """
        if lat is None or lon is None:
            return ""
        osm_url = f"https://www.openstreetmap.org/?mlat={lat}&mlon={lon}&zoom=17"
        gmaps_url = f"https://maps.google.com/?q={lat},{lon}"
        return (
            f'<span class="osm-list-map-links">'
            f'<a href="{osm_url}" target="_blank" rel="noopener" '
            f'title="OpenStreetMap">🗺️</a>'
            f'<span class="osm-list-map-sep" aria-hidden="true">·</span>'
            f'<a href="{gmaps_url}" target="_blank" rel="noopener" '
            f'title="Google Maps">📍</a>'
            f"</span>"
        )

    def render_name_cell(place: dict[str, Any]) -> str:
        name = str(place.get("name", ""))
        return (
            f'<td data-sort-value="{html.escape(name, quote=True)}">'
            f"{html.escape(name)}"
            f"{render_map_links(place.get('lat'), place.get('lon'))}</td>"
        )

    used_row_ids: set[str] = set()
    # Aggregated <slug>: [url, ...] map, emitted once as a JSON sidecar
    # instead of bloating every <tr> with a data-images attribute.
    images_by_slug: dict[str, list[str]] = {}

    def _row_slug(row: dict[str, Any]) -> tuple[str, str]:
        """Return ``(slug, parent_slug)`` for a row.

        ``slug`` is the unique row anchor (after dedup); ``parent_slug`` is
        the unsuffixed place slug, populated only for rows produced by
        ``_expand_items`` so JS can route marker popups to *any* row
        belonging to the parent place.
        """
        base = _place_anchor_slug(row)
        if not base:
            return "", ""
        item_parts = row.get("_item_slug_parts") or []
        candidate = base
        parent = ""
        if item_parts:
            raw = str(item_parts[0]).strip()
            if raw:
                candidate = f"{base}-{_slugify(raw)}"
                parent = base
        slug = candidate
        n = 2
        while slug in used_row_ids:
            slug = f"{candidate}-{n}"
            n += 1
        used_row_ids.add(slug)
        return slug, parent

    def _row_attrs(row: dict[str, Any]) -> str:
        """Compose ``<tr>`` attributes: anchor id, class, and data-images.

        ``id="osm-place-<slug>"`` is the per-row anchor; ``data-osm-place-slug``
        mirrors it so JS can match a marker without re-slugifying. For rows
        from items expansion we also emit ``data-osm-parent-slug`` so a
        popup whose marker only knows the parent slug can still route to
        one of the expanded rows.
        """
        slug, parent_slug = _row_slug(row)

        raw = row.get("images")
        has_imgs = False
        if raw:
            if isinstance(raw, str):
                raw = [raw]
            if isinstance(raw, list):
                resolved = [
                    _resolve_image_url(str(img), siteurl, None)
                    for img in raw
                    if img is not None
                ]
                if resolved:
                    has_imgs = True
                    if slug:
                        images_by_slug[slug] = resolved

        classes = ["osm-place-row"]
        if has_imgs:
            classes.append("osm-has-images")

        parts = []
        if slug:
            parts.append(f' id="osm-place-{slug}"')
        parts.append(f' class="{" ".join(classes)}"')
        if slug:
            parts.append(f' data-osm-place-slug="{slug}"')
        if parent_slug:
            parts.append(f' data-osm-parent-slug="{parent_slug}"')
        return "".join(parts)

    has_tags = any(row.get("tags") for row in rows) and not is_hidden("tags")
    has_url = any(row.get("urls") for row in rows) and not is_hidden("urls")
    has_images = any(row.get("images") for row in rows)

    # When ``name`` is in group_summary_at, it's hoisted into the summary
    # header (alongside its map links) and dropped from the data row column,
    # mirroring how other summary fields are removed from data_fields.
    name_is_summary = "name" in summary_set

    # Header
    headers = []
    if not name_is_summary:
        headers.append("<th>" + col_header("name", "Name") + "</th>")
    if has_tags:
        headers.append("<th>" + col_header("tags", "Tags") + "</th>")
    headers += [f"<th>{col_header(f)}</th>" for f in data_fields]
    if has_url:
        headers.append("<th>" + col_header("urls", "Links") + "</th>")
    if has_images:
        headers.append('<th class="osm-list-image-col-header">🖼</th>')
    col_count = len(headers)

    def render_value_cell(field: str, value: Any) -> str:
        if isinstance(value, list):
            props = field_schema.get(field) or {}
            display, sort_value = _render_list_cell(
                value, props if isinstance(props, dict) else {}
            )
            if sort_value is not None:
                return (
                    f'<td data-sort-value="{html.escape(sort_value, quote=True)}">'
                    f"{html.escape(display)}</td>"
                )
            return f"<td>{html.escape(display)}</td>"
        if value == "" or value is None:
            return "<td></td>"
        return f"<td>{html.escape(_format_scalar(value))}</td>"

    def render_data_row(row: dict[str, Any]) -> str:
        cells = []
        if not name_is_summary:
            cells.append(render_name_cell(row))
        if has_tags:
            cells.append(f"<td>{render_tags(row.get('tags', []))}</td>")
        for f in data_fields:
            cells.append(render_value_cell(f, row.get(f, "")))
        if has_url:
            cells.append(f"<td>{render_urls(row.get('urls', []))}</td>")
        if has_images:
            raw = row.get("images")
            if raw:
                cells.append('<td class="osm-list-image-icon">📷</td>')
            else:
                cells.append("<td></td>")
        return f"<tr{_row_attrs(row)}>" + "".join(cells) + "</tr>"

    rendered_rows: list[str] = []
    if group_summary_at:
        # Pre-compute place counts at every prefix depth so each header can
        # display its own subtotal (e.g. country total → city total → district
        # total) regardless of how many descendant rows it spans.
        prefix_counts: dict[tuple, int] = defaultdict(int)
        for row in rows:
            n = len(row.get("_places") or [row])
            key = tuple(row.get(f, "") for f in group_summary_at)
            for d in range(len(key)):
                prefix_counts[key[: d + 1]] += n

        used_ids: set[str] = set()

        def _anchor_id(prefix: tuple) -> str:
            slug_parts = [_slugify(v) for v in prefix]
            base = "osm-group--" + "--".join(slug_parts)
            anchor = base
            n = 2
            while anchor in used_ids:
                anchor = f"{base}-{n}"
                n += 1
            used_ids.add(anchor)
            return anchor

        prev_key: tuple = ()
        for row in rows:
            cur_key = tuple(row.get(f, "") for f in group_summary_at)
            for depth, val in enumerate(cur_key):
                prefix = cur_key[: depth + 1]
                prev_prefix = (
                    prev_key[: depth + 1] if len(prev_key) >= depth + 1 else None
                )
                if prefix == prev_prefix:
                    continue
                title = str(val)
                count_html = ""
                if group_count_template:
                    # The template itself is developer-controlled (set via
                    # OSM_GROUP_COUNT_TEMPLATE), so leave its markup intact;
                    # only ``n`` is interpolated and it's always an int.
                    count_text = group_count_template.format(n=prefix_counts[prefix])
                    count_html = f'<span class="osm-group-count">{count_text}</span>'
                # When ``name`` is the summary field at this depth, surface the
                # parent's map links inside the header so the block carries the
                # place's identity AND its location (mirrors data-row name cell).
                map_links_html = ""
                if group_summary_at[depth] == "name":
                    map_links_html = render_map_links(row.get("lat"), row.get("lon"))
                anchor_id = _anchor_id(prefix)
                rendered_rows.append(
                    f'<tr class="osm-group-header'
                    f' osm-group-header--depth-{depth}"'
                    f' data-depth="{depth}" id="{anchor_id}">'
                    f'<td colspan="{col_count}">'
                    f'<span class="osm-group-header-toggle"'
                    f' aria-hidden="true">▾</span>'
                    f'<strong class="osm-group-header-title">'
                    f"{html.escape(title)}</strong>"
                    f"{count_html}"
                    f"{map_links_html}"
                    f"</td></tr>"
                )
            prev_key = cur_key
            rendered_rows.append(render_data_row(row))
    else:
        for row in rows:
            rendered_rows.append(render_data_row(row))

    # Single JSON sidecar instead of per-row data-images attrs. The text
    # is HTML-safe because <script type="application/json"> isn't parsed
    # as JS; only "</script" needs neutralising.
    images_sidecar = ""
    if images_by_slug:
        sidecar_json = json.dumps(images_by_slug, ensure_ascii=False).replace(
            "</", "<\\/"
        )
        images_sidecar = (
            '<script type="application/json" class="osm-list-images">'
            f"{sidecar_json}</script>\n"
        )

    return (
        '<div class="osm-place-list-wrapper">\n'
        '<table class="osm-place-list">\n'
        "<thead><tr>" + "".join(headers) + "</tr></thead>\n"
        "<tbody>\n" + "\n".join(rendered_rows) + "\n</tbody>\n"
        "</table>\n"
        '<div class="osm-place-list-count"></div>\n'
        f"{images_sidecar}"
        "</div>"
    )


def _process_content(
    content: str,
    resolver: PlaceResolver,
    settings: dict[str, Any],
    lang: str | None = None,
) -> str:
    """Replace all {% place ... %} and {% place_list ... %} shortcodes in content.

    ``lang`` is the BCP-47 language tag of the article being rendered (e.g.
    ``zh-tw``). It controls per-locale schema title lookups via
    ``x-osm-list-i18n``. Falls back to ``settings["DEFAULT_LANG"]`` when None.
    """
    shortcode = settings.get("OSM_SHORTCODE", DEFAULT_SHORTCODE)
    map_height = settings.get("OSM_MAP_HEIGHT", DEFAULT_MAP_HEIGHT)
    tile_url = settings.get("OSM_MAP_TILE", DEFAULT_MAP_TILE)
    attribution = settings.get("OSM_MAP_ATTRIBUTION", DEFAULT_MAP_ATTRIBUTION)
    static_prefix = settings.get("OSM_STATIC_PREFIX", "/static")
    siteurl = settings.get("SITEURL", "")

    pattern = re.compile(
        r"\{%\s*" + re.escape(shortcode) + r"\s+(.+?)\s*%\}",
        re.DOTALL,
    )

    def replace(match: re.Match) -> str:
        raw_args = match.group(1)
        specs, _kwargs = _parse_shortcode_args(raw_args)

        # Each entry: {"url": "...", "fragment": "name_or_id" | None}
        geojson_entries: list[dict] = []
        names: list[str] = []
        images_map: dict[str, list[str]] = {}

        for spec in specs:
            fragment = None
            if "#" in spec:
                spec_path, fragment = spec.split("#", 1)
                fragment = fragment.strip()
            else:
                spec_path = spec

            yaml_paths = resolver.resolve_to_paths(spec_path)
            for yaml_path in yaml_paths:
                url = _geojson_url(yaml_path, resolver.root, static_prefix)
                entry = {"url": url, "fragment": fragment}
                if entry not in geojson_entries:
                    geojson_entries.append(entry)

                # Collect names and images for caption, respecting fragment filter
                places = _load_yaml_file(yaml_path)
                valid = [p for p in places if _validate_place(p, str(yaml_path))]
                if fragment:
                    valid = [
                        p
                        for p in valid
                        if str(p.get("id", "")) == fragment
                        or str(p.get("name", "")) == fragment
                    ]
                names.extend(p["name"] for p in valid)

                # Collect images from places - map by id or name
                for p in valid:
                    images = p.get("images", [])
                    if images:
                        # Handle both string and list formats
                        if isinstance(images, str):
                            images = [images]
                        resolved_images = []
                        for img in images:
                            resolved_url = _resolve_image_url(
                                img, siteurl, resolver.root.parent
                            )
                            resolved_images.append(resolved_url)

                        # Use id if available, otherwise use name
                        key = p.get("id") or p.get("name")
                        if key:
                            images_map[key] = resolved_images

        images_dict = images_map if images_map else None

        field_schema = _resolve_schema_properties(specs, resolver, settings)
        layer_field = next(
            (k for k, v in field_schema.items() if v.get("x-osm-map-layer") is True),
            None,
        )
        popup_labels = _build_popup_field_labels(
            field_schema, lang or settings.get("DEFAULT_LANG")
        )

        return _render_place_html(
            geojson_entries,
            names,
            map_height,
            tile_url,
            attribution,
            images_dict,
            layer_field,
            field_labels=popup_labels,
        )

    result = cast(str, pattern.sub(replace, content))

    # ── place_list shortcode ──────────────────────────────────────
    list_shortcode = settings.get("OSM_LIST_SHORTCODE", DEFAULT_LIST_SHORTCODE)
    list_pattern = re.compile(
        r"\{%\s*" + re.escape(list_shortcode) + r"\s+(.+?)\s*%\}",
        re.DOTALL,
    )

    def replace_list(match: re.Match) -> str:
        specs, kwargs = _parse_shortcode_args(match.group(1))
        places: list[dict[str, Any]] = []
        for spec in specs:
            loaded = resolver.resolve(spec)
            places.extend(p for p in loaded if _validate_place(p, spec))

        # Normalize urls fields using the incrementally-built article URL map
        for place in places:
            if "urls" in place:
                place["urls"] = _normalize_url_field(place["urls"], _article_url_map)

        field_labels: dict[str, str] = settings.get("OSM_LIST_FIELD_LABELS", {})
        list_fields: list[str] = settings.get("OSM_LIST_FIELDS", [])

        group_by = _parse_csv_kwarg(kwargs.get("group_by", ""))
        aggregate = _parse_aggregate_kwarg(kwargs.get("aggregate", ""))
        group_summary_at = _parse_csv_kwarg(kwargs.get("group_summary_at", ""))
        group_count_template = _resolve_group_count_template(
            settings, lang or settings.get("DEFAULT_LANG")
        )
        field_schema = _resolve_schema_properties(specs, resolver, settings)

        return _render_place_list_html(
            places,
            list_fields,
            field_labels,
            group_by=group_by,
            aggregate=aggregate,
            group_summary_at=group_summary_at,
            group_count_template=group_count_template,
            field_schema=field_schema,
            lang=lang or settings.get("DEFAULT_LANG"),
            siteurl=siteurl,
        )

    result = cast(str, list_pattern.sub(replace_list, result))
    return result


_resolver: PlaceResolver | None = None
_settings: dict[str, Any] = {}
_article_url_map: dict[str, str] = {}
_content_path: Path | None = None


def _build_shortcode_pattern(shortcodes: list[str]) -> re.Pattern[str]:
    """Compile the regex that matches every ``{% <shortcode> ... %}`` block.

    Used both by the pre-Markdown stash extension and (logically) by the
    HTML-stage substitution in ``_process_content``; the two regexes must
    stay in sync so anything stashed at preprocess time is still recognized
    when the substitution runs.
    """
    joined = "|".join(re.escape(s) for s in shortcodes)
    return re.compile(r"\{%\s*(?:" + joined + r")\s+.+?\s*%\}", re.DOTALL)


if _HAS_MARKDOWN:

    class _ShortcodePreserver(_markdown.preprocessors.Preprocessor):
        """Stash ``{% ... %}`` shortcode blocks via Markdown's ``htmlStash``
        before any other extension can touch them.

        Without this, Markdown's ``attr_list`` extension (bundled with
        ``markdown.extensions.extra``) treats the ``{...}`` syntax as an
        attribute list. When two shortcodes sit on adjacent lines they merge
        into a single paragraph and the second ``{% ... %}`` gets eaten as
        paragraph attributes — pelican-osm never sees it. Stashing makes the
        block opaque to every other extension; Markdown restores it verbatim
        at the end of conversion, after which ``_process_content`` does the
        real substitution.
        """

        def __init__(self, md: Any, pattern: re.Pattern[str]) -> None:
            super().__init__(md)
            self._pattern = pattern

        def run(self, lines: list[str]) -> list[str]:
            text = "\n".join(lines)
            text = self._pattern.sub(
                lambda m: self.md.htmlStash.store(m.group(0)),
                text,
            )
            return text.split("\n")

    class _ShortcodePreserveExtension(_markdown.Extension):
        def __init__(self, shortcodes: list[str]) -> None:
            super().__init__()
            if not shortcodes:
                raise ValueError("shortcodes must be non-empty")
            self._pattern = _build_shortcode_pattern(shortcodes)

        def extendMarkdown(self, md: Any) -> None:
            # Priority 25 sits between normalize_whitespace (30) and
            # html_block (20). Running AFTER normalize_whitespace is required
            # because that preprocessor strips STX/ETX control chars from
            # source — which would silently corrupt our htmlStash
            # placeholders. Running BEFORE html_block (and well before any
            # block/tree processor like attr_list) means our stashed
            # shortcodes pass through untouched.
            md.preprocessors.register(
                _ShortcodePreserver(md, self._pattern),
                "pelican_osm_shortcode_preserve",
                25,
            )


def _register_markdown_extension(settings: dict[str, Any]) -> None:
    """Append our shortcode-preserving Markdown extension to ``MARKDOWN``.

    No-op when ``markdown`` isn't installed (e.g., RST-only sites) or when
    the user opts out via ``OSM_DISABLE_MARKDOWN_PROTECTION``. Idempotent so
    repeated ``initialized`` signals (e.g. from i18n_subsites rebuilds)
    don't stack copies.
    """
    if not _HAS_MARKDOWN:
        return
    if settings.get("OSM_DISABLE_MARKDOWN_PROTECTION"):
        return

    md_settings = settings.setdefault("MARKDOWN", {})
    md_settings.setdefault("extensions", [])
    md_settings.setdefault("extension_configs", {})
    extensions = md_settings["extensions"]

    if any(isinstance(e, _ShortcodePreserveExtension) for e in extensions):
        return

    shortcodes = [
        settings.get("OSM_SHORTCODE", DEFAULT_SHORTCODE),
        settings.get("OSM_LIST_SHORTCODE", DEFAULT_LIST_SHORTCODE),
    ]
    extensions.append(_ShortcodePreserveExtension(shortcodes))


def _init_resolver(pelican_obj: Any) -> None:
    global _resolver, _settings, _article_url_map, _content_path

    # i18n_subsites fires signals.initialized for each language subsite using
    # a SITEURL that extends the main site's (e.g. /en under /). Resetting
    # the URL map there would clobber canonical zh-tw URLs with per-subsite
    # fallback URLs (e.g. /en/post-zh-tw.html) that may not exist, breaking
    # both the table links and the shared GeoJSON used by all map pins.
    new_siteurl = pelican_obj.settings.get("SITEURL", "")
    old_siteurl = _settings.get("SITEURL", "")
    is_subsite = bool(old_siteurl) and new_siteurl.startswith(old_siteurl + "/")
    if not is_subsite:
        _article_url_map = {}

    _settings = pelican_obj.settings

    # Pelican's PATH setting may be relative.
    # Resolve it against the directory containing pelicanconf.py when possible,
    # otherwise fall back to cwd — same strategy Pelican itself uses internally.
    raw_path = pelican_obj.settings.get("PATH", "content")
    content_path = Path(raw_path)
    if not content_path.is_absolute():
        conf_file = pelican_obj.settings.get("pelicanconf")
        if conf_file:
            content_path = Path(conf_file).parent / raw_path
        content_path = content_path.resolve()

    _content_path = content_path

    places_root = pelican_obj.settings.get("OSM_PLACES_ROOT", DEFAULT_PLACES_ROOT)
    root = Path(places_root)
    if not root.is_absolute():
        root = (content_path / root).resolve()

    _resolver = PlaceResolver(root)
    log.debug("pelican-osm: content_path=%s", content_path)
    log.debug("pelican-osm: places root=%s exists=%s", root, root.exists())

    _register_markdown_extension(_settings)

    if root.exists():
        _validate_yaml_files(root, _settings)


def _process_article(content: Any) -> None:
    if not isinstance(content, (Article, Page)):
        return

    # Build the article URL map incrementally so place_list shortcodes on
    # pages can resolve {filename} references to articles processed earlier.
    # Keys are stored both as absolute paths and as content-relative paths so
    # that {filename}posts/foo.md references resolve correctly.
    siteurl = _settings.get("SITEURL", "").rstrip("/")
    src = getattr(content, "source_path", None)
    url = getattr(content, "url", None)
    if src and url:
        abs_url = siteurl + "/" + url.lstrip("/")
        # setdefault: first write wins. When i18n_subsites processes an
        # untranslated article as a fallback, its URL (e.g. /en/post-zh-tw.html)
        # must not overwrite the canonical main-site URL already in the map.
        _article_url_map.setdefault(src, abs_url)
        if _content_path:
            try:
                rel = Path(src).relative_to(_content_path)
                _article_url_map.setdefault(str(rel), abs_url)
            except ValueError:
                pass

    if _resolver is None:
        return

    if hasattr(content, "_content"):
        article_lang = getattr(content, "lang", None) or _settings.get("DEFAULT_LANG")
        content._content = _process_content(
            content._content, _resolver, _settings, lang=article_lang
        )


def _build_article_url_map(pelican_obj: Any) -> dict[str, str]:
    """Return a map of source_path → absolute URL for every article and page."""
    siteurl = pelican_obj.settings.get("SITEURL", "").rstrip("/")
    url_map: dict[str, str] = {}
    for generator in getattr(pelican_obj, "generators", []):
        for content in (
            *getattr(generator, "articles", []),
            *getattr(generator, "pages", []),
        ):
            src = getattr(content, "source_path", None)
            url = getattr(content, "url", None)
            if src and url:
                url_map[src] = siteurl + "/" + url.lstrip("/")
    return url_map


def _resolve_filename_url(url: str, article_url_map: dict[str, str]) -> str:
    """Resolve a ``{filename}/path/to/post.md`` reference to an absolute URL.

    If ``url`` does not start with ``{filename}`` it is returned unchanged.
    """
    if not url.startswith("{filename}"):
        return url
    path_part = url[len("{filename}") :].lstrip("/")
    resolved = article_url_map.get(path_part)
    if resolved is None:
        log.warning("pelican-osm: could not resolve {filename} URL: %s", url)
        return url
    return resolved


def _normalize_url_field(
    url_value: Any,
    article_url_map: dict[str, str] | None,
) -> list[dict[str, str | None]]:
    """Normalize the ``urls`` field to a list of ``{label, href}`` dicts.

    Accepted input formats::

        # plain string
        urls: "https://example.com"

        # single object
        urls:
          label: "2024"
          href: "{filename}posts/review/2024/my-post.md"

        # list of objects
        urls:
          - label: "2023"
            href: "{filename}posts/review/2023/visit.md"
          - label: "2024"
            href: "{filename}posts/review/2024/visit.md"
    """

    def _resolve(href: str) -> str:
        return _resolve_filename_url(href, article_url_map) if article_url_map else href

    def _entry(item: Any) -> dict[str, str | None] | None:
        if isinstance(item, str):
            return {"label": None, "href": _resolve(item)}
        if isinstance(item, dict):
            href = str(item.get("href", ""))
            label = item.get("label")
            return {
                "label": str(label) if label is not None else None,
                "href": _resolve(href),
            }
        return None

    if isinstance(url_value, list):
        return [e for item in url_value if (e := _entry(item)) is not None]

    entry = _entry(url_value)
    return [entry] if entry is not None else []


def _place_to_feature(
    place: dict[str, Any],
    article_url_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Convert a single place dict to a GeoJSON Feature."""

    properties = {}
    for k, v in place.items():
        if k in ("lat", "lon", "images", "items"):
            continue
        if v == "" or v == [] or v is None:
            continue
        if isinstance(v, (datetime.date, datetime.datetime)):
            v = v.isoformat()
        elif isinstance(v, list):
            v = [
                item.isoformat()
                if isinstance(item, (datetime.date, datetime.datetime))
                else item
                for item in v
            ]
        properties[k] = v

    if "urls" in properties:
        properties["urls"] = _normalize_url_field(properties["urls"], article_url_map)

    slug = _place_anchor_slug(place)
    if slug:
        properties["slug"] = slug

    return {
        "type": "Feature",
        "geometry": {
            "type": "Point",
            "coordinates": [float(place["lon"]), float(place["lat"])],
        },
        "properties": properties,
    }


def _yaml_to_geojson(
    yaml_path: Path,
    article_url_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Load a YAML file and return a GeoJSON FeatureCollection."""
    places = _load_yaml_file(yaml_path)
    valid = [p for p in places if _validate_place(p, str(yaml_path))]
    return {
        "type": "FeatureCollection",
        "features": [_place_to_feature(p, article_url_map) for p in valid],
    }


def _export_geojson(pelican_obj: Any) -> None:
    """Convert every YAML under places root to a .geojson file in output/static/places/.

    Written relative to the site's output directory.
    """
    if _resolver is None:
        return

    output = Path(pelican_obj.settings.get("OUTPUT_PATH", "output"))
    root = _resolver.root

    if not root.exists():
        return

    yaml_files = sorted(f for f in root.rglob("*") if _is_place_yaml(f))

    for yaml_path in yaml_files:
        rel = yaml_path.relative_to(root)
        dest = output / "static" / "places" / rel.with_suffix(".geojson")
        dest.parent.mkdir(parents=True, exist_ok=True)

        geojson = _yaml_to_geojson(yaml_path, _article_url_map)
        dest.write_text(
            json.dumps(geojson, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        log.debug("pelican-osm: wrote %s (%d features)", dest, len(geojson["features"]))


def _copy_static(pelican_obj: Any) -> None:
    """Copy bundled static assets (JS/CSS) to output."""
    static_src = Path(__file__).parent / "static"
    output = Path(pelican_obj.settings.get("OUTPUT_PATH", "output"))
    dest = output / "static" / "pelican_osm"

    if static_src.exists():
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(static_src, dest)
        log.debug("pelican-osm: copied static assets to %s", dest)


def register() -> None:
    signals.initialized.connect(_init_resolver)
    signals.content_object_init.connect(_process_article)
    signals.finalized.connect(_copy_static)
    signals.finalized.connect(_export_geojson)
