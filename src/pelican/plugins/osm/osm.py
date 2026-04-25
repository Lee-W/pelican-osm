"""pelican-osm plugin: embed OpenStreetMap maps via {% place %} shortcodes."""

from __future__ import annotations

import datetime
import json
import logging
import re
import shutil
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

import yaml
from pelican.contents import Article, Page

from pelican import signals

log = logging.getLogger(__name__)

DEFAULT_SHORTCODE = "place"
DEFAULT_LIST_SHORTCODE = "place_list"
DEFAULT_PLACES_ROOT = "places"  # relative to Pelican's PATH (content dir)
DEFAULT_MAP_HEIGHT = "400px"
DEFAULT_MAP_TILE = "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
DEFAULT_MAP_ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'

# Fields never shown as regular columns in the list table
_LIST_RESERVED = frozenset(["name", "lat", "lon", "id", "images", "urls", "tags"])


def _load_yaml_file(path: Path) -> list[dict[str, Any]]:
    """Load a YAML file and always return a list of place dicts.

    Three formats are supported, in priority order:

    1. **locations-based** (preferred for real use):
       Top-level keys other than ``locations`` become file-level defaults
       applied to every place. Per-place values always win::

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
            # defaults < per-place values
            entry = {**file_defaults, **item}
            places.append(entry)
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
                entry = {**file_defaults, **val}
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
            entry = {**file_defaults, **item}
            places.append(entry)
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
        return sorted(f for f in directory.rglob("*") if f.suffix in (".yml", ".yaml"))

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


def _resolve_image_url(image_path: str, siteurl: str, content_path: Path) -> str:
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
) -> str:
    """Render a single map block that fetches one or more GeoJSON URLs.

    geojson_entries: list of {"url": str, "fragment": str | None}
    images_map: dict of {place_id_or_name: [image_urls]}
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
        f"{images_attr}>"
        f'<div class="osm-map-loading"><div class="osm-map-spinner"></div></div>'
        f"</div>\n"
        f'  <div class="osm-map-caption">{captions}</div>\n'
        f"</div>"
    )


def _render_place_list_html(
    places: list[dict[str, Any]],
    fields: list[str],
    field_labels: dict[str, str],
) -> str:
    """Render an HTML table for a list of places.

    ``fields`` controls which columns appear (in order) between the Name and
    Post columns.  If empty, every non-reserved field found in the data is
    used automatically.
    """
    if not places:
        return "<!-- pelican-osm: no places for list -->"

    # Auto-detect columns when none are configured
    if not fields:
        seen: list[str] = []
        for place in places:
            for k in place:
                if k not in _LIST_RESERVED and k not in seen:
                    seen.append(k)
        fields = seen

    def col_header(f: str) -> str:
        return field_labels.get(f, f.replace("_", " ").capitalize())

    def render_tags(tags: Any) -> str:
        if not tags:
            return ""
        if isinstance(tags, str):
            tags = [tags]
        return " ".join(
            f'<span class="osm-badge osm-badge--tag">{t}</span>' for t in tags
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
            f'<a href="{u["href"]}">{link_text(u)}</a>'
            for u in urls
            if isinstance(u, dict) and u.get("href")
        )

    def render_name_cell(place: dict[str, Any]) -> str:
        name = place.get("name", "")
        lat = place.get("lat")
        lon = place.get("lon")
        if lat is not None and lon is not None:
            osm_url = f"https://www.openstreetmap.org/?mlat={lat}&mlon={lon}&zoom=17"
            gmaps_url = f"https://maps.google.com/?q={lat},{lon}"
            map_links = (
                f'<span class="osm-list-map-links">'
                f'<a href="{osm_url}" target="_blank" rel="noopener" title="OpenStreetMap">🗺️</a>'
                f'<span class="osm-list-map-sep" aria-hidden="true">·</span>'
                f'<a href="{gmaps_url}" target="_blank" rel="noopener" title="Google Maps">📍</a>'
                f"</span>"
            )
            return f'<td data-sort-value="{name}">{name}{map_links}</td>'
        return f'<td data-sort-value="{name}">{name}</td>'

    has_tags = any(place.get("tags") for place in places)
    has_url = any(place.get("urls") for place in places)

    # Header
    headers = ["<th>" + field_labels.get("name", "Name") + "</th>"]
    if has_tags:
        headers.append("<th>" + field_labels.get("tags", "Tags") + "</th>")
    headers += [f"<th>{col_header(f)}</th>" for f in fields]
    if has_url:
        headers.append("<th>" + field_labels.get("urls", "Links") + "</th>")

    # Rows
    rows: list[str] = []
    for place in places:
        cells = [render_name_cell(place)]
        if has_tags:
            cells.append(f"<td>{render_tags(place.get('tags', []))}</td>")
        for f in fields:
            cells.append(f"<td>{place.get(f, '')}</td>")
        if has_url:
            cells.append(f"<td>{render_urls(place.get('urls', []))}</td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")

    return (
        '<div class="osm-place-list-wrapper">\n'
        '<table class="osm-place-list">\n'
        "<thead><tr>" + "".join(headers) + "</tr></thead>\n"
        "<tbody>\n" + "\n".join(rows) + "\n</tbody>\n"
        "</table>\n"
        '<div class="osm-place-list-count"></div>\n'
        "</div>"
    )


