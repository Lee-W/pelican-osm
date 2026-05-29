"""Tests for pelican-osm plugin (current architecture).

Architecture summary:
  - YAML files are loaded via _load_yaml_file (3 formats)
  - PlaceResolver maps shortcode specs to YAML paths or place dicts
  - _process_content replaces {% place %} with HTML that references GeoJSON URLs
  - _export_geojson converts every YAML to a .geojson file at build time
  - JS fetches the .geojson files at runtime
"""

from __future__ import annotations

import datetime
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from pelican import signals

import pelican.plugins.osm.osm as osm_module

from pelican.plugins.osm.osm import (
    PlaceResolver,
    _aggregate_field,
    _build_popup_field_labels,
    _collapse_places,
    _copy_static,
    _expand_items,
    _export_geojson,
    _extract_year,
    _extract_years,
    _find_schema_for,
    _geojson_url,
    _init_resolver,
    _is_place_yaml,
    _load_yaml_file,
    _merge_place,
    _normalize_url_field,
    _parse_aggregate_kwarg,
    _parse_csv_kwarg,
    _parse_shortcode_args,
    _place_to_feature,
    _process_article,
    _process_content,
    _register_markdown_extension,
    _render_place_html,
    _render_place_list_html,
    _resolve_group_count_template,
    _resolve_i18n_title,
    _resolve_image_url,
    _resolve_schema_properties,
    _safe_url,
    _ShortcodePreserveExtension,
    _validate_place,
    _validate_yaml_files,
    _walk_schema_properties,
    _yaml_to_geojson,
    register,
)

DEFAULT_SETTINGS = {
    "OSM_SHORTCODE": "place",
    "OSM_MAP_HEIGHT": "400px",
    "OSM_MAP_TILE": "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
    "OSM_MAP_ATTRIBUTION": "&copy; OpenStreetMap contributors",
    "OSM_STATIC_PREFIX": "/static",
}

