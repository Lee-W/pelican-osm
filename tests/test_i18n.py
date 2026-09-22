"""OSM and tabular share language rules, with stable place identity."""

from __future__ import annotations

import html
import json
import re
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from pelican.plugins.osm import osm

TRANSLATIONS = {"fields": ["name", "note"], "source_lang": "zh-TW"}


def make_places(root: Path, name: str = "來源名稱") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / "place.yaml"
    path.write_text(
        f"- name: {name}\n  lat: 25\n  lon: 121\n  note: 來源備註\n"
        "  images: [photo.jpg]\n  translations:\n    ja:\n      name: 日本語の名前\n",
        encoding="utf-8",
    )
    return path


def component_payload(rendered: str) -> dict:
    match = re.search(r'data-i18n="([^"]+)"', rendered)
    assert match is not None
    result = json.loads(html.unescape(match[1]))
    assert isinstance(result, dict)
    return result


def test_map_list_share_explicit_language_and_escaped_schema_labels(tmp_path):
    make_places(tmp_path)
    (tmp_path / "_schema.yaml").write_text(
        "properties:\n  name:\n    title: Source label\n"
        '    x-osm-list-i18n:\n      title:\n        ja: "名前 <&>"\n'
    )
    settings = {"DEFAULT_LANG": "zh-TW", "OSM_TRANSLATIONS": TRANSLATIONS}
    resolver = osm.PlaceResolver(tmp_path)
    rendered = osm._process_content(
        '{% place place.yaml lang="ja-JP" %}{% place_list place.yaml lang="ja-JP" %}',
        resolver,
        settings,
        lang="en",
    )
    assert rendered.count('lang="ja-JP"') == 2
    assert "/places/i18n/ja-JP/place.geojson" in rendered
    assert "日本語の名前" in rendered and "名前 &lt;&amp;&gt;" in rendered
    assert 'lang="zh-TW">來源備註' in rendered
    assert 'id="osm-place-來源名稱"' in rendered
    assert component_payload(rendered)["words"]["viewInTable"] == "表で表示"
    assert "translations" not in rendered


def test_script_fallback_and_schema_override():
    props = {"title": "Source", "x-osm-list-i18n": {"title": {"zh": "繁體", "ja": ""}}}
    assert osm._resolve_i18n_title(props, "zh-Hans") is None
    assert osm._resolve_i18n_title(props, "zh_TW") == "繁體"
    assert osm._build_popup_field_labels({"name": props}, "ja") == {"name": ""}


def test_projection_preserves_source_fragments_images_and_raw_data(tmp_path):
    source = make_places(tmp_path)
    before = deepcopy(osm._load_yaml_file(source))
    translated = osm._yaml_to_geojson(source, lang="ja", translations=TRANSLATIONS)
    props = translated["features"][0]["properties"]
    assert props["name"] == "日本語の名前"
    assert props["slug"] == "來源名稱"
    assert props["_osm_source_name"] == "來源名稱"
    assert props["_osm_languages"] == {"name": "ja", "note": "zh-TW"}
    assert "translations" not in props
    assert osm._load_yaml_file(source) == before
    assert translated["features"][0]["geometry"]["coordinates"] == [121, 25]
    rendered = osm._process_content(
        '{% place place.yaml#來源名稱 lang="ja" %}',
        osm.PlaceResolver(tmp_path),
        {"OSM_TRANSLATIONS": TRANSLATIONS},
    )
    assert "日本語の名前" in rendered
    images = json.loads(html.unescape(re.search(r'data-images="([^"]+)"', rendered)[1]))
    assert images == {"來源名稱": ["/photo.jpg"]}


def test_empty_translated_name_keeps_feature_identity(tmp_path):
    source = make_places(tmp_path)
    source.write_text(source.read_text().replace("name: 日本語の名前", 'name: ""'))
    feature = osm._yaml_to_geojson(source, lang="ja", translations=TRANSLATIONS)[
        "features"
    ][0]
    assert feature["properties"]["name"] == ""
    assert feature["properties"]["slug"] == "來源名稱"


def test_export_uses_own_root_and_locale_after_another_build(tmp_path):
    builds = []
    for locale, name in [("ja", "主站來源"), ("en", "其他站")]:
        root = tmp_path / locale
        make_places(root / "places", name)
        build = SimpleNamespace(
            settings={
                "PATH": str(root),
                "DEFAULT_LANG": locale,
                "OUTPUT_PATH": str(root / "output"),
                "OSM_TRANSLATIONS": TRANSLATIONS,
            }
        )
        osm._init_resolver(build)
        osm._process_content(
            "{% place place.yaml %}",
            build.settings["_OSM_CONTEXT"]["resolver"],
            build.settings,
        )
        builds.append(build)
    for build in builds:
        osm._export_geojson(build)
    for locale, name in [("ja", "主站來源"), ("en", "其他站")]:
        path = (
            tmp_path / locale / "output/static/places/i18n" / locale / "place.geojson"
        )
        props = json.loads(path.read_text())["features"][0]["properties"]
        assert props["_osm_source_name"] == name
        assert props["name"] == ("日本語の名前" if locale == "ja" else name)


@pytest.mark.parametrize(
    "locale, expected",
    [("en", "1 place"), ("ja", "1 件"), ("zh-TW", "1 個地點"), ("zh-Hans", "1 place")],
)
def test_list_count_is_localized_before_javascript(locale, expected):
    rendered = osm._render_place_list_html([{"name": "X"}], [], {}, lang=locale)
    assert f'class="osm-place-list-count">{expected}</div>' in rendered


def test_caption_and_message_overrides_are_escaped():
    rendered = osm._render_place_html(
        [{"url": "/x"}],
        ["<&>", "B", "C", "D"],
        "400px",
        "/tiles",
        "",
        lang="ja",
        messages={"more": " + {n} <&>"},
    )
    assert "&lt;&amp;&gt;, B, C + 1 &lt;&amp;&gt;" in rendered
    assert component_payload(rendered)["words"]["more"] == " + {n} <&>"


def test_invalid_message_and_structural_translation_are_rejected(tmp_path):
    make_places(tmp_path)
    with pytest.raises(ValueError, match=r"OSM_MESSAGES\.more"):
        osm._process_content(
            "{% place place.yaml %}",
            osm.PlaceResolver(tmp_path),
            {"OSM_MESSAGES": {"more": "missing count"}},
        )
    with pytest.raises(ValueError, match="group fields must stay canonical"):
        osm._render_place_list_html(
            [{"name": "N"}], [], {}, group_by=["name"], translations=TRANSLATIONS
        )


def test_subsite_geojson_url_includes_site_prefix(tmp_path):
    make_places(tmp_path / "places")
    settings = {
        "SITEURL": "https://example.test/ja",
        "PATH": str(tmp_path),
        "OUTPUT_PATH": str(tmp_path / "output/ja"),
        "OSM_TRANSLATIONS": TRANSLATIONS,
    }
    build = SimpleNamespace(settings=settings)
    osm._init_resolver(build)
    rendered = osm._process_content(
        '{% place place.yaml lang="ja" %}',
        settings["_OSM_CONTEXT"]["resolver"],
        settings,
    )
    assert "https://example.test/ja/static/places/i18n/ja/place.geojson" in rendered
    osm._export_geojson(build)
    assert (tmp_path / "output/ja/static/places/i18n/ja/place.geojson").exists()
