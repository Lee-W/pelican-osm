/**
 * pelican-osm map initializer
 * Reads data-geojson (JSON array of GeoJSON URLs) from each .osm-map element,
 * fetches the GeoJSON files, and renders a Leaflet map with popups.
 *
 * i18n: Override window.OSM_I18N before this script loads, e.g.:
 *   window.OSM_I18N = {
 *     manyPlaces:    (names, total) => `${names} 等 ${total} 個地點`,
 *     osmLink:       "OSM",
 *     googleLink:    "Google",
 *     fieldLabels:   { date: "日期", location: "地點" },
 *   };
 */
(function () {
  "use strict";

  // ── i18n defaults (English) ───────────────────────────────────
  const DEFAULT_I18N = {
    osmLink: "OSM",
    googleLink: "Google",
    fieldLabels: {},
  };

  const i18n = Object.assign({}, DEFAULT_I18N, window.OSM_I18N || {});
  i18n.fieldLabels = Object.assign(
    {},
    DEFAULT_I18N.fieldLabels,
    (window.OSM_I18N || {}).fieldLabels,
  );

  // ── Field label resolution ────────────────────────────────────
  const HIDDEN_FIELDS = new Set(["name", "lat", "lon", "id", "tags", "photos"]);

  function fieldLabel(key) {
    if (i18n.fieldLabels[key]) return i18n.fieldLabels[key];
    return key.charAt(0).toUpperCase() + key.slice(1).replace(/_/g, " ");
  }

  // ── Popup builder ─────────────────────────────────────────────
  function buildPopupHtml(props, lat, lon) {
    const tagBadges =
      Array.isArray(props.tags) && props.tags.length
        ? `<div class="osm-popup-tags">${props.tags
            .map((t) => `<span class="osm-badge osm-badge--tag">${t}</span>`)
            .join("")}</div>`
        : "";

    const fieldLines = Object.entries(props)
      .filter(([key]) => !HIDDEN_FIELDS.has(key))
      .map(
        ([key, value]) =>
          `<div class="osm-popup-field"><span class="osm-popup-label">${fieldLabel(key)}:</span> ${value}</div>`,
      )
      .join("");

    const osmUrl = `https://www.openstreetmap.org/?mlat=${lat}&mlon=${lon}&zoom=16`;
    const googleUrl = `https://www.google.com/maps?q=${lat},${lon}`;
    const links =
      `<div class="osm-popup-links">` +
      `🔗 <a href="${osmUrl}" target="_blank" rel="noopener">${i18n.osmLink}</a>` +
      ` | ` +
      `<a href="${googleUrl}" target="_blank" rel="noopener">${i18n.googleLink}</a>` +
      `</div>`;

    return (
      `<div class="osm-popup">` +
      `<strong class="osm-popup-name">${props.name}</strong>` +
      tagBadges +
      fieldLines +
      links +
      `</div>`
    );
  }

  // ── Add GeoJSON features to map ───────────────────────────────
  function addFeatures(map, features, markers) {
    for (const feature of features) {
      if (feature.geometry?.type !== "Point") continue;
      const [lon, lat] = feature.geometry.coordinates;
      const props = feature.properties || {};
      if (!props.name) continue;

      const marker = L.marker([lat, lon]).addTo(map);
      marker.bindPopup(buildPopupHtml(props, lat, lon), { maxWidth: 280 });
      markers.push(marker);
    }
  }

  // ── Map init ──────────────────────────────────────────────────
  async function initMap(el) {
    const rawUrls = el.getAttribute("data-geojson");
    const tileUrl = el.getAttribute("data-tile");
    const attribution = el.getAttribute("data-attribution");

    let urls;
    try {
      urls = JSON.parse(rawUrls);
    } catch (e) {
      console.error("pelican-osm: failed to parse data-geojson", e);
      return;
    }

    if (!urls || urls.length === 0) return;

    const map = L.map(el.id);
    L.tileLayer(tileUrl, { attribution, maxZoom: 18 }).addTo(map);

    const markers = [];

    // Fetch all GeoJSON files in parallel
    const results = await Promise.allSettled(
      urls.map((url) =>
        fetch(url).then((r) => {
          if (!r.ok) throw new Error(`${r.status} ${url}`);
          return r.json();
        }),
      ),
    );

    for (const result of results) {
      if (result.status === "fulfilled") {
        addFeatures(map, result.value.features || [], markers);
      } else {
        console.warn("pelican-osm: failed to fetch GeoJSON:", result.reason);
      }
    }

    if (markers.length === 0) return;

    if (markers.length === 1) {
      const latlng = markers[0].getLatLng();
      map.setView(latlng, 14);
    } else {
      const group = L.featureGroup(markers);
      map.fitBounds(group.getBounds().pad(0.15));
    }
  }

  function initAllMaps() {
    document.querySelectorAll(".osm-map").forEach(initMap);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initAllMaps);
  } else {
    initAllMaps();
  }
})();