TILE = DEFAULT_SETTINGS["OSM_MAP_TILE"]
ATTR = DEFAULT_SETTINGS["OSM_MAP_ATTRIBUTION"]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def places_root(tmp_path: Path) -> Path:
    root = tmp_path / "places"

    # japan/mygo.yaml — locations format (.yaml extension)
    japan = root / "japan"
    japan.mkdir(parents=True)
    (japan / "mygo.yaml").write_text(
        yaml.dump(
            {
                "anime": "BanG Dream! It's MyGO!!!!!",
                "tags": ["動畫"],
                "locations": [
                    {
                        "name": "豊島区立南池袋第二公園",
                        "lat": 35.7225,
                        "lon": 139.7170,
                        "date": "2026-02-22",
                        "category": "公園",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    # japan/tamako.yml — locations format (.yml extension), multiple places
    (japan / "tamako.yml").write_text(
        yaml.dump(
            {
                "anime": "玉子市場",
                "tags": ["動畫"],
                "locations": [
                    {
                        "name": "出町桝形商店街",
                        "lat": 35.0303,
                        "lon": 135.7690,
                        "category": "商店街",
                    },
                    {
                        "name": "鴨川デルタ",
                        "lat": 35.0297,
                        "lon": 135.7717,
                        "category": "景點",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    # taiwan.yml — dict-of-places format
    (root / "taiwan.yml").write_text(
        yaml.dump(
            {
                "defaults": {"country": "Taiwan"},
                "taipei101": {"name": "台北101", "lat": 25.0337, "lon": 121.5645},
                "taroko": {"name": "太魯閣", "lat": 24.1558, "lon": 121.6213},
            }
        ),
        encoding="utf-8",
    )

    # bare.yml — bare list format
    (root / "bare.yml").write_text(
        yaml.dump(
            [
                {"name": "Place A", "lat": 1.0, "lon": 2.0},
                {"name": "Place B", "lat": 3.0, "lon": 4.0},
            ]
        ),
        encoding="utf-8",
    )

    return root


@pytest.fixture()
def resolver(places_root: Path) -> PlaceResolver:
    return PlaceResolver(places_root)


# ---------------------------------------------------------------------------
# _load_yaml_file
# ---------------------------------------------------------------------------


class TestLoadYamlFile:
    def test_locations_format_returns_places(self, places_root):
        places = _load_yaml_file(places_root / "japan" / "mygo.yaml")
        assert len(places) == 1
        assert places[0]["name"] == "豊島区立南池袋第二公園"

    def test_locations_format_file_metadata_becomes_defaults(self, places_root):
        places = _load_yaml_file(places_root / "japan" / "mygo.yaml")
        assert places[0]["anime"] == "BanG Dream! It's MyGO!!!!!"
        assert places[0]["tags"] == ["動畫"]

    def test_locations_format_multiple_places(self, places_root):
        places = _load_yaml_file(places_root / "japan" / "tamako.yml")
        assert len(places) == 2
        names = {p["name"] for p in places}
        assert names == {"出町桝形商店街", "鴨川デルタ"}

    def test_locations_format_place_overrides_default(self, tmp_path):
        yml = tmp_path / "test.yml"
        yml.write_text(
            "country: Japan\n"
            "locations:\n"
            "  - name: A\n"
            "    lat: 1.0\n"
            "    lon: 2.0\n"
            "    country: Taiwan\n",
            encoding="utf-8",
        )
        places = _load_yaml_file(yml)
        assert places[0]["country"] == "Taiwan"

    def test_dict_format_loads_places(self, places_root):
        places = _load_yaml_file(places_root / "taiwan.yml")
        assert len(places) == 2
        ids = {p["id"] for p in places}
        assert ids == {"taipei101", "taroko"}

    def test_dict_format_defaults_applied(self, places_root):
        places = _load_yaml_file(places_root / "taiwan.yml")
        for p in places:
            assert p["country"] == "Taiwan"

    def test_dict_format_place_overrides_default(self, tmp_path):
        yml = tmp_path / "test.yml"
        yml.write_text(
            "defaults:\n  country: Japan\n"
            "ueno:\n  name: 上野公園\n  lat: 35.7\n  lon: 139.7\n  country: Override\n",
            encoding="utf-8",
        )
        places = _load_yaml_file(yml)
        assert places[0]["country"] == "Override"

    def test_bare_list_format(self, places_root):
        places = _load_yaml_file(places_root / "bare.yml")
        assert len(places) == 2
        assert {p["name"] for p in places} == {"Place A", "Place B"}

    def test_bare_list_with_defaults_item(self, tmp_path):
        yml = tmp_path / "test.yml"
        yml.write_text(
            "- defaults:\n    country: Japan\n- name: A\n  lat: 1.0\n  lon: 2.0\n",
            encoding="utf-8",
        )
        places = _load_yaml_file(yml)
        assert len(places) == 1
        assert places[0]["country"] == "Japan"

    def test_empty_file_returns_empty_list(self, tmp_path):
        yml = tmp_path / "empty.yml"
        yml.write_text("", encoding="utf-8")
        assert _load_yaml_file(yml) == []

    def test_date_loaded_as_string_or_date_object(self, tmp_path):
        """YAML may parse unquoted dates as datetime.date — acceptable either way."""
        import datetime

        yml = tmp_path / "test.yml"
        yml.write_text(
            "locations:\n  - name: A\n    lat: 1.0\n    lon: 2.0\n    date: 2026-02-22\n",
            encoding="utf-8",
        )
        places = _load_yaml_file(yml)
        assert places[0]["date"] in ("2026-02-22", datetime.date(2026, 2, 22))

    # ── tag append-merge across all 3 formats ─────────────────────────────

    def test_locations_format_tags_unioned_with_per_location(self, tmp_path):
        yml = tmp_path / "test.yml"
        yml.write_text(
            "tags: [動畫]\n"
            "locations:\n"
            "  - name: A\n    lat: 1.0\n    lon: 2.0\n    tags: [已歇業]\n",
            encoding="utf-8",
        )
        places = _load_yaml_file(yml)
        assert places[0]["tags"] == ["動畫", "已歇業"]

    def test_locations_format_empty_per_location_tags_preserve_file_tags(
        self, tmp_path
    ):
        # Regression: previously `tags: []` overrode file-level tags
        yml = tmp_path / "test.yml"
        yml.write_text(
            "tags: [動畫]\n"
            "locations:\n"
            "  - name: A\n    lat: 1.0\n    lon: 2.0\n    tags: []\n",
            encoding="utf-8",
        )
        places = _load_yaml_file(yml)
        assert places[0]["tags"] == ["動畫"]

    def test_dict_format_tags_unioned(self, tmp_path):
        yml = tmp_path / "test.yml"
        yml.write_text(
            "defaults:\n  tags: [coworking]\n"
            "spot:\n  name: A\n  lat: 1.0\n  lon: 2.0\n  tags: [台北]\n",
            encoding="utf-8",
        )
        places = _load_yaml_file(yml)
        assert places[0]["tags"] == ["coworking", "台北"]

    def test_bare_list_format_tags_unioned(self, tmp_path):
        yml = tmp_path / "test.yml"
        yml.write_text(
            "- defaults:\n    tags: [動畫]\n"
            "- name: A\n  lat: 1.0\n  lon: 2.0\n  tags: [已歇業]\n",
            encoding="utf-8",
        )
        places = _load_yaml_file(yml)
        assert places[0]["tags"] == ["動畫", "已歇業"]


# ---------------------------------------------------------------------------
# _merge_place
# ---------------------------------------------------------------------------


class TestMergePlace:
    def test_non_tag_fields_overridden_by_per_place(self):
        result = _merge_place({"country": "Japan"}, {"country": "Taiwan"})
        assert result["country"] == "Taiwan"

    def test_per_place_only_fields_kept(self):
        result = _merge_place({}, {"name": "A"})
        assert result["name"] == "A"

    def test_file_only_fields_propagate(self):
        result = _merge_place({"anime": "MyGO"}, {"name": "A"})
        assert result["anime"] == "MyGO"
        assert result["name"] == "A"

    def test_tags_unioned_with_file_first(self):
        result = _merge_place({"tags": ["動畫"]}, {"tags": ["已歇業"]})
        assert result["tags"] == ["動畫", "已歇業"]

    def test_tag_duplicates_collapsed(self):
        result = _merge_place({"tags": ["動畫", "電影"]}, {"tags": ["動畫"]})
        assert result["tags"] == ["動畫", "電影"]

    def test_empty_per_place_tags_preserves_file_tags(self):
        result = _merge_place({"tags": ["動畫"]}, {"tags": []})
        assert result["tags"] == ["動畫"]

    def test_no_per_place_tags_preserves_file_tags(self):
        result = _merge_place({"tags": ["動畫"]}, {})
        assert result["tags"] == ["動畫"]

    def test_no_file_tags_keeps_per_place_tags(self):
        result = _merge_place({}, {"tags": ["已歇業"]})
        assert result["tags"] == ["已歇業"]

    def test_neither_has_tags_no_tags_key(self):
        result = _merge_place({"country": "Japan"}, {"name": "A"})
        assert "tags" not in result


# ---------------------------------------------------------------------------
# _expand_items
# ---------------------------------------------------------------------------


class TestExpandItems:
    def test_no_items_passes_through(self):
        result = _expand_items([{"name": "A", "lat": 1.0, "lon": 2.0, "country": "JP"}])
        assert result == [{"name": "A", "lat": 1.0, "lon": 2.0, "country": "JP"}]

    def test_empty_items_drops_key(self):
        result = _expand_items([{"name": "A", "lat": 1.0, "lon": 2.0, "items": []}])
        assert result == [{"name": "A", "lat": 1.0, "lon": 2.0}]

    def test_items_expand_inheriting_parent_fields(self):
        result = _expand_items(
            [
                {
                    "name": "T",
                    "lat": 1.0,
                    "lon": 2.0,
                    "country": "TW",
                    "items": [
                        {"hall": "Hall 1", "rows": "G"},
                        {"hall": "Hall 2", "rows": "E"},
                    ],
                }
            ]
        )
        assert len(result) == 2
        assert result[0]["hall"] == "Hall 1"
        assert result[0]["name"] == "T"
        assert result[0]["lat"] == 1.0
        assert result[0]["country"] == "TW"
        assert result[0]["rows"] == "G"
        assert result[1]["hall"] == "Hall 2"
        assert result[1]["country"] == "TW"
        assert result[1]["rows"] == "E"
        assert "items" not in result[0]

    def test_item_field_overrides_non_protected_parent_field(self):
        # Item-level fields win on collision for everything except the
        # parent-only set (name/lat/lon).
        result = _expand_items(
            [
                {
                    "name": "P",
                    "lat": 1.0,
                    "lon": 2.0,
                    "note": "parent",
                    "items": [{"hall": "H", "note": "child"}],
                }
            ]
        )
        assert result[0]["note"] == "child"

    def test_item_name_is_dropped(self, caplog):
        # Items are sub-rows of one place; ``name`` belongs to the parent.
        # A name on an item is dropped (parent wins) and a warning is logged.
        with caplog.at_level("WARNING"):
            result = _expand_items(
                [
                    {
                        "name": "Parent",
                        "lat": 1.0,
                        "lon": 2.0,
                        "items": [{"name": "Child", "hall": "H"}],
                    }
                ]
            )
        assert result[0]["name"] == "Parent"
        assert result[0]["hall"] == "H"
        assert any("'name'" in rec.message for rec in caplog.records)

    def test_item_lat_lon_are_dropped(self, caplog):
        with caplog.at_level("WARNING"):
            result = _expand_items(
                [
                    {
                        "name": "Parent",
                        "lat": 1.0,
                        "lon": 2.0,
                        "items": [{"hall": "H", "lat": 99.0, "lon": 88.0}],
                    }
                ]
            )
        assert result[0]["lat"] == 1.0
        assert result[0]["lon"] == 2.0
        msgs = " ".join(rec.message for rec in caplog.records)
        assert "'lat'" in msgs
        assert "'lon'" in msgs

    def test_tags_union_parent_then_item(self):
        result = _expand_items(
            [
                {
                    "name": "P",
                    "lat": 1.0,
                    "lon": 2.0,
                    "tags": ["theater"],
                    "items": [{"hall": "H1", "tags": ["imax"]}],
                }
            ]
        )
        assert result[0]["tags"] == ["theater", "imax"]

    def test_non_dict_items_skipped(self):
        result = _expand_items(
            [
                {
                    "name": "P",
                    "lat": 1.0,
                    "lon": 2.0,
                    "items": ["not a dict", {"hall": "H1"}, 42],
                }
            ]
        )
        assert len(result) == 1
        assert result[0]["hall"] == "H1"

    def test_mixed_with_and_without_items(self):
        result = _expand_items(
            [
                {"name": "Solo", "lat": 1.0, "lon": 2.0},
                {
                    "name": "Group",
                    "lat": 3.0,
                    "lon": 4.0,
                    "items": [{"hall": "G1"}, {"hall": "G2"}],
                },
            ]
        )
        # Parent's name flows to every expanded row; items add ``hall``.
        assert [p["name"] for p in result] == ["Solo", "Group", "Group"]
        assert [p.get("hall") for p in result] == [None, "G1", "G2"]
        assert result[1]["lat"] == 3.0
        assert result[2]["lat"] == 3.0


# ---------------------------------------------------------------------------
# _render_place_list_html with items
# ---------------------------------------------------------------------------


class TestRenderPlaceListHtmlItems:
    def test_items_render_as_separate_rows(self):
        places = [
            {
                "name": "松仁威秀",
                "lat": 25.03,
                "lon": 121.56,
                "country": "TW",
                "items": [
                    {"hall": "6 廳（TITAN）", "rows": "G"},
                    {"hall": "2 廳", "rows": "E"},
                ],
            }
        ]
        html = _render_place_list_html(places, [], {})
        assert html.count("<tr") == 3  # 2 data rows + 1 thead; data rows carry id/class
        assert "6 廳（TITAN）" in html
        assert "2 廳" in html
        # Parent's name cascades to every row's name cell (one row per item).
        assert html.count('data-sort-value="松仁威秀"') == 2

    def test_group_summary_at_with_parent_field(self):
        places = [
            {
                "name": "T1",
                "country": "TW",
                "lat": 1.0,
                "lon": 2.0,
                "items": [{"hall": "H1"}, {"hall": "H2"}],
            },
            {
                "name": "T2",
                "country": "JP",
                "lat": 3.0,
                "lon": 4.0,
                "items": [{"hall": "H3"}],
            },
        ]
        html = _render_place_list_html(
            places,
            [],
            {},
            group_by=["country", "name", "hall"],
            group_summary_at=["country"],
        )
        assert "TW" in html
        assert "JP" in html
        # 3 data rows (T1+H1, T1+H2, T2+H3), each with a row anchor.
        assert html.count('id="osm-place-') == 3

    def test_items_field_not_a_column(self):
        places = [
            {
                "name": "T",
                "lat": 1.0,
                "lon": 2.0,
                "items": [{"hall": "H1", "rows": "G"}],
            }
        ]
        html = _render_place_list_html(places, [], {})
        # The 'items' key should not appear as a column header
        assert "<th>Items</th>" not in html

    def test_items_not_merged_when_group_by_matches_parent_only(self):
        # Regression: with group_by="country,name" (parent's identifying
        # fields) and *no* aggregate, items of one parent share the same
        # group-key tuple. They must still render as separate rows — auto
        # SQL-style collapse here would silently destroy per-hall data.
        places = [
            {
                "name": "T1",
                "lat": 1.0,
                "lon": 2.0,
                "country": "TW",
                "items": [
                    {"hall": "H1", "rows": "G"},
                    {"hall": "H2", "rows": "E"},
                    {"hall": "H3", "rows": "F"},
                ],
            }
        ]
        html = _render_place_list_html(
            places,
            [],
            {},
            group_by=["country", "name"],
            group_summary_at=["country", "name"],
        )
        tbody = re.search(r"<tbody>(.*?)</tbody>", html, re.DOTALL).group(1)
        # Three data rows (H1/H2/H3); group headers don't live inside <tr>
        # without class — strip those out by counting only data <tr>.
        data_rows = [
            line for line in tbody.split("<tr") if "osm-group-header" not in line
        ]
        # First split chunk is empty preamble, so subtract 1.
        assert len(data_rows) - 1 == 3
        assert "H1" in html
        assert "H2" in html
        assert "H3" in html

    def test_name_in_summary_drops_name_column(self):
        # When name is hoisted into group_summary_at, the data rows no
        # longer carry a Name column — each row is identified by its
        # item-specific field (e.g. ``hall``).
        places = [
            {
                "name": "T1",
                "lat": 1.0,
                "lon": 2.0,
                "country": "TW",
                "items": [{"hall": "H1"}, {"hall": "H2"}],
            }
        ]
        html = _render_place_list_html(
            places,
            [],
            {},
            group_by=["country", "name"],
            group_summary_at=["country", "name"],
        )
        thead = re.search(r"<thead>(.*?)</thead>", html, re.DOTALL).group(1)
        assert "<th>Name</th>" not in thead
        # Hall column is still present
        assert ">Hall<" in thead

    def test_name_summary_header_includes_map_links(self):
        # When name is the summary field at a depth, the header for that
        # depth carries the place's 🗺️·📍 map links (same span structure
        # used in data-row name cells).
        places = [
            {
                "name": "T1",
                "lat": 25.03,
                "lon": 121.56,
                "country": "TW",
                "items": [{"hall": "H1"}],
            }
        ]
        html = _render_place_list_html(
            places,
            [],
            {},
            group_by=["country", "name"],
            group_summary_at=["country", "name"],
        )
        # Find the depth-1 header (where name lives) and verify it carries
        # the map-links span. The depth-0 header (country) must NOT have it.
        depth1 = re.search(
            r'<tr class="osm-group-header osm-group-header--depth-1"[^>]*>(.*?)</tr>',
            html,
        )
        depth0 = re.search(
            r'<tr class="osm-group-header osm-group-header--depth-0"[^>]*>(.*?)</tr>',
            html,
        )
        assert depth1 is not None and depth0 is not None
        assert "osm-list-map-links" in depth1.group(1)
        assert "mlat=25.03" in depth1.group(1)
        assert "osm-list-map-links" not in depth0.group(1)


# ---------------------------------------------------------------------------
# _place_to_feature with items
# ---------------------------------------------------------------------------


class TestPlaceToFeatureItems:
    def test_items_stripped_from_geojson_properties(self):
        feat = _place_to_feature(
            {
                "name": "T",
                "lat": 1.0,
                "lon": 2.0,
                "country": "TW",
                "items": [{"hall": "H1"}, {"hall": "H2"}],
            }
        )
        assert "items" not in feat["properties"]
        assert feat["properties"]["name"] == "T"
        assert feat["properties"]["country"] == "TW"


# ---------------------------------------------------------------------------
# _parse_shortcode_args
# ---------------------------------------------------------------------------


class TestParseShortcodeArgs:
    def test_legacy_single_spec(self):
        positional, kwargs = _parse_shortcode_args("pilgrimage")
        assert positional == ["pilgrimage"]
        assert kwargs == {}

    def test_legacy_comma_separated_specs(self):
        positional, kwargs = _parse_shortcode_args("japan/tamako.yml, taiwan.yml")
        assert positional == ["japan/tamako.yml", "taiwan.yml"]
        assert kwargs == {}

    def test_legacy_no_split_on_kwargs_when_no_equals(self):
        # No `=` means we never invoke shlex; this is the backwards-compat path
        positional, kwargs = _parse_shortcode_args("a, b , c")
        assert positional == ["a", "b", "c"]
        assert kwargs == {}

    def test_kwarg_with_unquoted_value(self):
        positional, kwargs = _parse_shortcode_args("pilgrimage sort=date:asc")
        assert positional == ["pilgrimage"]
        assert kwargs == {"sort": "date:asc"}

    def test_kwarg_with_quoted_comma_value(self):
        positional, kwargs = _parse_shortcode_args(
            'pilgrimage group_by="anime,country,city"'
        )
        assert positional == ["pilgrimage"]
        assert kwargs == {"group_by": "anime,country,city"}

    def test_multiple_kwargs(self):
        positional, kwargs = _parse_shortcode_args(
            'pilgrimage group_by="anime" aggregate="date:year" sort=date:asc'
        )
        assert positional == ["pilgrimage"]
        assert kwargs == {
            "group_by": "anime",
            "aggregate": "date:year",
            "sort": "date:asc",
        }

    def test_positional_plus_kwarg(self):
        positional, kwargs = _parse_shortcode_args(
            'japan/tamako.yml, taiwan.yml group_by="country"'
        )
        assert positional == ["japan/tamako.yml", "taiwan.yml"]
        assert kwargs == {"group_by": "country"}

    def test_only_kwargs(self):
        positional, kwargs = _parse_shortcode_args('group_by="anime"')
        assert positional == []
        assert kwargs == {"group_by": "anime"}


# ---------------------------------------------------------------------------
# _parse_csv_kwarg, _parse_aggregate_kwarg
# ---------------------------------------------------------------------------


class TestParseCsvKwarg:
    def test_single(self):
        assert _parse_csv_kwarg("anime") == ["anime"]

    def test_multiple(self):
        assert _parse_csv_kwarg("anime,country,city") == ["anime", "country", "city"]

    def test_strips_whitespace(self):
        assert _parse_csv_kwarg(" anime , country ") == ["anime", "country"]

    def test_empty(self):
        assert _parse_csv_kwarg("") == []

    def test_drops_empty_tokens(self):
        assert _parse_csv_kwarg("a,,b") == ["a", "b"]


class TestParseAggregateKwarg:
    def test_single(self):
        assert _parse_aggregate_kwarg("date:year") == {"date": "year"}

    def test_multiple(self):
        assert _parse_aggregate_kwarg("date:year,visits:sum") == {
            "date": "year",
            "visits": "sum",
        }

    def test_empty(self):
        assert _parse_aggregate_kwarg("") == {}

    def test_token_without_colon_is_ignored(self):
        # ``oops`` has no ``:`` and is silently dropped; the rest still parses
        assert _parse_aggregate_kwarg("date:year,oops") == {"date": "year"}

    def test_strips_whitespace(self):
        assert _parse_aggregate_kwarg(" date : year ") == {"date": "year"}


# ---------------------------------------------------------------------------
# _extract_year
# ---------------------------------------------------------------------------


class TestExtractYear:
    def test_date_object(self):
        assert _extract_year(datetime.date(2024, 5, 1)) == 2024

    def test_datetime_object(self):
        assert _extract_year(datetime.datetime(2024, 5, 1, 10, 30)) == 2024

    def test_iso_string(self):
        assert _extract_year("2024-05-01") == 2024

    def test_year_only_string(self):
        assert _extract_year("2024") == 2024

    def test_int_year(self):
        assert _extract_year(2024) == 2024

    def test_int_out_of_range(self):
        # A small int isn't a year; we don't want stray counts becoming dates
        assert _extract_year(42) is None

    def test_none(self):
        assert _extract_year(None) is None

    def test_empty_string(self):
        assert _extract_year("") is None

    def test_garbage_string(self):
        assert _extract_year("foo") is None

    def test_list_returns_first_year(self):
        assert _extract_year(["2024-05-01", "2025-03-10"]) == 2024

    def test_list_skips_non_dates(self):
        assert _extract_year(["foo", "2025-01-01"]) == 2025

    def test_empty_list(self):
        assert _extract_year([]) is None


# ---------------------------------------------------------------------------
# _extract_years
# ---------------------------------------------------------------------------


class TestExtractYears:
    def test_single_date(self):
        assert _extract_years("2024-05-01") == [2024]

    def test_list_of_dates(self):
        assert _extract_years(["2024-05-01", "2025-03-10"]) == [2024, 2025]

    def test_list_with_date_objects(self):
        assert _extract_years(
            [datetime.date(2024, 5, 1), datetime.date(2025, 3, 10)]
        ) == [2024, 2025]

    def test_list_skips_non_dates(self):
        assert _extract_years(["foo", "2025-01-01"]) == [2025]

    def test_none(self):
        assert _extract_years(None) == []

    def test_empty_list(self):
        assert _extract_years([]) == []


# ---------------------------------------------------------------------------
# _aggregate_field
# ---------------------------------------------------------------------------


class TestAggregateField:
    def test_year_unique_sorted(self):
        places = [
            {"date": "2023-01-01"},
            {"date": "2018-05-05"},
            {"date": "2023-12-31"},
        ]
        assert _aggregate_field("year", "date", places) == "2018, 2023"

    def test_year_with_missing_dates(self):
        places = [{"date": ""}, {}, {"date": None}]
        assert _aggregate_field("year", "date", places) == ""

    def test_year_mixed_types(self):
        places = [
            {"date": datetime.date(2024, 5, 1)},
            {"date": "2025-01-01"},
        ]
        assert _aggregate_field("year", "date", places) == "2024, 2025"

    def test_year_with_list_dates(self):
        places = [
            {"date": ["2018-05-01", "2019-03-10"]},
            {"date": ["2023-06-01"]},
        ]
        assert _aggregate_field("year", "date", places) == "2018, 2019, 2023"

    def test_year_mixed_single_and_list(self):
        places = [
            {"date": "2018-05-01"},
            {"date": ["2019-03-10", "2020-11-01"]},
        ]
        assert _aggregate_field("year", "date", places) == "2018, 2019, 2020"

    def test_unknown_op_returns_empty(self):
        # Unknown ops are logged and produce empty string rather than raising
        assert _aggregate_field("nonexistent", "date", [{"date": "2024"}]) == ""


# ---------------------------------------------------------------------------
# _collapse_places
# ---------------------------------------------------------------------------


class TestCollapsePlaces:
    # Without aggregate: rows are preserved, just bucketed for tree rendering.

    def test_no_aggregate_preserves_every_row(self):
        places = [
            {"name": "A", "anime": "X"},
            {"name": "B", "anime": "X"},
            {"name": "C", "anime": "Y"},
        ]
        rows = _collapse_places(places, ["anime"], {})
        assert len(rows) == 3
        assert all(len(r["_places"]) == 1 for r in rows)
        assert [r["name"] for r in rows] == ["A", "B", "C"]

    def test_no_aggregate_buckets_interleaved_keys_into_contiguous_runs(self):
        # Z first appears at index 0, X at index 1; Z's bucket emits first
        # (with both Z rows contiguous in original order), then X.
        places = [
            {"name": "A", "anime": "Z"},
            {"name": "B", "anime": "X"},
            {"name": "C", "anime": "Z"},
        ]
        rows = _collapse_places(places, ["anime"], {})
        assert [r["name"] for r in rows] == ["A", "C", "B"]

    def test_no_aggregate_keeps_per_row_field_values(self):
        # First-non-empty merging only applies when aggregating; without it,
        # every row keeps its own values verbatim.
        places = [
            {"name": "A", "anime": "X", "category": ""},
            {"name": "B", "anime": "X", "category": "商店街"},
        ]
        rows = _collapse_places(places, ["anime"], {})
        assert [r["category"] for r in rows] == ["", "商店街"]

    def test_no_aggregate_does_not_union_tags(self):
        places = [
            {"name": "A", "anime": "X", "tags": ["動畫"]},
            {"name": "B", "anime": "X", "tags": ["已歇業"]},
        ]
        rows = _collapse_places(places, ["anime"], {})
        assert [r["tags"] for r in rows] == [["動畫"], ["已歇業"]]

    def test_no_aggregate_multi_field_buckets_by_tuple(self):
        places = [
            {"name": "A", "anime": "X", "city": "K"},
            {"name": "B", "anime": "X", "city": "T"},
            {"name": "C", "anime": "X", "city": "K"},
        ]
        rows = _collapse_places(places, ["anime", "city"], {})
        assert [r["name"] for r in rows] == ["A", "C", "B"]

    # With aggregate: SQL-style collapse + merge.

    def test_aggregate_collapses_into_one_row_per_key(self):
        places = [
            {"name": "A", "anime": "X", "date": "2018-05-01"},
            {"name": "B", "anime": "X", "date": "2023-06-01"},
            {"name": "C", "anime": "Y", "date": "2024-01-01"},
        ]
        rows = _collapse_places(places, ["anime"], {"date": "year"})
        assert len(rows) == 2
        x_row = next(r for r in rows if r["anime"] == "X")
        assert len(x_row["_places"]) == 2

    def test_aggregate_multi_field_collapses_by_tuple(self):
        places = [
            {"name": "A", "anime": "X", "city": "K", "date": "2018"},
            {"name": "B", "anime": "X", "city": "T", "date": "2019"},
            {"name": "C", "anime": "X", "city": "K", "date": "2020"},
        ]
        rows = _collapse_places(places, ["anime", "city"], {"date": "year"})
        assert len(rows) == 2
        k_row = next(r for r in rows if r["city"] == "K")
        assert len(k_row["_places"]) == 2

    def test_aggregate_year_collapses_dates(self):
        places = [
            {"name": "A", "anime": "X", "date": "2018-05-01"},
            {"name": "B", "anime": "X", "date": "2023-06-01"},
            {"name": "C", "anime": "X", "date": "2018-09-01"},
        ]
        rows = _collapse_places(places, ["anime"], {"date": "year"})
        assert rows[0]["date"] == "2018, 2023"

    def test_aggregate_year_with_all_missing_dates_yields_empty(self):
        places = [
            {"name": "A", "anime": "X", "date": ""},
            {"name": "B", "anime": "X"},
        ]
        rows = _collapse_places(places, ["anime"], {"date": "year"})
        assert rows[0]["date"] == ""

    def test_aggregate_unions_tags_across_group(self):
        places = [
            {"name": "A", "anime": "X", "date": "2018", "tags": ["動畫"]},
            {"name": "B", "anime": "X", "date": "2019", "tags": ["已歇業"]},
        ]
        rows = _collapse_places(places, ["anime"], {"date": "year"})
        assert rows[0]["tags"] == ["動畫", "已歇業"]

    def test_aggregate_first_non_empty_wins_for_non_aggregate_fields(self):
        places = [
            {"name": "A", "anime": "X", "date": "2018", "category": ""},
            {"name": "B", "anime": "X", "date": "2019", "category": "商店街"},
        ]
        rows = _collapse_places(places, ["anime"], {"date": "year"})
        assert rows[0]["category"] == "商店街"

    def test_aggregate_order_preserves_first_appearance_of_keys(self):
        places = [
            {"name": "A", "anime": "Z", "date": "2018"},
            {"name": "B", "anime": "X", "date": "2019"},
            {"name": "C", "anime": "Z", "date": "2020"},
        ]
        rows = _collapse_places(places, ["anime"], {"date": "year"})
        assert [r["anime"] for r in rows] == ["Z", "X"]

    def test_aggregate_field_not_overwritten_by_first_non_empty(self):
        # The aggregate logic should win even if the original place had a
        # value: the row date should be replaced with the aggregated string.
        places = [
            {"name": "A", "anime": "X", "date": "2018-05-01"},
            {"name": "B", "anime": "X", "date": "2023-06-01"},
        ]
        rows = _collapse_places(places, ["anime"], {"date": "year"})
        assert rows[0]["date"] == "2018, 2023"


# ---------------------------------------------------------------------------
# _render_place_list_html (grouping/aggregation paths)
# ---------------------------------------------------------------------------


class TestRenderPlaceListHtml:
    def test_empty_returns_comment(self):
        assert (
            _render_place_list_html([], [], {})
            == "<!-- pelican-osm: no places for list -->"
        )

    def test_flat_path_no_group_header(self):
        places = [
            {"name": "A", "lat": 1.0, "lon": 2.0, "city": "K"},
            {"name": "B", "lat": 3.0, "lon": 4.0, "city": "T"},
        ]
        html = _render_place_list_html(places, [], {})
        assert "osm-group-header" not in html
        assert ">A<" in html
        assert ">B<" in html

    def test_group_by_collapses_rows_in_html(self):
        places = [
            {
                "name": "A1",
                "lat": 1.0,
                "lon": 2.0,
                "anime": "X",
                "city": "K",
                "date": "2018-01-01",
            },
            {
                "name": "A2",
                "lat": 1.0,
                "lon": 2.0,
                "anime": "X",
                "city": "K",
                "date": "2023-01-01",
            },
            {
                "name": "B1",
                "lat": 3.0,
                "lon": 4.0,
                "anime": "Y",
                "city": "T",
                "date": "2024-01-01",
            },
        ]
        html = _render_place_list_html(
            places,
            ["anime", "city", "date"],
            {},
            group_by=["anime", "city"],
            aggregate={"date": "year"},
        )
        # First place name wins as the row's name cell
        assert ">A1<" in html
        # Aggregated years rendered, not raw dates
        assert "2018, 2023" in html
        assert "2018-01-01" not in html

    def test_group_summary_at_emits_header_row(self):
        places = [
            {"name": "A1", "lat": 1.0, "lon": 2.0, "anime": "X", "city": "K"},
            {"name": "A2", "lat": 1.0, "lon": 2.0, "anime": "X", "city": "T"},
            {"name": "B1", "lat": 3.0, "lon": 4.0, "anime": "Y", "city": "Z"},
        ]
        html = _render_place_list_html(
            places,
            ["anime", "city"],
            {},
            group_by=["anime", "city"],
            group_summary_at=["anime"],
        )
        assert "osm-group-header" in html
        assert "colspan=" in html
        # Both anime values appear as header titles
        assert "X" in html
        assert "Y" in html

    def test_group_summary_at_removes_field_from_data_columns(self):
        places = [
            {"name": "A", "lat": 1.0, "lon": 2.0, "anime": "X", "city": "K"},
        ]
        html = _render_place_list_html(
            places,
            ["anime", "city"],
            {},
            group_by=["anime"],
            group_summary_at=["anime"],
        )
        thead = re.search(r"<thead>.*?</thead>", html, re.DOTALL)
        assert thead is not None
        # Name column + city column = 2 <th>; "anime" column was hoisted
        assert thead.group().count("<th>") == 2

    def test_group_summary_at_without_group_by_falls_back_to_flat(self):
        places = [
            {"name": "A", "lat": 1.0, "lon": 2.0, "anime": "X"},
        ]
        html = _render_place_list_html(
            places,
            [],
            {},
            group_summary_at=["anime"],
        )
        assert "osm-group-header" not in html

    def test_group_summary_at_not_a_prefix_falls_back_to_flat(self):
        places = [
            {"name": "A", "lat": 1.0, "lon": 2.0, "anime": "X", "country": "JP"},
        ]
        html = _render_place_list_html(
            places,
            ["anime", "country"],
            {},
            group_by=["anime", "country"],
            group_summary_at=["country"],
        )
        assert "osm-group-header" not in html

    def test_group_count_template_supports_i18n(self):
        # Custom template lets sites render the count in their own language
        # without patching the plugin source.
        places = [
            {"name": "A", "lat": 1.0, "lon": 2.0, "anime": "X"},
            {"name": "B", "lat": 1.0, "lon": 2.0, "anime": "X"},
        ]
        html = _render_place_list_html(
            places,
            [],
            {},
            group_by=["anime"],
            group_summary_at=["anime"],
            group_count_template="{n} 個地點",
        )
        assert "2 個地點" in html
        assert "places" not in html

    def test_group_count_template_empty_omits_count(self):
        places = [
            {"name": "A", "lat": 1.0, "lon": 2.0, "anime": "X"},
        ]
        html = _render_place_list_html(
            places,
            [],
            {},
            group_by=["anime"],
            group_summary_at=["anime"],
            group_count_template="",
        )
        assert "osm-group-header" in html
        assert "osm-group-count" not in html

    def test_group_summary_count_reflects_original_places(self):
        # Two K-ON places collapse into one row but the header should still
        # report "2 places" so the user sees the underlying count.
        places = [
            {
                "name": "A1",
                "lat": 1.0,
                "lon": 2.0,
                "anime": "X",
                "city": "K",
                "date": "2018-01-01",
            },
            {
                "name": "A2",
                "lat": 1.0,
                "lon": 2.0,
                "anime": "X",
                "city": "K",
                "date": "2023-01-01",
            },
        ]
        html = _render_place_list_html(
            places,
            ["anime", "city", "date"],
            {},
            group_by=["anime", "city"],
            aggregate={"date": "year"},
            group_summary_at=["anime"],
        )
        assert "2 places" in html

    def test_group_summary_at_nested_emits_per_depth_headers(self):
        # Two depths → each row gets a depth-0 + depth-1 header on first
        # appearance; depth-0 is NOT repeated when only depth-1 changes.
        places = [
            {"name": "A", "lat": 1.0, "lon": 2.0, "country": "JP", "city": "T"},
            {"name": "B", "lat": 1.0, "lon": 2.0, "country": "JP", "city": "O"},
            {"name": "C", "lat": 1.0, "lon": 2.0, "country": "TW", "city": "TPE"},
        ]
        html = _render_place_list_html(
            places,
            ["country", "city"],
            {},
            group_by=["country", "city"],
            group_summary_at=["country", "city"],
        )
        assert html.count("osm-group-header--depth-0") == 2  # JP, TW
        assert html.count("osm-group-header--depth-1") == 3  # T, O, TPE
        assert 'data-depth="0"' in html
        assert 'data-depth="1"' in html

    def test_group_summary_at_nested_counts_at_each_level(self):
        places = [
            {"name": "A", "lat": 1.0, "lon": 2.0, "country": "JP", "city": "T"},
            {"name": "B", "lat": 1.0, "lon": 2.0, "country": "JP", "city": "O"},
            {"name": "C", "lat": 1.0, "lon": 2.0, "country": "TW", "city": "TPE"},
        ]
        html = _render_place_list_html(
            places,
            ["country", "city"],
            {},
            group_by=["country", "city"],
            group_summary_at=["country", "city"],
        )
        # JP rolls up 2 places, TW rolls up 1, each leaf city has 1.
        assert "2 places" in html
        assert "1 places" in html

    def test_group_summary_at_nested_emits_anchor_ids(self):
        places = [
            {"name": "A", "lat": 1.0, "lon": 2.0, "country": "JP", "city": "Tokyo"},
        ]
        html = _render_place_list_html(
            places,
            ["country", "city"],
            {},
            group_by=["country", "city"],
            group_summary_at=["country", "city"],
        )
        assert 'id="osm-group--jp"' in html
        assert 'id="osm-group--jp--tokyo"' in html

    def test_list_cell_joins_with_default_separator(self):
        # A multi-visit place: list value gets joined for display.
        places = [
            {"name": "A", "lat": 1.0, "lon": 2.0, "date": ["2024-01-01", "2025-03-12"]},
        ]
        html = _render_place_list_html(places, ["date"], {})
        assert "2024-01-01, 2025-03-12" in html

    def test_list_cell_custom_separator_via_schema(self):
        places = [
            {"name": "A", "lat": 1.0, "lon": 2.0, "date": ["2024", "2025"]},
        ]
        html = _render_place_list_html(
            places,
            ["date"],
            {},
            field_schema={"date": {"x-osm-list-join": " · "}},
        )
        assert "2024 · 2025" in html

    def test_list_cell_sort_max_emits_data_sort_value(self):
        places = [
            {"name": "A", "lat": 1.0, "lon": 2.0, "date": ["2024-01-01", "2025-03-12"]},
        ]
        html = _render_place_list_html(
            places,
            ["date"],
            {},
            field_schema={"date": {"x-osm-list-sort": "max"}},
        )
        # Most recent visit is what sort should compare against.
        assert 'data-sort-value="2025-03-12"' in html

    def test_list_cell_sort_min_emits_data_sort_value(self):
        places = [
            {"name": "A", "lat": 1.0, "lon": 2.0, "date": ["2025-03-12", "2024-01-01"]},
        ]
        html = _render_place_list_html(
            places,
            ["date"],
            {},
            field_schema={"date": {"x-osm-list-sort": "min"}},
        )
        assert 'data-sort-value="2024-01-01"' in html

    def test_list_cell_datetime_items_render_iso(self):
        # PyYAML produces datetime.date for unquoted dates; the list cell must
        # not leak Python repr into the HTML.
        places = [
            {
                "name": "A",
                "lat": 1.0,
                "lon": 2.0,
                "date": [datetime.date(2024, 1, 1), datetime.date(2025, 3, 12)],
            },
        ]
        html = _render_place_list_html(
            places,
            ["date"],
            {},
            field_schema={"date": {"x-osm-list-sort": "max"}},
        )
        assert "2024-01-01, 2025-03-12" in html
        assert "datetime.date" not in html
        assert 'data-sort-value="2025-03-12"' in html

    def test_scalar_datetime_renders_iso(self):
        # Backward compat: a scalar date value still renders as ISO.
        places = [
            {"name": "A", "lat": 1.0, "lon": 2.0, "date": datetime.date(2024, 1, 1)},
        ]
        html = _render_place_list_html(places, ["date"], {})
        assert "2024-01-01" in html
        assert "datetime.date" not in html

    def test_list_cell_no_sort_hint_omits_data_sort_value(self):
        places = [
            {"name": "A", "lat": 1.0, "lon": 2.0, "date": ["2024", "2025"]},
        ]
        html = _render_place_list_html(places, ["date"], {})
        # Default behavior: no sort hint → no data-sort-value on the list cell.
        # The Name cell always carries one for icon-text exclusion, so exactly
        # one occurrence overall (the Name cell, not the Date cell).
        assert html.count("data-sort-value=") == 1

    def test_schema_title_drives_column_header(self):
        places = [
            {"name": "A", "lat": 1.0, "lon": 2.0, "date": "2024-01-01"},
        ]
        html = _render_place_list_html(
            places,
            ["date"],
            {},
            field_schema={"date": {"title": "日期"}},
        )
        thead = re.search(r"<thead>.*?</thead>", html, re.DOTALL).group()
        assert "<th>日期</th>" in thead

    def test_schema_title_overrides_field_labels(self):
        places = [
            {"name": "A", "lat": 1.0, "lon": 2.0, "date": "2024-01-01"},
        ]
        html = _render_place_list_html(
            places,
            ["date"],
            {"date": "Visited"},  # OSM_LIST_FIELD_LABELS
            field_schema={"date": {"title": "日期"}},  # schema wins
        )
        thead = re.search(r"<thead>.*?</thead>", html, re.DOTALL).group()
        assert "<th>日期</th>" in thead
        assert "Visited" not in thead

    def test_field_labels_used_when_schema_has_no_title(self):
        places = [
            {"name": "A", "lat": 1.0, "lon": 2.0, "date": "2024-01-01"},
        ]
        html = _render_place_list_html(
            places,
            ["date"],
            {"date": "Visited"},
            field_schema={"date": {"type": "string"}},  # no title
        )
        thead = re.search(r"<thead>.*?</thead>", html, re.DOTALL).group()
        assert "<th>Visited</th>" in thead

    def test_hidden_field_excluded_from_auto_detected_columns(self):
        places = [
            {"name": "A", "lat": 1.0, "lon": 2.0, "city": "Tokyo", "internal": "x"},
        ]
        html = _render_place_list_html(
            places,
            [],  # auto-detect
            {},
            field_schema={"internal": {"x-osm-list-hidden": True}},
        )
        thead = re.search(r"<thead>.*?</thead>", html, re.DOTALL).group()
        assert "internal" not in thead.lower()
        assert "city" in thead.lower() or "City" in thead

    def test_hidden_field_excluded_from_explicit_fields(self):
        places = [
            {"name": "A", "lat": 1.0, "lon": 2.0, "city": "Tokyo", "internal": "x"},
        ]
        html = _render_place_list_html(
            places,
            ["city", "internal"],
            {},
            field_schema={"internal": {"x-osm-list-hidden": True}},
        )
        thead = re.search(r"<thead>.*?</thead>", html, re.DOTALL).group()
        assert "internal" not in thead.lower()

    def test_hidden_field_still_usable_for_group_by(self):
        # Hidden fields stay in the data so group_by/sort/aggregate still work.
        places = [
            {"name": "A", "lat": 1.0, "lon": 2.0, "anime": "X", "internal_order": 1},
            {"name": "B", "lat": 1.0, "lon": 2.0, "anime": "X", "internal_order": 2},
        ]
        html = _render_place_list_html(
            places,
            ["anime"],
            {},
            group_by=["anime"],
            group_summary_at=["anime"],
            field_schema={"internal_order": {"x-osm-list-hidden": True}},
        )
        # Group header still emits, fields rendered, hidden field absent.
        assert "osm-group-header" in html
        assert "internal_order" not in html

    def test_hidden_tags_drops_tags_column(self):
        places = [
            {"name": "A", "lat": 1.0, "lon": 2.0, "tags": ["x"]},
        ]
        html = _render_place_list_html(
            places,
            [],
            {},
            field_schema={"tags": {"x-osm-list-hidden": True}},
        )
        thead = re.search(r"<thead>.*?</thead>", html, re.DOTALL).group()
        assert "<th>Tags</th>" not in thead

    def test_hidden_urls_drops_links_column(self):
        places = [
            {
                "name": "A",
                "lat": 1.0,
                "lon": 2.0,
                "urls": [{"href": "https://example.com"}],
            },
        ]
        html = _render_place_list_html(
            places,
            [],
            {},
            field_schema={"urls": {"x-osm-list-hidden": True}},
        )
        thead = re.search(r"<thead>.*?</thead>", html, re.DOTALL).group()
        assert "<th>Links</th>" not in thead

    def test_schema_title_applies_to_reserved_columns(self):
        # name / tags / urls are special-cased columns but should still honor
        # schema title so the whole header row can be translated from one place.
        places = [
            {
                "name": "A",
                "lat": 1.0,
                "lon": 2.0,
                "tags": ["x"],
                "urls": [{"href": "https://example.com"}],
            },
        ]
        html = _render_place_list_html(
            places,
            [],
            {},
            field_schema={
                "name": {"title": "地點"},
                "tags": {"title": "標籤"},
                "urls": {"title": "連結"},
            },
        )
        thead = re.search(r"<thead>.*?</thead>", html, re.DOTALL).group()
        assert "<th>地點</th>" in thead
        assert "<th>標籤</th>" in thead
        assert "<th>連結</th>" in thead

    def test_images_row_gets_class_icon_and_json_sidecar(self):
        places = [
            {
                "name": "A",
                "lat": 1.0,
                "lon": 2.0,
                "images": ["https://example.com/a.jpg"],
            },
            {"name": "B", "lat": 1.0, "lon": 2.0},
        ]
        html = _render_place_list_html(places, [], {})
        assert "osm-has-images" in html
        assert "osm-list-image-icon" in html
        assert "osm-list-image-col-header" in html
        # Image URLs no longer live inside each <tr>; they ride in one
        # JSON sidecar keyed by row slug, so big tables stay lean.
        assert 'data-images="' not in html
        assert '<script type="application/json" class="osm-list-images">' in html
        sidecar = re.search(
            r'<script type="application/json" class="osm-list-images">(.*?)</script>',
            html,
            re.DOTALL,
        )
        assert sidecar is not None
        payload = json.loads(sidecar.group(1))
        assert payload == {"a": ["https://example.com/a.jpg"]}

    def test_images_no_sidecar_when_no_rows_have_images(self):
        places = [{"name": "A", "lat": 1.0, "lon": 2.0}]
        html = _render_place_list_html(places, [], {})
        assert "osm-list-images" not in html

    def test_images_no_indicator_without_images(self):
        places = [
            {"name": "A", "lat": 1.0, "lon": 2.0, "city": "Tokyo"},
        ]
        html = _render_place_list_html(places, [], {})
        assert "osm-has-images" not in html
        assert "osm-list-image-col-header" not in html

    def test_images_resolves_relative_path_with_siteurl(self):
        places = [
            {"name": "A", "lat": 1.0, "lon": 2.0, "images": ["images/photo.jpg"]},
        ]
        html = _render_place_list_html(places, [], {}, siteurl="https://example.com")
        assert "https://example.com/images/photo.jpg" in html

    def test_images_string_value_gets_icon(self):
        places = [
            {
                "name": "A",
                "lat": 1.0,
                "lon": 2.0,
                "images": "https://example.com/a.jpg",
            },
        ]
        html = _render_place_list_html(places, [], {})
        assert "osm-has-images" in html
        assert "osm-list-image-icon" in html


# ---------------------------------------------------------------------------
# Row anchors (id="osm-place-<slug>") and slug in GeoJSON properties
# ---------------------------------------------------------------------------


class TestPlaceRowAnchors:
    def test_row_carries_slugged_id(self):
        places = [{"name": "A1", "lat": 1.0, "lon": 2.0}]
        html = _render_place_list_html(places, [], {})
        assert 'id="osm-place-a1"' in html
        assert 'class="osm-place-row"' in html
        assert 'data-osm-place-slug="a1"' in html

    def test_id_field_wins_over_name(self):
        places = [{"id": "stable-key", "name": "Pretty Name", "lat": 1.0, "lon": 2.0}]
        html = _render_place_list_html(places, [], {})
        assert 'id="osm-place-stable-key"' in html
        assert "osm-place-pretty-name" not in html

    def test_collision_appends_suffix(self):
        # Two rows resolving to the same base slug must get distinct ids
        # so the HTML stays valid and anchors deep-link to the right row.
        places = [
            {"name": "Same", "lat": 1.0, "lon": 2.0},
            {"name": "Same", "lat": 3.0, "lon": 4.0},
        ]
        html = _render_place_list_html(places, [], {})
        assert 'id="osm-place-same"' in html
        assert 'id="osm-place-same-2"' in html

    def test_items_expansion_uses_item_field_as_suffix(self):
        # When items have a distinguishing field (here `hall`), the anchor
        # incorporates it so links read as cinema-h1 instead of cinema-2.
        places = [
            {
                "name": "Cinema",
                "lat": 1.0,
                "lon": 2.0,
                "items": [{"hall": "H1"}, {"hall": "H2"}, {"hall": "H3"}],
            }
        ]
        html = _render_place_list_html(places, [], {})
        assert 'id="osm-place-cinema-h1"' in html
        assert 'id="osm-place-cinema-h2"' in html
        assert 'id="osm-place-cinema-h3"' in html
        # Parent slug is mirrored so a popup that only knows "cinema" can
        # still find any of the expanded rows.
        assert html.count('data-osm-parent-slug="cinema"') == 3

    def test_items_expansion_falls_back_to_numeric_suffix(self):
        # When item dicts collapse to the same suffix (or have no
        # distinguishing values), dedup adds a numeric tail.
        places = [
            {
                "name": "Cinema",
                "lat": 1.0,
                "lon": 2.0,
                "items": [{"hall": "H1"}, {"hall": "H1"}],
            }
        ]
        html = _render_place_list_html(places, [], {})
        assert 'id="osm-place-cinema-h1"' in html
        assert 'id="osm-place-cinema-h1-2"' in html

    def test_non_item_rows_have_no_parent_slug_attr(self):
        # Plain (no items-expansion) rows must not carry the parent-slug
        # fallback attribute — it'd just bloat the HTML for nothing.
        places = [{"name": "A", "lat": 1.0, "lon": 2.0}]
        html = _render_place_list_html(places, [], {})
        assert "data-osm-parent-slug" not in html

    def test_row_class_combines_with_has_images(self):
        places = [
            {
                "name": "A",
                "lat": 1.0,
                "lon": 2.0,
                "images": ["https://example.com/a.jpg"],
            }
        ]
        html = _render_place_list_html(places, [], {})
        assert 'class="osm-place-row osm-has-images"' in html

    def test_geojson_includes_slug(self):
        feat = _place_to_feature(
            {"name": "豊島区立南池袋第二公園", "lat": 1.0, "lon": 2.0}
        )
        assert feat["properties"]["slug"] == "豊島区立南池袋第二公園"

    def test_geojson_slug_prefers_id(self):
        feat = _place_to_feature(
            {"id": "south-park", "name": "South Park", "lat": 1.0, "lon": 2.0}
        )
        assert feat["properties"]["slug"] == "south-park"

    def test_geojson_no_slug_when_name_blank(self):
        # name="" fails _validate_place upstream, but defend the helper anyway
        # so a malformed feature doesn't blow up the export.
        feat = _place_to_feature({"name": "", "lat": 1.0, "lon": 2.0})
        assert "slug" not in feat["properties"]


# ---------------------------------------------------------------------------
# HTML escaping in the place_list table (XSS hardening)
# ---------------------------------------------------------------------------


class TestPlaceListEscaping:
    def test_name_is_escaped_in_attr_and_text(self):
        places = [{"name": "<img src=x onerror=alert(1)>", "lat": 1.0, "lon": 2.0}]
        html_out = _render_place_list_html(places, [], {})
        assert "<img src=x onerror=alert(1)>" not in html_out
        assert "&lt;img src=x onerror=alert(1)&gt;" in html_out

    def test_tag_value_is_escaped(self):
        places = [
            {"name": "A", "lat": 1.0, "lon": 2.0, "tags": ["<script>alert(1)</script>"]}
        ]
        html_out = _render_place_list_html(places, [], {})
        assert "<script>alert(1)</script>" not in html_out
        assert "&lt;script&gt;" in html_out

    def test_field_value_is_escaped(self):
        places = [{"name": "A", "lat": 1.0, "lon": 2.0, "note": "<b>bold</b>"}]
        html_out = _render_place_list_html(places, ["note"], {})
        assert "<b>bold</b>" not in html_out
        assert "&lt;b&gt;bold&lt;/b&gt;" in html_out

    def test_javascript_url_dropped_in_urls(self):
        places = [
            {
                "name": "A",
                "lat": 1.0,
                "lon": 2.0,
                "urls": [{"label": "click", "href": "javascript:alert(1)"}],
            }
        ]
        html_out = _render_place_list_html(places, [], {})
        assert "javascript:" not in html_out
        assert 'href="#"' in html_out

    def test_group_header_title_is_escaped(self):
        places = [
            {"name": "A", "lat": 1.0, "lon": 2.0, "anime": "<svg onload=alert(1)>"}
        ]
        html_out = _render_place_list_html(
            places, ["anime"], {}, group_by=["anime"], group_summary_at=["anime"]
        )
        assert "<svg onload=alert(1)>" not in html_out
        assert "&lt;svg onload=alert(1)&gt;" in html_out


# ---------------------------------------------------------------------------
# _validate_place
# ---------------------------------------------------------------------------


class TestValidatePlace:
    def test_valid(self):
        assert _validate_place({"name": "A", "lat": 1.0, "lon": 2.0}, "test") is True

    def test_missing_name(self):
        assert _validate_place({"lat": 1.0, "lon": 2.0}, "test") is False

    def test_missing_lat(self):
        assert _validate_place({"name": "A", "lon": 2.0}, "test") is False

    def test_missing_lon(self):
        assert _validate_place({"name": "A", "lat": 1.0}, "test") is False


# ---------------------------------------------------------------------------
# PlaceResolver.resolve_to_paths
# ---------------------------------------------------------------------------


class TestResolveTopaths:
    def test_file_with_extension(self, resolver, places_root):
        paths = resolver.resolve_to_paths("japan/tamako.yml")
        assert paths == [places_root / "japan" / "tamako.yml"]

    def test_file_yaml_extension(self, resolver, places_root):
        paths = resolver.resolve_to_paths("japan/mygo.yaml")
        assert paths == [places_root / "japan" / "mygo.yaml"]

    def test_file_without_extension_tries_yml_then_yaml(self, resolver, places_root):
        # taiwan has .yml
        paths = resolver.resolve_to_paths("taiwan")
        assert paths == [places_root / "taiwan.yml"]

    def test_directory_returns_all_yaml_files(self, resolver, places_root):
        paths = resolver.resolve_to_paths("japan")
        assert set(paths) == {
            places_root / "japan" / "mygo.yaml",
            places_root / "japan" / "tamako.yml",
        }

    def test_directory_with_trailing_slash(self, resolver, places_root):
        paths = resolver.resolve_to_paths("japan/")
        assert len(paths) == 2

    def test_dot_returns_all_under_root(self, resolver, places_root):
        paths = resolver.resolve_to_paths(".")
        assert len(paths) == 4  # mygo.yaml, tamako.yml, taiwan.yml, bare.yml

    def test_empty_string_returns_all_under_root(self, resolver, places_root):
        assert resolver.resolve_to_paths("") == resolver.resolve_to_paths(".")

    def test_root_name_returns_all(self, resolver, places_root):
        # spec == root folder name should load entire root
        paths = resolver.resolve_to_paths(places_root.name)
        assert len(paths) == 4

    def test_fragment_stripped_before_path_resolution(self, resolver, places_root):
        paths = resolver.resolve_to_paths("japan/tamako.yml#出町桝形商店街")
        assert paths == [places_root / "japan" / "tamako.yml"]

    def test_nonexistent_returns_empty(self, resolver):
        assert resolver.resolve_to_paths("nowhere/nope.yml") == []


# ---------------------------------------------------------------------------
# PlaceResolver.resolve
# ---------------------------------------------------------------------------


class TestResolve:
    def test_file_loads_all_places(self, resolver):
        places = resolver.resolve("japan/tamako.yml")
        assert len(places) == 2

    def test_both_yml_and_yaml_extensions(self, resolver):
        assert len(resolver.resolve("japan/tamako.yml")) == 2
        assert len(resolver.resolve("japan/mygo.yaml")) == 1

    def test_fragment_by_name(self, resolver):
        places = resolver.resolve("japan/tamako.yml#出町桝形商店街")
        assert len(places) == 1
        assert places[0]["name"] == "出町桝形商店街"

    def test_fragment_by_id(self, resolver):
        places = resolver.resolve("taiwan.yml#taipei101")
        assert len(places) == 1
        assert places[0]["name"] == "台北101"

    def test_fragment_not_found_returns_empty(self, resolver):
        assert resolver.resolve("japan/tamako.yml#doesnotexist") == []

    def test_directory_loads_all_places(self, resolver):
        places = resolver.resolve("japan")
        assert len(places) == 3  # 1 from mygo + 2 from tamako

    def test_root_name_spec_loads_all(self, resolver, places_root):
        all_places = resolver.resolve(places_root.name)
        assert len(all_places) > 0
        assert len(all_places) == len(resolver.resolve("."))

    def test_nonexistent_returns_empty(self, resolver):
        assert resolver.resolve("nowhere.yml") == []


# ---------------------------------------------------------------------------
# _geojson_url
# ---------------------------------------------------------------------------


class TestGeojsonUrl:
    def test_yml_to_geojson_url(self, tmp_path):
        root = tmp_path / "places"
        path = root / "japan" / "tokyo.yml"
        url = _geojson_url(path, root, "/static")
        assert url == "/static/places/japan/tokyo.geojson"

    def test_yaml_extension(self, tmp_path):
        root = tmp_path / "places"
        path = root / "japan" / "mygo.yaml"
        url = _geojson_url(path, root, "/static")
        assert url == "/static/places/japan/mygo.geojson"

    def test_custom_static_prefix(self, tmp_path):
        root = tmp_path / "places"
        path = root / "taiwan.yml"
        url = _geojson_url(path, root, "/assets")
        assert url == "/assets/places/taiwan.geojson"

    def test_trailing_slash_stripped(self, tmp_path):
        root = tmp_path / "places"
        path = root / "taiwan.yml"
        url = _geojson_url(path, root, "/static/")
        assert url == "/static/places/taiwan.geojson"


# ---------------------------------------------------------------------------
# _render_place_html
# ---------------------------------------------------------------------------


class TestRenderPlaceHtml:
    def _entry(self, url, fragment=None):
        return {"url": url, "fragment": fragment}

    def test_returns_map_block(self):
        html = _render_place_html(
            [self._entry("/static/places/a.geojson")], ["A"], "400px", TILE, ATTR
        )
        assert 'class="osm-map-block"' in html
        assert 'class="osm-map"' in html
        assert 'class="osm-map-caption"' in html

    def test_data_geojson_attribute_contains_url(self):
        html = _render_place_html(
            [self._entry("/static/places/a.geojson")], ["A"], "400px", TILE, ATTR
        )
        assert "data-geojson=" in html
        assert "/static/places/a.geojson" in html

    def test_fragment_included_in_data_geojson(self):
        html = _render_place_html(
            [self._entry("/static/places/a.geojson", "上野公園")],
            ["上野公園"],
            "400px",
            TILE,
            ATTR,
        )
        assert "上野公園" in html

    def test_null_fragment_included(self):
        html = _render_place_html(
            [self._entry("/static/places/a.geojson", None)], ["A"], "400px", TILE, ATTR
        )
        assert "null" in html  # JSON null for no fragment

    def test_multiple_entries(self):
        entries = [self._entry("/a.geojson"), self._entry("/b.geojson")]
        html = _render_place_html(entries, ["A", "B"], "400px", TILE, ATTR)
        assert "a.geojson" in html
        assert "b.geojson" in html

    def test_empty_entries_returns_comment(self):
        html = _render_place_html([], [], "400px", TILE, ATTR)
        assert "pelican-osm: no valid places" in html

    def test_caption_few_names(self):
        html = _render_place_html(
            [self._entry("/u.geojson")], ["A", "B"], "400px", TILE, ATTR
        )
        assert "A, B" in html

    def test_caption_many_names(self):
        names = ["A", "B", "C", "D", "E"]
        html = _render_place_html(
            [self._entry("/u.geojson")], names, "400px", TILE, ATTR
        )
        assert "A, B, C" in html
        assert "and 2 more" in html

    def test_attribution_with_quotes_is_escaped(self):
        attr = '&copy; <a href="https://example.com">OSM</a>'
        html = _render_place_html(
            [self._entry("/u.geojson")], ["A"], "400px", TILE, attr
        )
        m = re.search(r'data-attribution="([^"]*)"', html)
        assert m is not None

    def test_map_height_css_var(self):
        html = _render_place_html(
            [self._entry("/u.geojson")], ["A"], "600px", TILE, ATTR
        )
        assert "--osm-map-height:600px" in html

    def test_unique_map_ids(self):
        html1 = _render_place_html(
            [self._entry("/u.geojson")], ["A"], "400px", TILE, ATTR
        )
        html2 = _render_place_html(
            [self._entry("/u.geojson")], ["A"], "400px", TILE, ATTR
        )
        id1 = re.search(r'id="(osm-map-\d+)"', html1).group(1)
        id2 = re.search(r'id="(osm-map-\d+)"', html2).group(1)
        assert id1 != id2

    def test_field_labels_attribute_emitted_when_set(self):
        html = _render_place_html(
            [self._entry("/u.geojson")],
            ["A"],
            "400px",
            TILE,
            ATTR,
            field_labels={"date": "日期", "category": "分類"},
        )
        m = re.search(r'data-osm-field-labels="([^"]*)"', html)
        assert m is not None
        payload = json.loads(m.group(1).replace("&quot;", '"').replace("&amp;", "&"))
        assert payload == {"date": "日期", "category": "分類"}

    def test_field_labels_attribute_omitted_when_empty(self):
        html = _render_place_html(
            [self._entry("/u.geojson")], ["A"], "400px", TILE, ATTR
        )
        assert "data-osm-field-labels" not in html


# ---------------------------------------------------------------------------
# _process_content
# ---------------------------------------------------------------------------


class TestProcessContent:
    def test_single_file_shortcode(self, resolver, places_root):
        content = "{% place japan/tamako.yml %}"
        result = _process_content(content, resolver, DEFAULT_SETTINGS)
        assert "osm-map-block" in result
        assert "tamako.geojson" in result

    def test_directory_shortcode(self, resolver):
        content = "{% place japan %}"
        result = _process_content(content, resolver, DEFAULT_SETTINGS)
        assert "osm-map-block" in result
        assert "mygo.geojson" in result
        assert "tamako.geojson" in result

    def test_comma_separated_specs_single_map(self, resolver):
        content = "{% place japan/tamako.yml, taiwan.yml %}"
        result = _process_content(content, resolver, DEFAULT_SETTINGS)
        # Only one map block
        assert result.count("osm-map-block") == 1
        assert "tamako.geojson" in result
        assert "taiwan.geojson" in result

    def test_multiple_shortcodes_separate_maps(self, resolver):
        content = "{% place japan/tamako.yml %}\n{% place taiwan.yml %}"
        result = _process_content(content, resolver, DEFAULT_SETTINGS)
        assert result.count("osm-map-block") == 2

    def test_custom_shortcode_name(self, resolver):
        settings = {**DEFAULT_SETTINGS, "OSM_SHORTCODE": "map"}
        result = _process_content("{% map japan/tamako.yml %}", resolver, settings)
        assert "osm-map-block" in result

    def test_default_shortcode_not_matched_with_custom(self, resolver):
        settings = {**DEFAULT_SETTINGS, "OSM_SHORTCODE": "map"}
        result = _process_content("{% place japan/tamako.yml %}", resolver, settings)
        assert "osm-map-block" not in result

    def test_no_shortcode_unchanged(self, resolver):
        content = "Just plain text."
        assert _process_content(content, resolver, DEFAULT_SETTINGS) == content

    def test_surrounding_content_preserved(self, resolver):
        content = "Before\n{% place japan/tamako.yml %}\nAfter"
        result = _process_content(content, resolver, DEFAULT_SETTINGS)
        assert result.startswith("Before\n")
        assert result.endswith("\nAfter")

    def test_names_in_caption(self, resolver):
        content = "{% place japan/tamako.yml %}"
        result = _process_content(content, resolver, DEFAULT_SETTINGS)
        assert "出町桝形商店街" in result
        assert "鴨川デルタ" in result

    def test_static_prefix_in_url(self, resolver):
        settings = {**DEFAULT_SETTINGS, "OSM_STATIC_PREFIX": "/assets"}
        result = _process_content("{% place japan/tamako.yml %}", resolver, settings)
        assert "/assets/places/japan/tamako.geojson" in result

    def test_deduplication_of_urls(self, resolver):
        # Same file listed twice should produce only one URL
        content = "{% place japan/tamako.yml, japan/tamako.yml %}"
        result = _process_content(content, resolver, DEFAULT_SETTINGS)
        assert result.count("tamako.geojson") == 1

    def test_fragment_passed_through_to_data_geojson(self, resolver):
        content = "{% place japan/tamako.yml#出町桝形商店街 %}"
        result = _process_content(content, resolver, DEFAULT_SETTINGS)
        m = re.search(r'data-geojson="([^"]*)"', result)
        assert m is not None
        decoded = m.group(1).replace("&quot;", '"').replace("&amp;", "&")
        entries = json.loads(decoded)
        assert entries[0]["fragment"] == "出町桝形商店街"

    def test_fragment_filters_caption_names(self, resolver):
        # Caption should only show the matched place, not all places in the file
        content = "{% place japan/tamako.yml#出町桝形商店街 %}"
        result = _process_content(content, resolver, DEFAULT_SETTINGS)
        assert "出町桝形商店街" in result
        assert "鴨川デルタ" not in result

    def test_no_fragment_shows_all_names(self, resolver):
        content = "{% place japan/tamako.yml %}"
        result = _process_content(content, resolver, DEFAULT_SETTINGS)
        assert "出町桝形商店街" in result
        assert "鴨川デルタ" in result

    def test_unknown_kwargs_do_not_break_parsing(self, resolver):
        # Forward-compat: kwargs that the current shortcode doesn't consume
        # should still parse without breaking the legacy positional spec.
        content = '{% place japan/tamako.yml future_kwarg="x,y" %}'
        result = _process_content(content, resolver, DEFAULT_SETTINGS)
        assert "tamako.geojson" in result

    # ── place_list grouping/aggregation kwargs ────────────────────────────

    def test_place_list_group_by_no_aggregate_preserves_rows(self, resolver):
        # Directory shortcode loads mygo (1 place) and tamako (2 places).
        # group_by without aggregate buckets but does NOT merge: all 3 rows
        # survive, just contiguous per anime.
        content = '{% place_list japan group_by="anime" %}'
        result = _process_content(content, resolver, DEFAULT_SETTINGS)
        assert "osm-place-list" in result
        tbody = re.search(r"<tbody>(.*?)</tbody>", result, re.DOTALL)
        assert tbody is not None
        assert tbody.group(1).count("<tr") == 3

    def test_place_list_group_summary_emits_header(self, resolver):
        content = '{% place_list japan group_by="anime" group_summary_at="anime" %}'
        result = _process_content(content, resolver, DEFAULT_SETTINGS)
        assert "osm-group-header" in result
        # Both anime titles surface as group headers
        assert "玉子市場" in result
        assert "MyGO" in result

    def test_place_list_group_count_template_setting(self, resolver):
        settings = {
            **DEFAULT_SETTINGS,
            "OSM_LIST_GROUP_COUNT_TEMPLATE": "{n} 個地點",
        }
        content = '{% place_list japan group_by="anime" group_summary_at="anime" %}'
        result = _process_content(content, resolver, settings)
        assert "個地點" in result
        assert ">1 places<" not in result

    def test_place_list_aggregate_year_renders_year_string(self, tmp_path: Path):
        # Build a fixture with two same-anime places in different years.
        root = tmp_path / "places"
        root.mkdir()
        (root / "show.yml").write_text(
            "anime: Show\nlocations:\n"
            "  - name: A\n    lat: 1.0\n    lon: 2.0\n    date: 2018-05-01\n"
            "  - name: B\n    lat: 1.0\n    lon: 2.0\n    date: 2023-06-01\n",
            encoding="utf-8",
        )
        resolver = PlaceResolver(root)
        content = '{% place_list show.yml group_by="anime" aggregate="date:year" %}'
        result = _process_content(content, resolver, DEFAULT_SETTINGS)
        assert "2018, 2023" in result


# ---------------------------------------------------------------------------
# _place_to_feature
# ---------------------------------------------------------------------------


class TestPlaceToFeature:
    def test_basic_structure(self):
        feature = _place_to_feature({"name": "A", "lat": 35.7, "lon": 139.7})
        assert feature["type"] == "Feature"
        assert feature["geometry"]["type"] == "Point"
        assert feature["geometry"]["coordinates"] == [139.7, 35.7]  # [lon, lat]
        assert feature["properties"]["name"] == "A"

    def test_lat_lon_not_in_properties(self):
        feature = _place_to_feature({"name": "A", "lat": 1.0, "lon": 2.0})
        assert "lat" not in feature["properties"]
        assert "lon" not in feature["properties"]

    def test_extra_fields_in_properties(self):
        feature = _place_to_feature(
            {"name": "A", "lat": 1.0, "lon": 2.0, "anime": "MyGO", "category": "公園"}
        )
        assert feature["properties"]["anime"] == "MyGO"
        assert feature["properties"]["category"] == "公園"

    def test_date_object_serialised_to_iso(self):
        import datetime

        feature = _place_to_feature(
            {"name": "A", "lat": 1.0, "lon": 2.0, "date": datetime.date(2026, 2, 22)}
        )
        assert feature["properties"]["date"] == "2026-02-22"

    def test_empty_string_stripped(self):
        feature = _place_to_feature({"name": "A", "lat": 1.0, "lon": 2.0, "notes": ""})
        assert "notes" not in feature["properties"]

    def test_empty_list_stripped(self):
        feature = _place_to_feature(
            {"name": "A", "lat": 1.0, "lon": 2.0, "tags": [], "photos": []}
        )
        assert "tags" not in feature["properties"]
        assert "photos" not in feature["properties"]

    def test_none_stripped(self):
        feature = _place_to_feature(
            {"name": "A", "lat": 1.0, "lon": 2.0, "extra": None}
        )
        assert "extra" not in feature["properties"]

    def test_nonempty_list_kept(self):
        feature = _place_to_feature(
            {"name": "A", "lat": 1.0, "lon": 2.0, "tags": ["已歇業"]}
        )
        assert feature["properties"]["tags"] == ["已歇業"]


# ---------------------------------------------------------------------------
# _yaml_to_geojson
# ---------------------------------------------------------------------------


class TestYamlToGeojson:
    def test_feature_collection_structure(self, places_root):
        fc = _yaml_to_geojson(places_root / "japan" / "tamako.yml")
        assert fc["type"] == "FeatureCollection"
        assert len(fc["features"]) == 2

    def test_file_metadata_in_properties(self, places_root):
        fc = _yaml_to_geojson(places_root / "japan" / "tamako.yml")
        for f in fc["features"]:
            assert f["properties"]["anime"] == "玉子市場"

    def test_invalid_places_excluded(self, tmp_path):
        yml = tmp_path / "test.yml"
        yml.write_text(
            "locations:\n"
            "  - name: Valid\n    lat: 1.0\n    lon: 2.0\n"
            "  - name: NoCoords\n",
            encoding="utf-8",
        )
        fc = _yaml_to_geojson(yml)
        assert len(fc["features"]) == 1
        assert fc["features"][0]["properties"]["name"] == "Valid"

    def test_list_of_dates_serialized_to_strings(self, tmp_path):
        yml = tmp_path / "test.yml"
        yml.write_text(
            "locations:\n"
            "  - name: A\n    lat: 1.0\n    lon: 2.0\n"
            "    date:\n      - 2026-02-22\n      - 2026-03-15\n",
            encoding="utf-8",
        )
        fc = _yaml_to_geojson(yml)
        import json

        # Must be JSON-serializable (no datetime.date objects)
        serialized = json.dumps(fc)
        assert "2026-02-22" in serialized
        assert "2026-03-15" in serialized


# ---------------------------------------------------------------------------
# _export_geojson
# ---------------------------------------------------------------------------


class TestExportGeojson:
    def test_geojson_files_created(self, places_root, tmp_path):
        output = tmp_path / "output"
        output.mkdir()

        import pelican.plugins.osm.osm as osm_mod

        orig, orig_s = osm_mod._resolver, osm_mod._settings
        osm_mod._resolver = PlaceResolver(places_root)
        osm_mod._settings = {}

        class FakePelican:
            settings = {"OUTPUT_PATH": str(output)}

        try:
            _export_geojson(FakePelican())
        finally:
            osm_mod._resolver, osm_mod._settings = orig, orig_s

        # Each YAML should have a corresponding .geojson
        assert (output / "static" / "places" / "japan" / "mygo.geojson").exists()
        assert (output / "static" / "places" / "japan" / "tamako.geojson").exists()
        assert (output / "static" / "places" / "taiwan.geojson").exists()
        assert (output / "static" / "places" / "bare.geojson").exists()

    def test_geojson_content_is_valid(self, places_root, tmp_path):
        output = tmp_path / "output"
        output.mkdir()

        import pelican.plugins.osm.osm as osm_mod

        orig, orig_s = osm_mod._resolver, osm_mod._settings
        osm_mod._resolver = PlaceResolver(places_root)
        osm_mod._settings = {}

        class FakePelican:
            settings = {"OUTPUT_PATH": str(output)}

        try:
            _export_geojson(FakePelican())
        finally:
            osm_mod._resolver, osm_mod._settings = orig, orig_s

        geojson_path = output / "static" / "places" / "japan" / "tamako.geojson"
        fc = json.loads(geojson_path.read_text(encoding="utf-8"))
        assert fc["type"] == "FeatureCollection"
        assert len(fc["features"]) == 2
        names = {f["properties"]["name"] for f in fc["features"]}
        assert names == {"出町桝形商店街", "鴨川デルタ"}

    def test_geojson_mirrors_yaml_directory_structure(self, places_root, tmp_path):
        output = tmp_path / "output"
        output.mkdir()

        import pelican.plugins.osm.osm as osm_mod

        orig, orig_s = osm_mod._resolver, osm_mod._settings
        osm_mod._resolver = PlaceResolver(places_root)
        osm_mod._settings = {}

        class FakePelican:
            settings = {"OUTPUT_PATH": str(output)}

        try:
            _export_geojson(FakePelican())
        finally:
            osm_mod._resolver, osm_mod._settings = orig, orig_s

        # japan/mygo.yaml → static/places/japan/mygo.geojson (not .yaml)
        assert (output / "static" / "places" / "japan" / "mygo.geojson").exists()
        assert not (output / "static" / "places" / "japan" / "mygo.yaml").exists()


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


jsonschema = pytest.importorskip("jsonschema")


PLACE_SCHEMA = {
    "type": "object",
    "required": ["anime", "locations"],
    "properties": {
        "anime": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "locations": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name", "lat", "lon"],
                "properties": {
                    "name": {"type": "string"},
                    "lat": {"type": "number", "minimum": -90, "maximum": 90},
                    "lon": {"type": "number", "minimum": -180, "maximum": 180},
                    "date": {"type": "string", "format": "date"},
                },
            },
        },
    },
}


class TestIsPlaceYaml:
    def test_regular_yaml(self, tmp_path):
        assert _is_place_yaml(tmp_path / "foo.yaml") is True
        assert _is_place_yaml(tmp_path / "foo.yml") is True

    def test_underscore_prefix_excluded(self, tmp_path):
        assert _is_place_yaml(tmp_path / "_schema.yaml") is False
        assert _is_place_yaml(tmp_path / "_private.yml") is False

    def test_non_yaml_excluded(self, tmp_path):
        assert _is_place_yaml(tmp_path / "foo.json") is False
        assert _is_place_yaml(tmp_path / "foo.txt") is False


class TestFindSchemaFor:
    def test_sibling_schema_found(self, tmp_path):
        root = tmp_path / "places"
        root.mkdir()
        (root / "_schema.yaml").write_text("type: object\n")
        yaml_file = root / "foo.yaml"
        yaml_file.write_text("name: A\n")

        result = _find_schema_for(yaml_file, root, ["_schema.yaml"])
        assert result == (root / "_schema.yaml").resolve()

    def test_ancestor_schema_found(self, tmp_path):
        root = tmp_path / "places"
        sub = root / "japan"
        sub.mkdir(parents=True)
        (root / "_schema.yaml").write_text("type: object\n")
        yaml_file = sub / "tokyo.yaml"
        yaml_file.write_text("name: A\n")

        result = _find_schema_for(yaml_file, root, ["_schema.yaml"])
        assert result == (root / "_schema.yaml").resolve()

    def test_nearest_ancestor_wins(self, tmp_path):
        root = tmp_path / "places"
        sub = root / "japan"
        sub.mkdir(parents=True)
        (root / "_schema.yaml").write_text("type: object\n")
        (sub / "_schema.yaml").write_text("type: object\n")
        yaml_file = sub / "tokyo.yaml"

        result = _find_schema_for(yaml_file, root, ["_schema.yaml"])
        assert result == (sub / "_schema.yaml").resolve()

    def test_no_schema_returns_none(self, tmp_path):
        root = tmp_path / "places"
        root.mkdir()
        yaml_file = root / "foo.yaml"
        yaml_file.write_text("name: A\n")

        assert _find_schema_for(yaml_file, root, ["_schema.yaml"]) is None

    def test_multiple_filenames_first_match_wins(self, tmp_path):
        root = tmp_path / "places"
        root.mkdir()
        (root / "_schema.json").write_text('{"type": "object"}\n')
        yaml_file = root / "foo.yaml"

        result = _find_schema_for(
            yaml_file, root, ["_schema.yaml", "_schema.yml", "_schema.json"]
        )
        assert result == (root / "_schema.json").resolve()


class TestValidateYamlFiles:
    @pytest.fixture()
    def schema_root(self, tmp_path):
        root = tmp_path / "places"
        sub = root / "pilgrimage"
        sub.mkdir(parents=True)
        (sub / "_schema.yaml").write_text(yaml.dump(PLACE_SCHEMA), encoding="utf-8")
        return root

    def test_no_schema_no_validation(self, places_root, caplog):
        # places_root fixture has no schema files
        with caplog.at_level("WARNING"):
            _validate_yaml_files(places_root, {})
        assert not any("schema validation" in r.message for r in caplog.records)

    def test_valid_file_no_errors(self, schema_root, caplog):
        sub = schema_root / "pilgrimage"
        (sub / "valid.yaml").write_text(
            yaml.dump(
                {
                    "anime": "Test",
                    "tags": ["動畫"],
                    "locations": [
                        {"name": "A", "lat": 1.0, "lon": 2.0, "date": "2026-01-01"},
                    ],
                }
            ),
            encoding="utf-8",
        )

        with caplog.at_level("WARNING"):
            _validate_yaml_files(schema_root, {})

        assert not any("schema validation" in r.message for r in caplog.records)

    def test_invalid_file_logs_warning(self, schema_root, caplog):
        sub = schema_root / "pilgrimage"
        (sub / "invalid.yaml").write_text(
            yaml.dump(
                {
                    "anime": "Test",
                    "locations": [
                        # missing required `name`
                        {"lat": 1.0, "lon": 2.0},
                    ],
                }
            ),
            encoding="utf-8",
        )

        with caplog.at_level("WARNING"):
            _validate_yaml_files(schema_root, {})

        assert any("schema validation" in r.message for r in caplog.records)

    def test_missing_required_top_level_key(self, schema_root, caplog):
        sub = schema_root / "pilgrimage"
        (sub / "noanime.yaml").write_text(
            yaml.dump({"locations": [{"name": "A", "lat": 1.0, "lon": 2.0}]}),
            encoding="utf-8",
        )

        with caplog.at_level("WARNING"):
            _validate_yaml_files(schema_root, {})

        assert any("anime" in r.message for r in caplog.records)

    def test_unquoted_date_does_not_break_validation(self, schema_root, caplog):
        # PyYAML parses `2026-01-01` as datetime.date — coercion should normalize it
        sub = schema_root / "pilgrimage"
        (sub / "datey.yaml").write_text(
            "anime: Test\n"
            "locations:\n"
            "  - name: A\n"
            "    lat: 1.0\n"
            "    lon: 2.0\n"
            "    date: 2026-01-01\n",
            encoding="utf-8",
        )

        with caplog.at_level("WARNING"):
            _validate_yaml_files(schema_root, {})

        assert not any("schema validation" in r.message for r in caplog.records)

    def test_strict_mode_raises(self, schema_root):
        sub = schema_root / "pilgrimage"
        (sub / "bad.yaml").write_text(
            yaml.dump({"anime": "T", "locations": [{"lat": 1.0, "lon": 2.0}]}),
            encoding="utf-8",
        )

        with pytest.raises(RuntimeError, match="schema validation error"):
            _validate_yaml_files(schema_root, {"OSM_VALIDATE_STRICT": True})

    def test_schema_file_not_loaded_as_place(self, schema_root):
        sub = schema_root / "pilgrimage"
        (sub / "valid.yaml").write_text(
            yaml.dump(
                {
                    "anime": "Test",
                    "locations": [{"name": "A", "lat": 1.0, "lon": 2.0}],
                }
            ),
            encoding="utf-8",
        )
        resolver = PlaceResolver(schema_root)
        paths = resolver.resolve_to_paths(".")
        assert all(not p.name.startswith("_") for p in paths)
        assert sub / "_schema.yaml" not in paths

    def test_custom_schema_filename(self, tmp_path, caplog):
        root = tmp_path / "places"
        root.mkdir()
        (root / "schema.yaml").write_text(yaml.dump(PLACE_SCHEMA), encoding="utf-8")
        (root / "bad.yaml").write_text(
            yaml.dump({"locations": [{"lat": 1.0, "lon": 2.0}]}),
            encoding="utf-8",
        )

        with caplog.at_level("WARNING"):
            _validate_yaml_files(root, {"OSM_VALIDATE_SCHEMA_FILENAMES": "schema.yaml"})

        assert any("schema validation" in r.message for r in caplog.records)

    def test_invalid_schema_logs_error(self, tmp_path, caplog):
        root = tmp_path / "places"
        root.mkdir()
        # `type` must be a string or list of strings — number is invalid
        (root / "_schema.yaml").write_text(yaml.dump({"type": 123}), encoding="utf-8")
        (root / "foo.yaml").write_text(
            yaml.dump({"name": "A", "lat": 1.0, "lon": 2.0}),
            encoding="utf-8",
        )

        with caplog.at_level("ERROR"):
            _validate_yaml_files(root, {})

        assert any("invalid schema" in r.message for r in caplog.records)


class TestResolveSchemaProperties:
    """Schema property resolution must reach into all three loader shapes,
    not just the canonical place dict with nested ``items``."""

    def _setup(self, tmp_path: Path, yaml_name: str, schema: dict) -> Path:
        root = tmp_path / "places"
        root.mkdir()
        (root / "_schema.yaml").write_text(yaml.dump(schema), encoding="utf-8")
        (root / yaml_name).write_text("name: A\nlat: 1\nlon: 2\n", encoding="utf-8")
        return root

    def test_locations_based_schema_extracts_per_place_props(self, tmp_path):
        # Pilgrimage shape: {properties: {locations: {items: {properties: ...}}}}
        schema = {
            "type": "object",
            "properties": {
                "anime": {"type": "string", "title": "作品"},
                "locations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "category": {
                                "type": "string",
                                "title": "分類",
                                "x-osm-list-hidden": True,
                            },
                            "date": {
                                "type": "string",
                                "x-osm-list-sort": "max",
                            },
                        },
                    },
                },
            },
        }
        root = self._setup(tmp_path, "foo.yaml", schema)
        resolver = PlaceResolver(root)
        merged = _resolve_schema_properties(["foo.yaml"], resolver, {})

        assert merged["category"]["title"] == "分類"
        assert merged["category"]["x-osm-list-hidden"] is True
        assert merged["date"]["x-osm-list-sort"] == "max"
        assert merged["anime"]["title"] == "作品"

    def test_dict_of_places_schema_extracts_per_place_props(self, tmp_path):
        # Theater shape: {additionalProperties: {properties: ..., items: {items: {...}}}}
        schema = {
            "type": "object",
            "additionalProperties": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "title": "影城"},
                    "address": {
                        "type": "string",
                        "x-osm-list-hidden": True,
                    },
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "hall": {"type": "string", "title": "影廳"},
                                "alt_rows": {
                                    "type": "string",
                                    "x-osm-list-hidden": True,
                                },
                            },
                        },
                    },
                },
            },
        }
        root = self._setup(tmp_path, "bar.yaml", schema)
        resolver = PlaceResolver(root)
        merged = _resolve_schema_properties(["bar.yaml"], resolver, {})

        assert merged["name"]["title"] == "影城"
        assert merged["address"]["x-osm-list-hidden"] is True
        assert merged["hall"]["title"] == "影廳"
        assert merged["alt_rows"]["x-osm-list-hidden"] is True

    def test_inner_props_win_over_outer_on_collision(self, tmp_path):
        # Item-level entries should override parent-level entries with the
        # same name, matching _expand_items row-merge precedence.
        schema = {
            "type": "object",
            "properties": {
                "tags": {"type": "array", "title": "FILE TAGS"},
                "locations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "tags": {"type": "array", "title": "PLACE TAGS"},
                        },
                    },
                },
            },
        }
        root = self._setup(tmp_path, "foo.yaml", schema)
        resolver = PlaceResolver(root)
        merged = _resolve_schema_properties(["foo.yaml"], resolver, {})

        assert merged["tags"]["title"] == "PLACE TAGS"


