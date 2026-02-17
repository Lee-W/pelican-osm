# pelican-osm

**pelican-osm** is a Pelican plugin that allows you to embed [OpenStreetMap] (OSM) maps into your Markdown or reStructuredText articles using a simple {% place %} shortcode. The plugin integrates with [Leaflet.js](https://leafletjs.com/) and provides a lightweight, open-source alternative to Google Maps for your Pelican blogs.

## Features

- Embed interactive maps from [OpenStreetMap].
- Simple shortcode syntax: `{% place <key> %}`.
- Supports multiple places per article.
- Customizable place data using a YAML file (`places.yaml``).
- Works in Markdown and reStructuredText articles.
- Minimal external dependencies (PyYAML, Pelican).

## Installation

Using pip:

```sh
pip install pelican-osm
```

## Usage

### 1. Create a places.yaml file

Place this file in your Pelican `content`` folder (PATH) or specify a custom path via`pelicanconf.py`.

Example `places.yaml`:

```yaml
cafe:
  name: "My Cafe"
  lat: 25.0503164
  lon: 121.5253053

park:
  name: "Nice Park"
  lat: 24.5
  lon: 120.9
```

### 2. Add the shortcode to your articles

In Markdown or reStructuredText:

```markdown
I visited this cafe:

{% place cafe %}

And also went to the park:

{% place park %}

The plugin will replace the shortcode with an interactive map and a caption.
```

### 3. Add Leaflet.js and CSS to your templates

**pelican-osm** does not automatically inject scripts and styles.  
Add the following to your `base.html`` (or relevant template):

```html
<link rel="stylesheet" href="https://unpkg.com/leaflet/dist/leaflet.css">
<script src="https://unpkg.com/leaflet/dist/leaflet.js"></script>
<script src="/static/pelican_osm/js/map-init.js"></script>
<link rel="stylesheet" href="/static/pelican_osm/css/map.css">
```

Make sure `map-init.js` and `map.css` are available in your `output/static/pelican_osm`` folder.  
This happens automatically when you build your site with Pelican if the plugin is installed.

### 4. Optional configuration

You can customize the shortcode name or the YAML file path in `pelicanconf.py`:

```python
OSM_PLUGIN_SHORTCODE = "osm"  # default is "place"
OSM_PLUGIN_PLACES = "data/places.yaml"  # default is "places.yaml"
```

## Example Output

The shortcode:

```markdown
{% place cafe %}
```

Produces:

```html
<div class="map-block">
  <div class="map" style="height:320px;" data-lat="25.0503164" data-lon="121.5253053" data-name="My Cafe"></div>
  <div class="map-caption">My Cafe</div>
</div>
```

With Leaflet.js loaded, this renders an interactive map.

## Requirements

- Python >= 3.11
- Pelican >= 4.5
- PyYAML >= 6.0

## License

MIT License © Wei Lee

[OpenStreetMap]: https://www.openstreetmap.org/
