"""Serve fixtures built with the real OSM renderer and bundled assets."""

# ruff: noqa: RUF001 -- Traditional Chinese fixture text uses fullwidth punctuation.

from __future__ import annotations

import argparse
import json
import shutil
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from pelican.plugins.osm.osm import (
    CATALOG,
    _copy_static,
    _place_to_feature,
    _render_place_html,
    _render_place_list_html,
)

ROOT = Path(__file__).resolve().parents[1]


class FixtureHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass


PLACES = [
    {
        "id": "park",
        "name": "南池袋公園",
        "lat": 35.726,
        "lon": 139.715,
        "city": "東京",
        "category": "公園",
        "tags": ["散步", "動畫"],
        "notes": "適合野餐與休息",
        "icon": "🌳",
        "custom_field": "新欄位也能找到",
        "details": {"levels": ["巢狀文字", {"count": 73, "enabled": False}]},
        "urls": [{"label": "公園資訊", "href": "https://park.example/guide"}],
        "_private": "private-only-value",
        "translations": {"ja": {"custom_field": "unused-translation"}},
    },
    {
        "id": "cafe",
        "name": "Ｃａｆé 日光",
        "lat": 25.034,
        "lon": 121.565,
        "city": "臺北",
        "category": "咖啡廳",
        "tags": ["咖啡", "散步"],
        "notes": "午後喝一杯咖啡，看看窗外的街道。",
        "images": ["/sample.svg", "/sample2.svg"],
        "icon": "☕",
    },
    {
        "id": "cinema",
        "name": "信義影城",
        "lat": 25.036,
        "lon": 121.568,
        "city": "臺北",
        "category": "影城",
        "tags": ["電影"],
        "notes": "喜歡大銀幕的週末",
        "icon": "🎬",
    },
    {
        "id": "shrine",
        "name": "鴨川神社",
        "lat": 35.03,
        "lon": 135.77,
        "city": "京都",
        "category": "神社",
        "tags": ["散步"],
        "notes": "河畔的安靜角落",
        "icon": "⛩️",
    },
]