class TestResolveI18nTitle:
    def test_returns_full_lang_match(self):
        props = {"x-osm-list-i18n": {"title": {"en": "Hall", "ja": "スクリーン"}}}
        assert _resolve_i18n_title(props, "en") == "Hall"
        assert _resolve_i18n_title(props, "ja") == "スクリーン"

    def test_falls_back_to_primary_subtag(self):
        # Schema has only primary "zh"; lookup with "zh-tw" should match.
        props = {"x-osm-list-i18n": {"title": {"zh": "影廳"}}}
        assert _resolve_i18n_title(props, "zh-tw") == "影廳"

    def test_full_match_wins_over_primary_subtag(self):
        props = {"x-osm-list-i18n": {"title": {"zh": "影廳", "zh-tw": "影廳-TW"}}}
        assert _resolve_i18n_title(props, "zh-tw") == "影廳-TW"

    def test_case_insensitive_lookup(self):
        props = {"x-osm-list-i18n": {"title": {"EN": "Hall"}}}
        assert _resolve_i18n_title(props, "en") == "Hall"

    def test_no_match_returns_none(self):
        props = {"x-osm-list-i18n": {"title": {"en": "Hall"}}}
        assert _resolve_i18n_title(props, "fr") is None

    def test_no_lang_returns_none(self):
        props = {"x-osm-list-i18n": {"title": {"en": "Hall"}}}
        assert _resolve_i18n_title(props, None) is None

    def test_no_i18n_block_returns_none(self):
        assert _resolve_i18n_title({"title": "Hall"}, "en") is None


