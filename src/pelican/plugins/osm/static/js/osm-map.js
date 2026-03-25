/**
 * pelican-osm map initializer
 * Reads data-geojson (JSON array of GeoJSON URLs) from each .osm-map element,
 * fetches the GeoJSON files, and renders a Leaflet map with popups.
 *
 * Enhanced features:
 * - Fullscreen button in top-right corner
 * - Image overlay support via data-images attribute
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
  function buildPopupHtml(props, lat, lon, images) {
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

    const photoGallery =
      images && images.length > 0
        ? `<div class="osm-popup-gallery">` +
          images
            .map(
              (img, idx) =>
                `<img src="${img}" alt="Place photo" class="osm-popup-photo" data-fullsrc="${img}" data-photo-idx="${idx}">`,
            )
            .join("") +
          `</div>`
        : "";

    return (
      `<div class="osm-popup">` +
      `<strong class="osm-popup-name">${props.name}</strong>` +
      tagBadges +
      fieldLines +
      links +
      photoGallery +
      `</div>`
    );
  }

  // ── Add GeoJSON features to map ───────────────────────────────
  function addFeatures(map, features, fragment, markers, imagesMap) {
    for (const feature of features) {
      if (feature.geometry?.type !== "Point") continue;
      const props = feature.properties || {};
      if (!props.name) continue;

      // Fragment filter: match by id or name
      if (fragment && props.id !== fragment && props.name !== fragment)
        continue;

      const [lon, lat] = feature.geometry.coordinates;
      const marker = L.marker([lat, lon]).addTo(map);
      const placeKey = props.id || props.name;
      const images = imagesMap[placeKey] || [];
      marker.bindPopup(buildPopupHtml(props, lat, lon, images), {
        maxWidth: 280,
      });
      markers.push(marker);
    }
  }

  // ── Fullscreen handler ────────────────────────────────────────
  function setupFullscreenButton(mapContainer, mapElement) {
    const fsBtn = document.createElement("button");
    fsBtn.className = "osm-fullscreen-btn";
    fsBtn.setAttribute("title", "Toggle fullscreen");
    fsBtn.innerHTML = "⛶";

    fsBtn.addEventListener("click", async (e) => {
      e.preventDefault();
      e.stopPropagation();

      const mapBlock = mapContainer.closest(".osm-map-block");
      if (!mapBlock) return;

      try {
        if (!document.fullscreenElement) {
          await mapBlock.requestFullscreen();
          fsBtn.classList.add("osm-fullscreen-btn--active");
        } else {
          await document.exitFullscreen();
          fsBtn.classList.remove("osm-fullscreen-btn--active");
        }
      } catch (err) {
        console.warn("pelican-osm: fullscreen request failed", err);
      }
    });

    mapContainer.parentElement.insertBefore(fsBtn, mapContainer.nextSibling);

    document.addEventListener("fullscreenchange", () => {
      setTimeout(() => {
        // Give Leaflet time to detect the parent element resize
        if (window.L && window.L.Map) {
          // Invalidate size on all maps (in case multiple maps exist)
          document.querySelectorAll(".osm-map").forEach((el) => {
            if (el._leaflet_map) {
              el._leaflet_map.invalidateSize();
            }
          });
        }
      }, 100);
    });
  }

  // ── Map init ──────────────────────────────────────────────────
  async function initMap(el) {
    const rawEntries = el.getAttribute("data-geojson");
    const tileUrl = el.getAttribute("data-tile");
    const attribution = el.getAttribute("data-attribution");
    const rawImages = el.getAttribute("data-images");

    let entries;
    let imagesData = {};
    try {
      entries = JSON.parse(rawEntries);
      if (rawImages) {
        imagesData = JSON.parse(rawImages);
      }
    } catch (e) {
      console.error("pelican-osm: failed to parse attributes", e);
      return;
    }

    if (!entries || entries.length === 0) return;

    const map = L.map(el.id);
    el._leaflet_map = map;
    L.tileLayer(tileUrl, { attribution, maxZoom: 18 }).addTo(map);

    const markers = [];

    const results = await Promise.allSettled(
      entries.map((entry) =>
        fetch(entry.url).then((r) => {
          if (!r.ok) throw new Error(`${r.status} ${entry.url}`);
          return r.json().then((fc) => ({ fc, fragment: entry.fragment }));
        }),
      ),
    );

    for (const result of results) {
      if (result.status === "fulfilled") {
        const { fc, fragment } = result.value;
        addFeatures(map, fc.features || [], fragment, markers, imagesData);
      } else {
        console.warn("pelican-osm: failed to fetch GeoJSON:", result.reason);
      }
    }

    if (markers.length === 0) return;

    // Setup fullscreen button
    setupFullscreenButton(el, el);

    // Set map view
    if (markers.length === 1) {
      const latlng = markers[0].getLatLng();
      map.setView(latlng, 14);
    } else {
      const group = L.featureGroup(markers);
      map.fitBounds(group.getBounds().pad(0.15));
    }
  }

  function setupPhotoLightbox() {
    let currentIdx = 0;
    let currentImages = [];

    const lightboxHtml = `
      <div id="osm-photo-lightbox" class="osm-lightbox">
        <div class="osm-lightbox-overlay"></div>
        <div class="osm-lightbox-container">
          <button class="osm-lightbox-close" title="Close (Esc)">&times;</button>
          <button class="osm-lightbox-prev" title="Previous (←)">&lsaquo;</button>
          <img class="osm-lightbox-image" src="" alt="">
          <button class="osm-lightbox-next" title="Next (→)">&rsaquo;</button>
          <div class="osm-lightbox-info"></div>
        </div>
      </div>
    `;

    document.body.insertAdjacentHTML("beforeend", lightboxHtml);

    const lightbox = document.getElementById("osm-photo-lightbox");
    const lightboxImg = lightbox.querySelector(".osm-lightbox-image");
    const lightboxInfo = lightbox.querySelector(".osm-lightbox-info");
    const closeBtn = lightbox.querySelector(".osm-lightbox-close");
    const prevBtn = lightbox.querySelector(".osm-lightbox-prev");
    const nextBtn = lightbox.querySelector(".osm-lightbox-next");
    const overlay = lightbox.querySelector(".osm-lightbox-overlay");

    function showLightbox(idx, images) {
      currentIdx = idx;
      currentImages = images;
      lightboxImg.src = images[idx];
      lightboxInfo.textContent = `${idx + 1} / ${images.length}`;
      lightbox.classList.add("osm-lightbox--active");
      document.body.style.overflow = "hidden";
    }

    function hideLightbox() {
      lightbox.classList.remove("osm-lightbox--active");
      document.body.style.overflow = "";
    }

    function goToImage(idx) {
      if (idx < 0 || idx >= currentImages.length) return;
      currentIdx = idx;
      lightboxImg.src = currentImages[idx];
      lightboxInfo.textContent = `${idx + 1} / ${currentImages.length}`;
    }

    closeBtn.addEventListener("click", hideLightbox);
    overlay.addEventListener("click", hideLightbox);

    prevBtn.addEventListener("click", () => {
      goToImage(currentIdx - 1);
    });

    nextBtn.addEventListener("click", () => {
      goToImage(currentIdx + 1);
    });

    document.addEventListener("keydown", (e) => {
      if (!lightbox.classList.contains("osm-lightbox--active")) return;
      if (e.key === "Escape") hideLightbox();
      if (e.key === "ArrowLeft") goToImage(currentIdx - 1);
      if (e.key === "ArrowRight") goToImage(currentIdx + 1);
    });

    document.addEventListener("click", (e) => {
      if (e.target.classList.contains("osm-popup-photo")) {
        const img = e.target;
        const src = img.getAttribute("data-fullsrc");
        const idx = parseInt(img.getAttribute("data-photo-idx"), 10);

        const gallery = img.closest(".osm-popup-gallery");
        const images = Array.from(
          gallery.querySelectorAll(".osm-popup-photo"),
        ).map((el) => el.getAttribute("data-fullsrc"));

        showLightbox(images.indexOf(src), images);
      }
    });
  }

  function initAllMaps() {
    setupPhotoLightbox();
    document.querySelectorAll(".osm-map").forEach(initMap);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initAllMaps);
  } else {
    initAllMaps();
  }
})();