def build_fixtures(output: Path) -> None:
    _copy_static(SimpleNamespace(settings={"OUTPUT_PATH": str(output)}))
    for package in ["leaflet", "leaflet.markercluster"]:
        shutil.copytree(ROOT / "node_modules" / package / "dist", output / package)
    for name, color in [("sample", "#c4ded2"), ("sample2", "#d4dcec")]:
        (output / f"{name}.svg").write_text(
            f'<svg xmlns="http://www.w3.org/2000/svg" width="640" height="480">'
            f'<rect width="640" height="480" fill="{color}"/></svg>'
        )
    (output / "tile.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256">'
        '<rect width="256" height="256" fill="#e7ebe4"/>'
        '<path d="M0 48H256M0 144H256M64 0V256M192 0V256" '
        'stroke="#fff" stroke-width="8"/></svg>'
    )
    for name, places in [
        ("explorer", PLACES),
        (
            "many-tags",
            [
                dict(PLACES[0], id=f"tag-{i}", tags=[f"作品標籤 {i:02}"])
                for i in range(20)
            ],
        ),
        (
            "many-layers",
            [dict(PLACES[0], id=f"p{i}", city=f"City {i}") for i in range(12)],
        ),
    ]:
        (output / f"{name}.geojson").write_text(
            json.dumps(
                {
                    "type": "FeatureCollection",
                    "features": [_place_to_feature(p) for p in places],
                },
                ensure_ascii=False,
            )
        )
        blocks = []
        for locale in ["zh-Hant", "en", "ja"]:
            blocks.append(
                _render_place_html(
                    [{"url": f"/{name}.geojson"}],
                    [p["name"] for p in places],
                    "360px",
                    "/tile.svg?x={x}&y={y}&z={z}",
                    "Preview tiles · OpenStreetMap",
                    images_map={"cafe": ["/sample.svg", "/sample2.svg"]},
                    layer_field="city",
                    lang=locale,
                )
            )
        blocks.append(
            _render_place_list_html(
                places,
                ["city", "category", "notes"],
                {},
                group_by=["city"],
                group_summary_at=["city"],
                group_count_template=CATALOG.resolve("zh-Hant")["placeCount"],
                group_count_is_text=True,
                lang="zh-Hant",
                column_order=["category", "name", "notes", "tags", "images"],
            )
        )
        (output / f"{name}.html").write_text(
            '<!doctype html><html lang="zh-Hant" class="theme-light"><head>'
            '<meta charset="utf-8"><meta name="viewport" content="width=device-width">'
            '<link rel="stylesheet" href="/leaflet/leaflet.css">'
            '<link rel="stylesheet" href="/static/pelican_osm/css/osm-map.css">'
            "<style>body{margin:0;font:18px/1.7 system-ui;"
            "background:var(--color-background-main);"
            "color:var(--color-content-main)}main{max-width:960px;margin:auto;padding:24px}"
            ".theme-light{--color-background-main:#fff;--color-background-secondary:#f6f7f9;"
            "--color-content-main:#243044;--color-content-secondary:#647086;"
            "--color-background-contrast:#dce1e8;--brand:#3159ab}"
            ".theme-dark{--color-background-main:#18202d;--color-background-secondary:#202b3b;"
            "--color-content-main:#e4e9f2;--color-content-secondary:#a9b7ce;"
            "--color-background-contrast:#3b4960;--brand:#a9c5ff}</style>"
            '<script src="/leaflet/leaflet.js"></script>'
            '<script src="/static/pelican_osm/js/osm-map.js" defer></script>'
            "</head><body><main><h1>走走，看看這些地方</h1>"
            "<p>地圖與地點清單 · 搜尋、篩選，找到下一個想去的地方。</p>"
            + "\n".join(blocks)
            + "</main></body></html>"
        )

    theaters = [
        {
            "id": "xin-yi",
            "name": "信義影城",
            "country": "臺灣",
            "city": "臺北",
            "lat": 25.036,
            "lon": 121.568,
            "tags": ["電影"],
            "items": [
                {"hall": "2 廳", "format": "IMAX", "rows": "G–J", "notes": "大銀幕"},
                {"hall": "1 廳", "format": "一般 2D", "rows": "E–G", "notes": "安靜"},
            ],
        },
        {
            "id": "song-ren",
            "name": "松仁影城",
            "country": "臺灣",
            "city": "臺北",
            "lat": 25.032,
            "lon": 121.567,
            "tags": ["電影"],
            "items": [
                {
                    "hall": "7 廳",
                    "format": "Dolby Cinema",
                    "rows": "H–J",
                    "notes": "音效清楚",
                },
            ],
        },
        {
            "id": "tai-chung",
            "name": "臺中影城",
            "country": "臺灣",
            "city": "臺中",
            "lat": 24.16,
            "lon": 120.64,
            "tags": ["電影"],
            "items": [
                {"hall": "5 廳", "format": "一般 2D", "rows": "F–H", "notes": "寬敞"},
                {"hall": "3 廳", "format": "IMAX", "rows": "G–J", "notes": "明亮"},
            ],
        },
        {
            "id": "ikebukuro",
            "name": "池袋シネマ",
            "country": "日本",
            "city": "東京",
            "lat": 35.73,
            "lon": 139.71,
            "tags": ["動畫"],
            "items": [
                {"hall": "6 廳", "format": "Dolby", "rows": "H–K", "notes": "音效很好"},
            ],
        },
    ]
    theaters[0]["items"][0]["custom_info"] = {
        "nested": ["新影廳欄位", {"score": 97}],
    }
    grouped = _render_place_list_html(
        theaters,
        ["hall", "format", "rows", "notes"],
        {"hall": "影廳", "format": "規格", "rows": "推薦排數"},
        group_by=["country", "city", "name"],
        group_summary_at=["country", "city", "name"],
        group_count_template=CATALOG.resolve("zh-Hant")["placeCount"],
        group_count_is_text=True,
        lang="zh-Hant",
        column_order=["hall", "format", "rows", "notes", "tags"],
    )
    template = (output / "explorer.html").read_text()
    prefix = template.split("<main>", 1)[0]
    (output / "theaters.geojson").write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [_place_to_feature(p) for p in theaters],
            },
            ensure_ascii=False,
        )
    )
    theater_map = _render_place_html(
        [{"url": "/theaters.geojson"}],
        [p["name"] for p in theaters],
        "360px",
        "/tile.svg?x={x}&y={y}&z={z}",
        "Preview tiles · OpenStreetMap",
        layer_field="city",
        lang="zh-Hant",
    )
    (output / "groups.html").write_text(
        prefix
        + "<main><h1>影城與影廳</h1>"
        + "<p>依國家、城市與影城分組，找到適合自己的銀幕。</p>"
        + theater_map
        + grouped
        + "</main></body></html>"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    with TemporaryDirectory(prefix="osm-browser-") as directory:
        output = Path(directory)
        build_fixtures(output)
        handler = partial(FixtureHandler, directory=str(output))
        ThreadingHTTPServer(("127.0.0.1", args.port), handler).serve_forever()


if __name__ == "__main__":
    main()