class TestBuildPopupFieldLabels:
    def test_uses_localized_title_when_present(self):
        schema = {
            "date": {
                "title": "Date",
                "x-osm-list-i18n": {"title": {"zh-tw": "日期"}},
            },
            "category": {"title": "Category"},
        }
        labels = _build_popup_field_labels(schema, "zh-tw")
        assert labels == {"date": "日期", "category": "Category"}

    def test_falls_back_to_plain_title_when_no_locale_match(self):
        schema = {"date": {"title": "Date"}}
        labels = _build_popup_field_labels(schema, "fr")
        assert labels == {"date": "Date"}

    def test_omits_fields_without_any_title(self):
        # No title and no i18n → JS derives the label, so we don't bake
        # anything into the data attribute.
        schema = {"date": {"x-osm-list-sort": "min"}}
        labels = _build_popup_field_labels(schema, "en")
        assert labels == {}

    def test_skips_non_dict_props(self):
        schema = {"date": "not-a-dict", "category": {"title": "Category"}}
        labels = _build_popup_field_labels(schema, "en")
        assert labels == {"category": "Category"}


class TestResolveGroupCountTemplate:
    def test_explicit_setting_wins(self):
        out = _resolve_group_count_template(
            {"OSM_LIST_GROUP_COUNT_TEMPLATE": "{n} spots"}, "zh-tw"
        )
        assert out == "{n} spots"

    def test_explicit_empty_string_wins_to_suppress(self):
        # Setting to "" is the documented way to suppress the count line —
        # built-in lang defaults must not override that intent.
        out = _resolve_group_count_template(
            {"OSM_LIST_GROUP_COUNT_TEMPLATE": ""}, "zh-tw"
        )
        assert out == ""

    def test_zh_tw_uses_zh_builtin(self):
        assert _resolve_group_count_template({}, "zh-tw") == "{n} 個地點"

    def test_zh_uses_zh_builtin(self):
        assert _resolve_group_count_template({}, "zh") == "{n} 個地點"

    def test_ja_uses_ja_builtin(self):
        assert _resolve_group_count_template({}, "ja") == "{n} 件"

    def test_unknown_lang_falls_back_to_english(self):
        assert _resolve_group_count_template({}, "fr") == "{n} places"

    def test_no_lang_falls_back_to_english(self):
        assert _resolve_group_count_template({}, None) == "{n} places"