def _process_content(content: str, resolver: PlaceResolver, settings: dict) -> str:
    """Replace all {% place ... %} and {% place_list ... %} shortcodes in content."""
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
        specs = [s.strip() for s in raw_args.split(",") if s.strip()]

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

        return _render_place_html(
            geojson_entries, names, map_height, tile_url, attribution, images_dict
        )

    result = cast(str, pattern.sub(replace, content))

    # ── place_list shortcode ──────────────────────────────────────
    list_shortcode = settings.get("OSM_LIST_SHORTCODE", DEFAULT_LIST_SHORTCODE)
    list_pattern = re.compile(
        r"\{%\s*" + re.escape(list_shortcode) + r"\s+(.+?)\s*%\}",
        re.DOTALL,
    )

    def replace_list(match: re.Match) -> str:
        specs = [s.strip() for s in match.group(1).split(",") if s.strip()]
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
        return _render_place_list_html(places, list_fields, field_labels)

    result = cast(str, list_pattern.sub(replace_list, result))
    return result


_resolver: PlaceResolver | None = None
_settings: dict = {}
_article_url_map: dict[str, str] = {}
_content_path: Path | None = None


def _init_resolver(pelican_obj) -> None:
    global _resolver, _settings, _article_url_map, _content_path
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
    log.warning("pelican-osm: content_path=%s", content_path)
    log.warning("pelican-osm: places root=%s exists=%s", root, root.exists())


def _process_article(content) -> None:
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
        _article_url_map[src] = abs_url
        if _content_path:
            try:
                rel = Path(src).relative_to(_content_path)
                _article_url_map[str(rel)] = abs_url
            except ValueError:
                pass

    if _resolver is None:
        return

    if hasattr(content, "_content"):
        content._content = _process_content(content._content, _resolver, _settings)


def _build_article_url_map(pelican_obj) -> dict[str, str]:
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
        if k in ("lat", "lon", "images"):
            continue
        if v == "" or v == [] or v is None:
            continue
        if isinstance(v, (datetime.date, datetime.datetime)):
            v = v.isoformat()
        properties[k] = v

    if "urls" in properties:
        properties["urls"] = _normalize_url_field(properties["urls"], article_url_map)

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


def _export_geojson(pelican_obj) -> None:
    """Convert every YAML under places root to a .geojson file in output/static/places/."""
    if _resolver is None:
        return

    output = Path(pelican_obj.settings.get("OUTPUT_PATH", "output"))
    root = _resolver.root

    if not root.exists():
        return

    yaml_files = sorted(f for f in root.rglob("*") if f.suffix in (".yml", ".yaml"))

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


def _copy_static(pelican_obj) -> None:
    """Copy bundled static assets (JS/CSS) to output."""
    static_src = Path(__file__).parent / "static"
    output = Path(pelican_obj.settings.get("OUTPUT_PATH", "output"))
    dest = output / "static" / "pelican_osm"

    if static_src.exists():
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(static_src, dest)
        log.debug("pelican-osm: copied static assets to %s", dest)


def register():
    signals.initialized.connect(_init_resolver)
    signals.content_object_init.connect(_process_article)
    signals.finalized.connect(_copy_static)
    signals.finalized.connect(_export_geojson)
