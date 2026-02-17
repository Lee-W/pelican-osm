import re
import shutil
from pathlib import Path
from typing import Any

import yaml
from pelican import Pelican, generators, signals


class OSMPlugin:
    """Replace {% shortcode key %} with OSM map HTML"""

    def __init__(self) -> None:
        self.places: dict[str, dict] = {}
        self.assets_injected: bool = False

    def load_config(self, pelican: Pelican) -> None:
        # load shortcode marker
        self.shortcode_name: str = getattr(
            pelican.settings, "OSM_PLUGIN_SHORTCODE", "place"
        )

        # load places file
        self.places_file = str(
            getattr(pelican.settings, "OSM_PLUGIN_PLACES", "places.yaml")
        )
        path = Path(pelican.settings["PATH"]) / "places.yaml"
        if path.is_file():
            self.places = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    def replace_short_codes(self, instance: Any) -> None:
        if not getattr(instance, "_content", None):
            return

        def render_map_html(p: dict) -> str:
            return (
                f'\n<div class="map-block">\n'
                f'  <div class="map" style="height:320px;" data-lat="{p["lat"]}" data-lon="{p["lon"]}" data-name="{p["name"]}"></div>\n'
                f'  <div class="map-caption">{p["name"]}</div>\n'
                f"</div>"
            )

        pattern = rf"\{{\%\s*{re.escape(self.shortcode_name)}\s*(.*?)\s*\%\}}"

        def repl(match: re.Match) -> str:
            key = match.group(1).strip()
            if key not in self.places:
                raise KeyError(f"Place not found in OSMPlugin: {key}")
            return render_map_html(self.places[key])

        instance._content = re.sub(pattern, repl, instance._content)

    def copy_static(self, generator: generators.Generator) -> None:
        src = Path(__file__).parent / "static"
        dst = Path(generator.output_path) / "static/pelican_osm"
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)


osm_plugin = OSMPlugin()


def register() -> None:
    signals.initialized.connect(osm_plugin.load_config)
    signals.content_object_init.connect(osm_plugin.replace_short_codes)
    signals.finalized.connect(osm_plugin.copy_static)