class TestRenderPlaceListI18nColumnHeader:
    def test_lang_match_overrides_title(self):
        places = [{"name": "A", "lat": 1, "lon": 2, "category": "park"}]
        html = _render_place_list_html(
            places,
            ["category"],
            {},
            field_schema={
                "category": {
                    "title": "分類",
                    "x-osm-list-i18n": {"title": {"en": "Category"}},
                }
            },
            lang="en",
        )
        assert "<th>Category</th>" in html
        assert "<th>分類</th>" not in html

    def test_no_lang_match_falls_back_to_title(self):
        places = [{"name": "A", "lat": 1, "lon": 2, "category": "park"}]
        html = _render_place_list_html(
            places,
            ["category"],
            {},
            field_schema={
                "category": {
                    "title": "分類",
                    "x-osm-list-i18n": {"title": {"en": "Category"}},
                }
            },
            lang="ja",
        )
        assert "<th>分類</th>" in html

    def test_primary_subtag_match(self):
        places = [{"name": "A", "lat": 1, "lon": 2, "category": "park"}]
        html = _render_place_list_html(
            places,
            ["category"],
            {},
            field_schema={
                "category": {
                    "title": "Category",
                    "x-osm-list-i18n": {"title": {"zh": "分類"}},
                }
            },
            lang="zh-tw",
        )
        assert "<th>分類</th>" in html


