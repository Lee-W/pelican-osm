from pathlib import Path
from types import SimpleNamespace

from pelican.plugins.tabular.assets import copy_assets

from pelican import signals  # type: ignore[attr-defined]
from pelican.plugins.osm.osm import _copy_static, register


def test_osm_only_registers_shared_assets_once(tmp_path: Path) -> None:
    register()
    register()
    pelican = SimpleNamespace(settings={"OUTPUT_PATH": str(tmp_path)})
    receivers = list(signals.finalized.receivers_for(pelican))
    assert receivers.count(copy_assets) == 1
    copy_assets(pelican)
    assert (tmp_path / "static/pelican_tabular/js/tabular.js").is_file()
    assert (tmp_path / "static/pelican_tabular/css/tabular-legacy.css").is_file()


def test_old_asset_urls_include_core_without_database_controls(tmp_path: Path) -> None:
    pelican = SimpleNamespace(settings={"OUTPUT_PATH": str(tmp_path)})
    _copy_static(pelican)
    root = tmp_path / "static/pelican_osm"
    script = (root / "js/osm-map.js").read_text()
    stylesheet = (root / "css/osm-map.css").read_text()
    assert "host.Tabular = api" in script
    assert "api.initViews =" not in script
    assert ".osm-place-list th .tabular-sort-button" in stylesheet
    assert "--osm-table-bg: #fff" in stylesheet
    _copy_static(pelican)
    assert (root / "js/osm-map.js").read_text() == script
    assert (root / "css/osm-map.css").read_text() == stylesheet
