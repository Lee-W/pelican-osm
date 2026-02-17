from pathlib import Path
from types import SimpleNamespace

import pytest
from pelican.plugins.osm.osm import OSMPlugin


@pytest.fixture
def plugin():
    p = OSMPlugin()
    p.shortcode_name = "place"
    p.places = {
        "cafe": {"lat": 25.0, "lon": 121.0, "name": "My Cafe"},
        "park": {"lat": 24.5, "lon": 120.9, "name": "Nice Park"},
    }
    return p


@pytest.fixture
def article():
    """Fake Pelican content object"""
    return SimpleNamespace(_content="")


@pytest.fixture
def tmp_generator(tmp_path):
    """Fake Pelican generator"""
    return SimpleNamespace(output_path=tmp_path)


class TestOSMPlugin:
    def test_replace_shortcode_basic(self, plugin, article):
        article._content = "Hello {% place cafe %}"

        plugin.replace_short_codes(article)

        assert "map-block" in article._content
        assert "My Cafe" in article._content
        assert 'data-lat="25.0"' in article._content
        assert 'data-lon="121.0"' in article._content

    def test_multiple_shortcodes(self, plugin, article):
        article._content = "{% place cafe %} --- {% place park %}"

        plugin.replace_short_codes(article)

        assert article._content.count("map-block") == 2
        assert "My Cafe" in article._content
        assert "Nice Park" in article._content

    def test_unknown_place_raises(self, plugin, article):
        article._content = "{% place nowhere %}"

        with pytest.raises(KeyError):
            plugin.replace_short_codes(article)

    def test_no_content_attribute(self, plugin):
        obj = SimpleNamespace()

        # should not crash
        plugin.replace_short_codes(obj)

    def test_empty_content(self, plugin, article):
        article._content = ""

        plugin.replace_short_codes(article)

        assert article._content == ""

    def test_custom_shortcode_name(self, plugin, article):
        plugin.shortcode_name = "osm"
        article._content = "{% osm cafe %}"

        plugin.replace_short_codes(article)

        assert "My Cafe" in article._content

    def test_copy_static(self, plugin, tmp_generator, tmp_path):
        # 建立假的 plugin static
        fake_static = tmp_path / "static"
        fake_static.mkdir()
        (fake_static / "map.js").write_text("console.log('ok')")
        (fake_static / "map.css").write_text("body{}")

        # 執行
        plugin.copy_static(tmp_generator)

        # 驗證
        dst = Path(tmp_generator.output_path) / "static/pelican_osm"
        assert (dst / "js/map-init.js").exists()
        assert (dst / "css/map.css").exists()