# ---------------------------------------------------------------------------
# Markdown extension: protects {% ... %} from attr_list mangling
# ---------------------------------------------------------------------------


class TestShortcodePreserveExtension:
    """Verify that the Markdown extension keeps shortcode text intact through
    a full Markdown conversion — including with ``markdown.extensions.extra``
    enabled, which is what bites users in practice (it bundles ``attr_list``).
    """

    def _convert(self, source: str, *, with_extra: bool = True) -> str:
        import markdown

        extensions: list = [_ShortcodePreserveExtension(["place", "place_list"])]
        if with_extra:
            extensions.append("markdown.extensions.extra")
        md = markdown.Markdown(extensions=extensions, output_format="html")
        return md.convert(source)

    def test_shortcode_survives_attr_list(self):
        # Two adjacent shortcodes (no blank line) used to get the second
        # consumed by attr_list as paragraph attributes.
        source = "{% place_list theaters/japan.yaml %}\n{% place theaters/japan.yaml %}"
        out = self._convert(source)
        assert "{% place_list theaters/japan.yaml %}" in out
        assert "{% place theaters/japan.yaml %}" in out
        # No attr_list-style attributes leaked onto a paragraph
        assert 'place=""' not in out
        assert "theaters=" not in out

    def test_shortcode_after_heading_no_blank_line(self):
        # Heading immediately followed by a shortcode line previously got
        # the whole block wrapped in HTML comments. Verify the shortcode
        # text comes through verbatim.
        source = (
            "## 日本 / Japan\n"
            "{% place_list theaters/japan.yaml %}\n"
            "{% place theaters/japan.yaml %}"
        )
        out = self._convert(source)
        assert "{% place_list theaters/japan.yaml %}" in out
        assert "{% place theaters/japan.yaml %}" in out
        # No HTML-comment wrapping of plugin output
        assert "<!--" not in out

    def test_shortcode_with_kwargs_preserved(self):
        source = (
            "{% place_list theaters/japan.yaml "
            'group_by="country,city" group_summary_at="country" %}'
        )
        out = self._convert(source)
        assert (
            "{% place_list theaters/japan.yaml "
            'group_by="country,city" group_summary_at="country" %}'
        ) in out

    def test_works_without_extra(self):
        # Sanity: even without the troublesome extension, the shortcode
        # should still pass through unchanged (no double-substitution etc.).
        source = "{% place taiwan.yaml %}"
        out = self._convert(source, with_extra=False)
        assert "{% place taiwan.yaml %}" in out

    def test_custom_shortcode_names(self):
        import markdown

        ext = _ShortcodePreserveExtension(["map", "map_list"])
        md = markdown.Markdown(
            extensions=[ext, "markdown.extensions.extra"],
            output_format="html",
        )
        # Custom names protected
        out = md.convert("{% map foo.yaml %}\n{% map bar.yaml %}")
        assert "{% map foo.yaml %}" in out
        assert "{% map bar.yaml %}" in out

    def test_non_matching_braces_untouched(self):
        # Real attr_list usage on a non-shortcode element should still work,
        # i.e. our extension must not be too greedy.
        source = "A paragraph with attrs.\n{: .my-class }"
        out = self._convert(source)
        assert 'class="my-class"' in out

    def test_empty_shortcodes_rejected(self):
        with pytest.raises(ValueError):
            _ShortcodePreserveExtension([])


