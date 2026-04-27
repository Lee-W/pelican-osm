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

import pytest
import yaml

from pelican.plugins.osm.osm import (
    PlaceResolver,
    _aggregate_field,
    _collapse_places,
    _export_geojson,
    _extract_year,
    _find_schema_for,
    _geojson_url,
    _is_place_yaml,
    _load_yaml_file,
    _merge_place,
    _parse_aggregate_kwarg,
    _parse_csv_kwarg,
    _parse_shortcode_args,
    _place_to_feature,
    _process_content,
    _render_place_html,
    _render_place_list_html,
    _validate_place,
    _validate_yaml_files,
    _yaml_to_geojson,
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

    def test_unknown_op_returns_empty(self):
        # Unknown ops are logged and produce empty string rather than raising
        assert _aggregate_field("nonexistent", "date", [{"date": "2024"}]) == ""


# ---------------------------------------------------------------------------
# _collapse_places
# ---------------------------------------------------------------------------


class TestCollapsePlaces:
    def test_single_field_collapses_matching_places(self):
        places = [
            {"name": "A", "anime": "X"},
            {"name": "B", "anime": "X"},
            {"name": "C", "anime": "Y"},
        ]
        rows = _collapse_places(places, ["anime"], {})
        assert len(rows) == 2
        assert rows[0]["anime"] == "X"
        assert len(rows[0]["_places"]) == 2
        assert rows[1]["anime"] == "Y"
        assert len(rows[1]["_places"]) == 1

    def test_multi_field_collapses_by_tuple(self):
        places = [
            {"name": "A", "anime": "X", "city": "K"},
            {"name": "B", "anime": "X", "city": "T"},
            {"name": "C", "anime": "X", "city": "K"},
        ]
        rows = _collapse_places(places, ["anime", "city"], {})
        assert len(rows) == 2
        k_row = next(r for r in rows if r["city"] == "K")
        assert len(k_row["_places"]) == 2

    def test_year_aggregate_collapses_dates(self):
        places = [
            {"name": "A", "anime": "X", "date": "2018-05-01"},
            {"name": "B", "anime": "X", "date": "2023-06-01"},
            {"name": "C", "anime": "X", "date": "2018-09-01"},
        ]
        rows = _collapse_places(places, ["anime"], {"date": "year"})
        assert rows[0]["date"] == "2018, 2023"

    def test_year_aggregate_with_all_missing_dates_yields_empty(self):
        places = [
            {"name": "A", "anime": "X", "date": ""},
            {"name": "B", "anime": "X"},
        ]
        rows = _collapse_places(places, ["anime"], {"date": "year"})
        assert rows[0]["date"] == ""

    def test_tags_unioned_across_group(self):
        places = [
            {"name": "A", "anime": "X", "tags": ["動畫"]},
            {"name": "B", "anime": "X", "tags": ["已歇業"]},
        ]
        rows = _collapse_places(places, ["anime"], {})
        assert rows[0]["tags"] == ["動畫", "已歇業"]

    def test_first_non_empty_wins_for_other_fields(self):
        places = [
            {"name": "A", "anime": "X", "category": ""},
            {"name": "B", "anime": "X", "category": "商店街"},
        ]
        rows = _collapse_places(places, ["anime"], {})
        assert rows[0]["category"] == "商店街"

    def test_order_preserves_first_appearance(self):
        places = [
            {"name": "A", "anime": "Z"},
            {"name": "B", "anime": "X"},
            {"name": "C", "anime": "Z"},
        ]
        rows = _collapse_places(places, ["anime"], {})
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

    def test_place_list_group_by_collapses_in_html(self, resolver):
        # Directory shortcode loads both mygo and tamako; group_by="anime"
        # collapses each file's places to one row.
        content = '{% place_list japan group_by="anime" %}'
        result = _process_content(content, resolver, DEFAULT_SETTINGS)
        assert "osm-place-list" in result
        # tbody contains exactly two data rows (no group_summary_at)
        tbody = re.search(r"<tbody>(.*?)</tbody>", result, re.DOTALL)
        assert tbody is not None
        assert tbody.group(1).count("<tr>") == 2

    def test_place_list_group_summary_emits_header(self, resolver):
        content = '{% place_list japan group_by="anime" group_summary_at="anime" %}'
        result = _process_content(content, resolver, DEFAULT_SETTINGS)
        assert "osm-group-header" in result
        # Both anime titles surface as group headers
        assert "玉子市場" in result
        assert "MyGO" in result

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