class TestRegisterMarkdownExtension:
    def test_appends_extension(self):
        settings: dict = {}
        _register_markdown_extension(settings)
        exts = settings["MARKDOWN"]["extensions"]
        assert any(isinstance(e, _ShortcodePreserveExtension) for e in exts)

    def test_idempotent(self):
        # i18n_subsites and similar plugins re-fire `initialized`. Repeat
        # calls must not stack copies of the extension.
        settings: dict = {}
        _register_markdown_extension(settings)
        _register_markdown_extension(settings)
        exts = settings["MARKDOWN"]["extensions"]
        assert sum(isinstance(e, _ShortcodePreserveExtension) for e in exts) == 1

    def test_respects_opt_out(self):
        settings: dict = {"OSM_DISABLE_MARKDOWN_PROTECTION": True}
        _register_markdown_extension(settings)
        # Either MARKDOWN is untouched, or extensions list has no preserver
        exts = settings.get("MARKDOWN", {}).get("extensions", [])
        assert not any(isinstance(e, _ShortcodePreserveExtension) for e in exts)

    def test_uses_custom_shortcode_names(self):
        # The extension's regex must match whatever the user configured.
        import markdown

        settings: dict = {
            "OSM_SHORTCODE": "map",
            "OSM_LIST_SHORTCODE": "map_list",
        }
        _register_markdown_extension(settings)
        ext = next(
            e
            for e in settings["MARKDOWN"]["extensions"]
            if isinstance(e, _ShortcodePreserveExtension)
        )
        md = markdown.Markdown(
            extensions=[ext, "markdown.extensions.extra"],
            output_format="html",
        )
        out = md.convert("{% map a.yaml %}\n{% map_list b.yaml %}")
        assert "{% map a.yaml %}" in out
        assert "{% map_list b.yaml %}" in out

    def test_preserves_existing_extensions(self):
        existing = ["markdown.extensions.extra"]
        settings = {"MARKDOWN": {"extensions": list(existing)}}
        _register_markdown_extension(settings)
        exts = settings["MARKDOWN"]["extensions"]
        assert "markdown.extensions.extra" in exts
        assert any(isinstance(e, _ShortcodePreserveExtension) for e in exts)


# ---------------------------------------------------------------------------
# _safe_url
# ---------------------------------------------------------------------------


class TestSafeUrl:
    # --- allowed schemes ---

    def test_https_url_allowed(self):
        assert _safe_url("https://example.com") == "https://example.com"

    def test_http_url_allowed(self):
        assert _safe_url("http://example.com") == "http://example.com"

    def test_mailto_allowed(self):
        assert _safe_url("mailto:user@example.com") == "mailto:user@example.com"

    def test_tel_allowed(self):
        assert _safe_url("tel:+81-3-1234-5678") == "tel:+81-3-1234-5678"

    def test_absolute_path_allowed(self):
        assert _safe_url("/static/images/photo.jpg") == "/static/images/photo.jpg"

    def test_relative_path_allowed(self):
        assert _safe_url("./images/photo.jpg") == "./images/photo.jpg"

    def test_fragment_allowed(self):
        assert _safe_url("#section-1") == "#section-1"

    def test_query_string_allowed(self):
        assert _safe_url("?q=search") == "?q=search"

    # --- blocked schemes ---

    def test_javascript_blocked(self):
        assert _safe_url("javascript:alert(1)") == "#"

    def test_data_uri_blocked(self):
        assert _safe_url("data:text/html,<h1>XSS</h1>") == "#"

    def test_vbscript_blocked(self):
        assert _safe_url("vbscript:msgbox(1)") == "#"

    def test_javascript_mixed_case_blocked(self):
        # verifies re.IGNORECASE is in effect
        assert _safe_url("JaVaScRiPt:alert(1)") == "#"

    def test_javascript_uppercase_blocked(self):
        assert _safe_url("JAVASCRIPT:alert(1)") == "#"

    # --- edge cases ---

    def test_none_returns_hash(self):
        assert _safe_url(None) == "#"

    def test_empty_string_returns_hash(self):
        assert _safe_url("") == "#"

    def test_whitespace_only_returns_hash(self):
        assert _safe_url("   ") == "#"

    def test_non_string_truthy_value(self):
        # str() conversion: integer 42 → "42", not a known scheme → "#"
        assert _safe_url(42) == "#"


# ---------------------------------------------------------------------------
# _normalize_url_field
# ---------------------------------------------------------------------------


class TestNormalizeUrlField:
    def test_plain_string_returns_single_entry_no_label(self):
        result = _normalize_url_field("https://example.com", None)
        assert result == [{"label": None, "href": "https://example.com"}]

    def test_single_dict_with_label_and_href(self):
        result = _normalize_url_field(
            {"label": "Blog", "href": "https://example.com"}, None
        )
        assert result == [{"label": "Blog", "href": "https://example.com"}]

    def test_single_dict_missing_label(self):
        result = _normalize_url_field({"href": "https://example.com"}, None)
        assert result == [{"label": None, "href": "https://example.com"}]

    def test_list_of_dicts(self):
        result = _normalize_url_field(
            [
                {"label": "2023", "href": "https://example.com/2023"},
                {"label": "2024", "href": "https://example.com/2024"},
            ],
            None,
        )
        assert len(result) == 2
        assert result[0] == {"label": "2023", "href": "https://example.com/2023"}
        assert result[1] == {"label": "2024", "href": "https://example.com/2024"}

    def test_article_url_map_none_preserves_href_as_is(self):
        raw = "{filename}posts/my-post.md"
        result = _normalize_url_field(raw, None)
        assert result == [{"label": None, "href": raw}]

    def test_filename_resolved_when_map_provided(self):
        url_map = {"posts/my-post.md": "https://example.com/posts/my-post/"}
        result = _normalize_url_field("{filename}posts/my-post.md", url_map)
        assert result == [{"label": None, "href": "https://example.com/posts/my-post/"}]

    def test_filename_in_dict_href_resolved(self):
        url_map = {"posts/visit.md": "https://example.com/posts/visit/"}
        result = _normalize_url_field(
            {"label": "Visit", "href": "{filename}posts/visit.md"}, url_map
        )
        assert result == [{"label": "Visit", "href": "https://example.com/posts/visit/"}]

    def test_none_input_returns_empty_list(self):
        assert _normalize_url_field(None, None) == []

    def test_list_with_invalid_item_skipped(self):
        # integers in the list are not string or dict → skipped
        result = _normalize_url_field([42, {"href": "https://example.com"}], None)
        assert result == [{"label": None, "href": "https://example.com"}]


# ---------------------------------------------------------------------------
# _resolve_image_url
# ---------------------------------------------------------------------------


class TestResolveImageUrl:
    def test_https_url_returned_as_is(self):
        assert (
            _resolve_image_url("https://example.com/img.jpg", "https://mysite.com")
            == "https://example.com/img.jpg"
        )

    def test_http_url_returned_as_is(self):
        assert (
            _resolve_image_url("http://example.com/img.jpg", "https://mysite.com")
            == "http://example.com/img.jpg"
        )

    def test_absolute_path_prepends_siteurl(self):
        assert (
            _resolve_image_url("/static/images/photo.jpg", "https://mysite.com")
            == "https://mysite.com/static/images/photo.jpg"
        )

    def test_relative_path_prepends_siteurl_with_slash(self):
        assert (
            _resolve_image_url("images/photo.jpg", "https://mysite.com")
            == "https://mysite.com/images/photo.jpg"
        )

    def test_siteurl_trailing_slash_normalized(self):
        # siteurl with trailing slash should not produce double slash
        assert (
            _resolve_image_url("/static/img.jpg", "https://mysite.com/")
            == "https://mysite.com/static/img.jpg"
        )

    def test_siteurl_trailing_slash_normalized_relative(self):
        assert (
            _resolve_image_url("images/photo.jpg", "https://mysite.com/")
            == "https://mysite.com/images/photo.jpg"
        )

    def test_subsite_siteurl(self):
        assert (
            _resolve_image_url("images/photo.jpg", "https://mysite.com/blog")
            == "https://mysite.com/blog/images/photo.jpg"
        )


# ---------------------------------------------------------------------------
# _walk_schema_properties
# ---------------------------------------------------------------------------


class TestWalkSchemaProperties:
    def test_locations_based_schema(self):
        """properties.locations.items.properties.* collected."""
        schema = {
            "properties": {
                "locations": {
                    "items": {
                        "properties": {
                            "name": {"title": "Name", "type": "string"},
                            "category": {"title": "Category", "type": "string"},
                        }
                    }
                }
            }
        }
        out: dict = {}
        _walk_schema_properties(schema, out)
        assert "name" in out
        assert "category" in out
        assert out["name"]["title"] == "Name"

    def test_dict_of_places_schema(self):
        """additionalProperties.properties.* collected."""
        schema = {
            "additionalProperties": {
                "properties": {
                    "name": {"title": "Name"},
                    "lat": {"title": "Latitude"},
                }
            }
        }
        out: dict = {}
        _walk_schema_properties(schema, out)
        assert "name" in out
        assert "lat" in out

    def test_bare_list_schema(self):
        """items.properties.* collected."""
        schema = {
            "items": {
                "properties": {
                    "name": {"title": "Place Name"},
                    "date": {"title": "Date"},
                }
            }
        }
        out: dict = {}
        _walk_schema_properties(schema, out)
        assert "name" in out
        assert "date" in out

    def test_item_level_overrides_parent_level(self):
        """Deeper item-level properties win over outer properties of same name."""
        schema = {
            "properties": {
                # outer-level "name" property — collected first
                "name": {"title": "Outer Name", "x-osm-list-hidden": False},
                "locations": {
                    "items": {
                        "properties": {
                            # inner item-level "name" — collected later, overrides
                            "name": {"title": "Inner Name", "x-osm-list-hidden": True},
                        }
                    }
                },
            }
        }
        out: dict = {}
        _walk_schema_properties(schema, out)
        # item-level (inner) must win
        assert out["name"]["title"] == "Inner Name"
        assert out["name"]["x-osm-list-hidden"] is True

    def test_non_dict_schema_is_noop(self):
        out: dict = {}
        _walk_schema_properties(None, out)
        _walk_schema_properties("not a dict", out)
        _walk_schema_properties([], out)
        assert out == {}

    def test_empty_schema_is_noop(self):
        out: dict = {}
        _walk_schema_properties({}, out)
        assert out == {}

    def test_nested_items_in_properties(self):
        """properties.items.items.properties.* (bare list with nested items)."""
        schema = {
            "properties": {
                "items": {
                    "items": {
                        "properties": {
                            "visited": {"title": "Visited", "type": "boolean"},
                        }
                    }
                }
            }
        }
        out: dict = {}
        _walk_schema_properties(schema, out)
        assert "visited" in out
        assert out["visited"]["title"] == "Visited"


# ---------------------------------------------------------------------------
# Signal handler integration tests
# ---------------------------------------------------------------------------
# These tests exercise the Pelican signal handlers that read/write module-level
# globals (_resolver, _settings, _article_url_map, _content_path).  The
# autouse fixture below saves and restores those globals around every test so
# parallel runs (pytest-xdist --dist=loadfile) cannot pollute each other.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _restore_osm_globals():
    """Save and restore osm module globals around each test."""
    saved_resolver = osm_module._resolver
    saved_settings = osm_module._settings
    saved_url_map = osm_module._article_url_map
    saved_content_path = osm_module._content_path
    yield
    osm_module._resolver = saved_resolver
    osm_module._settings = saved_settings
    osm_module._article_url_map = saved_url_map
    osm_module._content_path = saved_content_path


# ---------------------------------------------------------------------------
# register
# ---------------------------------------------------------------------------


class TestRegister:
    def test_connects_four_signals(self, monkeypatch):
        connected: dict[str, list] = {
            "initialized": [],
            "content_object_init": [],
            "finalized": [],
        }

        monkeypatch.setattr(
            signals.initialized,
            "connect",
            lambda fn: connected["initialized"].append(fn),
        )
        monkeypatch.setattr(
            signals.content_object_init,
            "connect",
            lambda fn: connected["content_object_init"].append(fn),
        )
        monkeypatch.setattr(
            signals.finalized,
            "connect",
            lambda fn: connected["finalized"].append(fn),
        )

        register()

        assert _init_resolver in connected["initialized"]
        assert _process_article in connected["content_object_init"]
        assert _copy_static in connected["finalized"]
        assert _export_geojson in connected["finalized"]


# ---------------------------------------------------------------------------
# _copy_static
# ---------------------------------------------------------------------------


class TestCopyStatic:
    def _make_pelican_obj(self, output_path: str) -> SimpleNamespace:
        return SimpleNamespace(settings={"OUTPUT_PATH": output_path})

    def test_copies_bundled_static_to_output(self, tmp_path):
        pelican_obj = self._make_pelican_obj(str(tmp_path))
        _copy_static(pelican_obj)

        dest = tmp_path / "static" / "pelican_osm"
        assert dest.exists()
        # bundled assets include css/ and js/ subdirectories
        assert (dest / "css").exists()
        assert (dest / "js").exists()

    def test_overwrites_existing_dest(self, tmp_path):
        dest = tmp_path / "static" / "pelican_osm"
        dest.mkdir(parents=True)
        (dest / "stale_file.txt").write_text("old")

        pelican_obj = self._make_pelican_obj(str(tmp_path))
        _copy_static(pelican_obj)

        # stale file should be gone (rmtree + copytree)
        assert not (dest / "stale_file.txt").exists()
        assert (dest / "css").exists()
