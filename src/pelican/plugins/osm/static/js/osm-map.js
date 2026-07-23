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
    placeCount: (n) => `${n} place${n === 1 ? "" : "s"}`,
    viewInTable: "View in table",
    fieldLabels: {},
  };

  // Built-in translations keyed by language prefix
  const BUILTIN_I18N = {
    zh: {
      placeCount: (n) => `${n} 個地點`,
      loadError: "無法載入地圖資料",
      noPlaces: "找不到地點",
      viewInTable: "在表格中檢視",
      fieldLabels: {
        date: "日期",
        category: "分類",
        type: "分類",
        city: "城市",
        country: "國家",
        notes: "備註",
        note: "備註",
        anime: "作品",
        work: "作品",
        series: "系列",
      },
    },
    ja: {
      placeCount: (n) => `${n} 件`,
      loadError: "地図データの読み込みに失敗しました",
      noPlaces: "場所が見つかりません",
      fieldLabels: {},
    },
  };

  // Detect language from <html lang="...">
  function detectBuiltinI18n() {
    const lang = (document.documentElement.lang || "").toLowerCase();
    if (!lang) return {};
    // Try exact match first (e.g. "zh-tw"), then prefix (e.g. "zh")
    return BUILTIN_I18N[lang] || BUILTIN_I18N[lang.split("-")[0]] || {};
  }

  const detectedI18n = detectBuiltinI18n();
  // Priority: user overrides > detected language > defaults
  const i18n = Object.assign({}, DEFAULT_I18N, detectedI18n, window.OSM_I18N || {});
  i18n.fieldLabels = Object.assign(
    {},
    DEFAULT_I18N.fieldLabels,
    detectedI18n.fieldLabels || {},
    (window.OSM_I18N || {}).fieldLabels,
  );

  // ── Dynamic loader for Leaflet.markercluster ──────────────────
  const MARKERCLUSTER_CDN = "https://unpkg.com/leaflet.markercluster@1/dist";
  let _clusterReady = null; // Promise, resolved once loaded (or skipped)

  function loadMarkerCluster() {
    if (_clusterReady) return _clusterReady;

    // Already loaded (user added script tags manually)
    if (typeof L.markerClusterGroup === "function") {
      _clusterReady = Promise.resolve(true);
      return _clusterReady;
    }

    _clusterReady = new Promise((resolve) => {
      // Load CSS
      for (const file of ["MarkerCluster.css", "MarkerCluster.Default.css"]) {
        const link = document.createElement("link");
        link.rel = "stylesheet";
        link.href = `${MARKERCLUSTER_CDN}/${file}`;
        document.head.appendChild(link);
      }

      // Load JS
      const script = document.createElement("script");
      script.src = `${MARKERCLUSTER_CDN}/leaflet.markercluster.js`;
      script.onload = () => resolve(true);
      script.onerror = () => {
        console.warn("pelican-osm: failed to load markercluster from CDN");
        resolve(false);
      };
      document.head.appendChild(script);
    });

    return _clusterReady;
  }

  // ── HTML safety helpers ───────────────────────────────────────
  // The popup is built as an HTML string and parsed by Leaflet, so every
  // user-supplied value (YAML place names, tags, field values, URL labels)
  // must be escaped before interpolation. `safeUrl` also rejects URL schemes
  // that can execute script (javascript:, data:, vbscript:) — relative URLs
  // and the http/https/mailto/tel set are allowed through unchanged.
  function esc(value) {
    if (value === null || value === undefined) return "";
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  const SAFE_URL_SCHEMES = /^(?:https?:|mailto:|tel:|\/|\.|#|\?)/i;
  function safeUrl(value) {
    if (value === null || value === undefined) return "#";
    const s = String(value).trim();
    if (!s) return "#";
    return SAFE_URL_SCHEMES.test(s) ? s : "#";
  }

  // ── Field label resolution ────────────────────────────────────
  const HIDDEN_FIELDS = new Set([
    "name",
    "lat",
    "lon",
    "id",
    "slug",
    "tags",
    "images",
    "urls",
    "_osm_icon",
  ]);

  function fieldLabel(key, perMapLabels) {
    // Precedence: schema-supplied per-map label (locale-aware, built at
    // render time) > globally configured/built-in i18n.fieldLabels >
    // derived from the field name itself.
    if (perMapLabels && perMapLabels[key]) return perMapLabels[key];
    if (i18n.fieldLabels[key]) return i18n.fieldLabels[key];
    return key.charAt(0).toUpperCase() + key.slice(1).replace(/_/g, " ");
  }

  // ── Popup builder ─────────────────────────────────────────────
  function buildPopupHtml(props, lat, lon, images, perMapLabels) {
    const tagBadges =
      Array.isArray(props.tags) && props.tags.length
        ? `<div class="osm-popup-tags">${props.tags
            .map(
              (t) =>
                `<span class="osm-badge osm-badge--tag">${esc(t)}</span>`,
            )
            .join("")}</div>`
        : "";

    const fieldLines = Object.entries(props)
      .filter(([key]) => !HIDDEN_FIELDS.has(key))
      .map(
        ([key, value]) =>
          `<div class="osm-popup-field"><span class="osm-popup-label">${esc(fieldLabel(key, perMapLabels))}:</span> ${esc(value)}</div>`,
      )
      .join("");

    // lat/lon come from GeoJSON coordinates (numbers); safe to interpolate.
    const osmUrl = `https://www.openstreetmap.org/?mlat=${lat}&mlon=${lon}&zoom=16`;
    const googleUrl = `https://www.google.com/maps?q=${lat},${lon}`;
    const links =
      `<div class="osm-popup-links">` +
      `<a href="${esc(osmUrl)}" target="_blank" rel="noopener" title="OpenStreetMap">🗺️</a>` +
      ` · ` +
      `<a href="${esc(googleUrl)}" target="_blank" rel="noopener" title="Google Maps">📍</a>` +
      `</div>`;

    // urls is normalized by Python to [{label, href}, ...]
    const urlEntries = Array.isArray(props.urls) ? props.urls : [];
    const postLink =
      urlEntries.length > 0
        ? `<div class="osm-popup-post-link">` +
          urlEntries
            .map(({ label, href }) => {
              let text = label;
              if (!text) {
                try {
                  text = new URL(href).hostname;
                } catch {
                  text = "Link";
                }
              }
              return `<a href="${esc(safeUrl(href))}" target="_blank" rel="noopener">${esc(text)}</a>`;
            })
            .join(" | ") +
          `</div>`
        : "";

    const photoGallery =
      images && images.length > 0
        ? `<div class="osm-popup-gallery">` +
          images
            .map(
              (img, idx) =>
                `<img src="${esc(safeUrl(img))}" alt="Place photo" class="osm-popup-photo" data-fullsrc="${esc(safeUrl(img))}" data-photo-idx="${idx}">`,
            )
            .join("") +
          `</div>`
        : "";

    // Only emit the table link when an anchor for this slug actually
    // exists on the page — keeps the popup clean on map-only articles.
    // For items-expanded places the parent slug doesn't appear as an id;
    // fall back to data-osm-parent-slug, which marks every expanded row.
    let tableAnchorId = null;
    if (props.slug) {
      const direct = document.getElementById("osm-place-" + props.slug);
      if (direct) {
        tableAnchorId = direct.id;
      } else {
        const parentMatch = document.querySelector(
          `[data-osm-parent-slug="${CSS.escape(props.slug)}"]`,
        );
        if (parentMatch) tableAnchorId = parentMatch.id;
      }
    }
    const tableLink = tableAnchorId
      ? `<div class="osm-popup-table-link"><a href="#${esc(tableAnchorId)}">${esc(i18n.viewInTable)}</a></div>`
      : "";

    return (
      `<div class="osm-popup">` +
      `<strong class="osm-popup-name">${esc(props.name)}</strong>` +
      tagBadges +
      fieldLines +
      links +
      postLink +
      tableLink +
      photoGallery +
      `</div>`
    );
  }

  // ── Add GeoJSON features to map ───────────────────────────────
  function addFeatures(
    layer,
    features,
    fragment,
    markers,
    imagesMap,
    layerField,
    perMapLabels,
  ) {
    for (const feature of features) {
      if (feature.geometry?.type !== "Point") continue;
      const props = feature.properties || {};
      if (!props.name) continue;

      // Fragment filter: match by id or name
      if (fragment && props.id !== fragment && props.name !== fragment)
        continue;

      const [lon, lat] = feature.geometry.coordinates;
      const marker = props._osm_icon
        ? L.marker([lat, lon], {
            icon: L.divIcon({
              html: `<span class="osm-marker-icon">${esc(props._osm_icon)}</span>`,
              className: "osm-marker-icon-wrapper",
              iconSize: [28, 28],
              iconAnchor: [14, 28],
              popupAnchor: [0, -28],
            }),
          })
        : L.marker([lat, lon]);
      marker.addTo(layer);
      const placeKey = props.id || props.name;
      marker._osmPlaceId = props.id || null;
      marker._osmPlaceName = props.name;
      marker._osmPlaceSlug = props.slug || null;
      marker._osmTags = Array.isArray(props.tags) ? props.tags : [];
      marker._osmLayer = layerField ? (props[layerField] || null) : null;
      const images = imagesMap[placeKey] || [];
      marker.bindPopup(buildPopupHtml(props, lat, lon, images, perMapLabels), {
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

      const isCssFullscreen = mapBlock.classList.contains(
        "osm-map-block--fullscreen",
      );
      const isNativeFullscreen = document.fullscreenElement === mapBlock;

      if (!isCssFullscreen && !isNativeFullscreen) {
        // Enter fullscreen - try native first
        if (mapBlock.requestFullscreen) {
          try {
            await mapBlock.requestFullscreen();
            fsBtn.classList.add("osm-fullscreen-btn--active");
          } catch (err) {
            // Fallback to CSS fullscreen
            mapBlock.classList.add("osm-map-block--fullscreen");
            fsBtn.classList.add("osm-fullscreen-btn--active");
          }
        } else {
          // No native fullscreen support, use CSS
          mapBlock.classList.add("osm-map-block--fullscreen");
          fsBtn.classList.add("osm-fullscreen-btn--active");
        }
      } else {
        // Exit fullscreen
        if (isNativeFullscreen) {
          document.exitFullscreen();
        }
        mapBlock.classList.remove("osm-map-block--fullscreen");
        fsBtn.classList.remove("osm-fullscreen-btn--active");
      }
    });

    mapContainer.parentElement.insertBefore(fsBtn, mapContainer.nextSibling);

    // Listen for native fullscreen changes
    document.addEventListener("fullscreenchange", () => {
      setTimeout(() => {
        if (window.L && window.L.Map) {
          document.querySelectorAll(".osm-map").forEach((el) => {
            if (el._leaflet_map) {
              el._leaflet_map.invalidateSize();
            }
          });
        }
      }, 100);
    });

    // Handle exit when pressing Esc
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        // Check if lightbox is open
        const lightbox = document.getElementById("osm-photo-lightbox");
        if (lightbox && lightbox.classList.contains("osm-lightbox--active")) {
          // Let lightbox handle Esc
          return;
        }

        // Exit fullscreen (both native and CSS)
        const fullscreenMapBlock = document.querySelector(
          ".osm-map-block--fullscreen",
        );
        if (fullscreenMapBlock) {
          e.preventDefault();
          fullscreenMapBlock.classList.remove("osm-map-block--fullscreen");
          const btn = fullscreenMapBlock.querySelector(".osm-fullscreen-btn");
          if (btn) btn.classList.remove("osm-fullscreen-btn--active");
        }

        // Also exit native fullscreen
        if (document.fullscreenElement) {
          e.preventDefault();
          document.exitFullscreen().then(() => {
            const fullscreenMapBlock = document.querySelector(
              ".osm-map-block--fullscreen",
            );
            if (fullscreenMapBlock) {
              fullscreenMapBlock.classList.remove("osm-map-block--fullscreen");
              const btn = fullscreenMapBlock.querySelector(
                ".osm-fullscreen-btn",
              );
              if (btn) btn.classList.remove("osm-fullscreen-btn--active");
            }
          });
        }
      }
    });
  }

  // ── Reset view button ──────────────────────────────────────────
  function setupResetButton(mapEl, map, initialView) {
    const btn = document.createElement("button");
    btn.className = "osm-reset-btn";
    btn.setAttribute("title", i18n.resetView || "Reset view");
    btn.innerHTML = "↺";

    btn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      if (initialView.bounds) {
        map.fitBounds(initialView.bounds);
      } else {
        map.setView(initialView.center, initialView.zoom);
      }
    });

    mapEl.parentElement.insertBefore(btn, mapEl.nextSibling);
  }

  // ── Shared filter state + marker reconciler ──────────────────
  function makeFilterState(map, markers, clusterGroup, initialView) {
    const state = { tag: null, layer: null };

    function refitBounds(visible) {
      if (visible.length === 0) return;
      if (visible.length === 1) {
        map.setView(visible[0].getLatLng(), 14);
      } else {
        map.fitBounds(L.featureGroup(visible).getBounds().pad(0.15));
      }
    }

    function apply() {
      const { tag, layer } = state;
      const visible = [];
      if (clusterGroup) {
        clusterGroup.clearLayers();
        markers.forEach((m) => {
          const ok = (!tag || m._osmTags.includes(tag)) &&
                     (!layer || m._osmLayer === layer);
          if (ok) { clusterGroup.addLayer(m); visible.push(m); }
        });
      } else {
        markers.forEach((m) => {
          const ok = (!tag || m._osmTags.includes(tag)) &&
                     (!layer || m._osmLayer === layer);
          if (ok) { m.addTo(map); visible.push(m); }
          else m.remove();
        });
      }
      if (!tag && !layer) {
        if (initialView.bounds) map.fitBounds(initialView.bounds);
        else map.setView(initialView.center, initialView.zoom);
      } else {
        refitBounds(visible);
      }
      return visible;
    }

    const callbacks = [];
    function onApply(cb) { callbacks.push(cb); }
    const _apply = apply;
    function applyAndNotify() {
      const visible = _apply();
      callbacks.forEach((cb) => cb(visible));
      return visible;
    }

    return { state, apply: applyAndNotify, markers, onApply };
  }

  // ── Map tag filter ────────────────────────────────────────────
  function setupMapTagFilter(mapEl, filterCtx) {
    const { state, apply } = filterCtx;
    const markers = filterCtx.markers;

    const allTags = new Set();
    for (const m of markers) {
      for (const t of m._osmTags) allTags.add(t);
    }
    if (allTags.size === 0) return;

    const mapBlock = mapEl.closest(".osm-map-block");
    if (!mapBlock) return;

    const bar = document.createElement("div");
    bar.className = "osm-map-tag-bar";

    function setTag(tag) {
      state.tag = tag;
      bar.querySelectorAll(".osm-map-tag-chip").forEach((c) => {
        const isActive = c.dataset.tag === tag;
        c.classList.toggle("osm-map-tag-chip--active", isActive);
        c.textContent = isActive ? tag + " ✕" : c.dataset.tag;
      });
      apply();
    }

    for (const tag of allTags) {
      const chip = document.createElement("button");
      chip.className = "osm-map-tag-chip";
      chip.dataset.tag = tag;
      chip.textContent = tag;
      chip.addEventListener("click", (e) => {
        e.preventDefault();
        setTag(state.tag === tag ? null : tag);
      });
      bar.appendChild(chip);
    }

    mapBlock.insertBefore(bar, mapEl.nextSibling);

    // When any filter changes, hide tag chips that have no visible markers
    filterCtx.onApply((visible) => {
      bar.querySelectorAll(".osm-map-tag-chip").forEach((chip) => {
        const tag = chip.dataset.tag;
        const hasVisible = visible.some((m) => m._osmTags.includes(tag));
        chip.style.display = hasVisible || state.tag === tag ? "" : "none";
      });
    });
  }

  // ── Layer filter (x-osm-map-layer field) ─────────────────────
  function setupMapLayerFilter(mapEl, filterCtx) {
    const { state, apply, markers } = filterCtx;

    const allLayers = [...new Set(markers.map((m) => m._osmLayer).filter(Boolean))].sort();
    if (allLayers.length <= 1) return;

    const mapBlock = mapEl.closest(".osm-map-block");
    if (!mapBlock) return;

    const bar = document.createElement("div");
    bar.className = "osm-map-layer-bar";

    if (allLayers.length > 10) {
      // ── Select + explicit clear button ──────────────────────
      const labelEl = document.createElement("label");
      labelEl.className = "osm-map-layer-label";
      labelEl.textContent = "作品：";

      const select = document.createElement("select");
      select.className = "osm-map-layer-select";

      const defaultOpt = document.createElement("option");
      defaultOpt.value = "";
      defaultOpt.textContent = `全部（${allLayers.length}）`;
      select.appendChild(defaultOpt);
      for (const layer of allLayers) {
        const opt = document.createElement("option");
        opt.value = layer;
        opt.textContent = layer;
        select.appendChild(opt);
      }

      const clearBtn = document.createElement("button");
      clearBtn.className = "osm-map-layer-clear";
      clearBtn.textContent = "✕";
      clearBtn.title = "清除篩選";
      clearBtn.style.display = "none";

      function syncClearBtn() {
        clearBtn.style.display = state.layer ? "" : "none";
      }

      select.addEventListener("change", () => {
        state.layer = select.value || null;
        syncClearBtn();
        apply();
      });
      clearBtn.addEventListener("click", (e) => {
        e.preventDefault();
        state.layer = null;
        select.value = "";
        syncClearBtn();
        apply();
      });

      labelEl.appendChild(select);
      bar.appendChild(labelEl);
      bar.appendChild(clearBtn);

      filterCtx.onApply(() => {
        // Disable options with no markers under TAG filter only —
        // never disable based on layer filter so user can switch freely.
        const tagFiltered = new Set(
          markers
            .filter((m) => !state.tag || m._osmTags.includes(state.tag))
            .map((m) => m._osmLayer)
            .filter(Boolean),
        );
        select.querySelectorAll("option").forEach((opt) => {
          if (!opt.value) return;
          opt.disabled = !tagFiltered.has(opt.value);
        });
        select.value = state.layer || "";
        syncClearBtn();
      });

    } else {
      // ── Chips (≤ 10 layers) ──────────────────────────────────
      function setLayer(layer) {
        state.layer = layer;
        bar.querySelectorAll(".osm-map-layer-chip").forEach((c) => {
          const isActive = c.dataset.layer === layer;
          c.classList.toggle("osm-map-layer-chip--active", isActive);
          c.textContent = isActive ? layer + " ✕" : c.dataset.layer;
        });
        apply();
      }

      for (const layer of allLayers) {
        const chip = document.createElement("button");
        chip.className = "osm-map-layer-chip";
        chip.dataset.layer = layer;
        chip.textContent = layer;
        chip.addEventListener("click", (e) => {
          e.preventDefault();
          setLayer(state.layer === layer ? null : layer);
        });
        bar.appendChild(chip);
      }

      filterCtx.onApply((visible) => {
        const visibleLayers = new Set(visible.map((m) => m._osmLayer).filter(Boolean));
        bar.querySelectorAll(".osm-map-layer-chip").forEach((chip) => {
          const layer = chip.dataset.layer;
          chip.style.display = visibleLayers.has(layer) || state.layer === layer ? "" : "none";
        });
      });
    }

    const tagBar = mapBlock.querySelector(".osm-map-tag-bar");
    mapBlock.insertBefore(bar, tagBar || mapEl.nextSibling);
  }

  // ── Map init ──────────────────────────────────────────────────
  async function initMap(el) {
    const rawEntries = el.getAttribute("data-geojson");
    const tileUrl = el.getAttribute("data-tile");
    const attribution = el.getAttribute("data-attribution");
    const rawImages = el.getAttribute("data-images");
    const layerField = el.getAttribute("data-osm-layer-field") || null;
    const rawFieldLabels = el.getAttribute("data-osm-field-labels");

    let entries;
    let imagesData = {};
    let perMapLabels = null;
    try {
      entries = JSON.parse(rawEntries);
      if (rawImages) {
        imagesData = JSON.parse(rawImages);
      }
      if (rawFieldLabels) {
        perMapLabels = JSON.parse(rawFieldLabels);
      }
    } catch (e) {
      console.error("pelican-osm: failed to parse attributes", e);
      return;
    }

    if (!entries || entries.length === 0) return;

    // Load markercluster before initializing the map
    await loadMarkerCluster();

    // Remove loading indicator
    const loader = el.querySelector(".osm-map-loading");
    if (loader) loader.remove();

    const map = L.map(el.id);
    el._leaflet_map = map;
    L.tileLayer(tileUrl, { attribution, maxZoom: 18 }).addTo(map);

    const markers = [];

    // Use marker clustering if Leaflet.markercluster is loaded
    const clusterGroup =
      typeof L.markerClusterGroup === "function"
        ? L.markerClusterGroup()
        : null;
    const markerLayer = clusterGroup || map;

    const results = await Promise.allSettled(
      entries.map((entry) =>
        fetch(entry.url).then((r) => {
          if (!r.ok) throw new Error(`${r.status} ${entry.url}`);
          return r.json().then((fc) => ({ fc, fragment: entry.fragment }));
        }),
      ),
    );

    let fetchErrors = 0;
    for (const result of results) {
      if (result.status === "fulfilled") {
        const { fc, fragment } = result.value;
        addFeatures(
          markerLayer,
          fc.features || [],
          fragment,
          markers,
          imagesData,
          layerField,
          perMapLabels,
        );
      } else {
        fetchErrors++;
        console.warn("pelican-osm: failed to fetch GeoJSON:", result.reason);
      }
    }

    if (markers.length === 0) {
      const msg = document.createElement("div");
      msg.className = "osm-map-empty";
      msg.textContent =
        fetchErrors === entries.length
          ? (i18n.loadError || "Failed to load map data")
          : (i18n.noPlaces || "No places found");
      el.appendChild(msg);
      return;
    }

    // Add cluster group to map
    if (clusterGroup) {
      map.addLayer(clusterGroup);
    }

    // Setup fullscreen button
    setupFullscreenButton(el, el);

    // Set map view and save initial bounds for reset
    let initialView;
    if (markers.length === 1) {
      const latlng = markers[0].getLatLng();
      map.setView(latlng, 14);
      initialView = { center: latlng, zoom: 14 };
    } else {
      const group = clusterGroup || L.featureGroup(markers);
      const bounds = group.getBounds().pad(0.15);
      map.fitBounds(bounds);
      initialView = { bounds };
    }

    // Reset view button
    setupResetButton(el, map, initialView);

    // Shared filter state — both tag and layer filters use AND logic
    const filterCtx = makeFilterState(map, markers, clusterGroup, initialView);

    // Tag filtering on map
    setupMapTagFilter(el, filterCtx);

    // Layer filtering (x-osm-map-layer field)
    setupMapLayerFilter(el, filterCtx);

    // Scroll popup to top on open so name/info is visible before photos
    map.on("popupopen", (e) => {
      requestAnimationFrame(() => {
        const popupEl = e.popup.getElement();
        if (!popupEl) return;
        const wrapper = popupEl.querySelector(".leaflet-popup-content-wrapper");
        const content = popupEl.querySelector(".leaflet-popup-content");
        if (wrapper) wrapper.scrollTop = 0;
        if (content) content.scrollTop = 0;
      });
    });

    // Deep linking: open popup when URL hash matches a place id/name, or the
    // table-row anchor (#osm-place-<slug>) emitted by the place_list shortcode.
    // hashchange handles in-page clicks from the popup's "view in table" link.
    function openMarkerFromHash() {
      const hash = decodeURIComponent(window.location.hash.slice(1));
      if (!hash) return;
      const slugFromAnchor = hash.startsWith("osm-place-")
        ? hash.slice("osm-place-".length)
        : null;
      const target = markers.find(
        (m) =>
          m._osmPlaceId === hash ||
          m._osmPlaceName === hash ||
          (slugFromAnchor && m._osmPlaceSlug === slugFromAnchor),
      );
      if (!target) return;
      if (clusterGroup && clusterGroup.zoomToShowLayer) {
        clusterGroup.zoomToShowLayer(target, () => {
          target.openPopup();
        });
      } else {
        map.setView(target.getLatLng(), 16);
        target.openPopup();
      }
    }
    openMarkerFromHash();
    window.addEventListener("hashchange", openMarkerFromHash);
  }

  function setupPhotoLightbox() {
    let currentIdx = 0;
    let currentImages = [];

    const lightboxHtml = `
      <div id="osm-photo-lightbox" class="osm-lightbox" tabindex="-1">
        <div class="osm-lightbox-overlay"></div>
        <div class="osm-lightbox-container" tabindex="-1">
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
    const container = lightbox.querySelector(".osm-lightbox-container");

    function showLightbox(idx, images) {
      currentIdx = idx;
      currentImages = images;
      lightboxImg.src = "";
      lightboxImg.src = images[idx];
      lightboxInfo.textContent = `${idx + 1} / ${images.length}`;

      // In native fullscreen only the fullscreen element and its descendants
      // are rendered — move the lightbox inside it so it stays visible.
      // Use position:absolute (not fixed) so it fills the fullscreen element
      // reliably across browsers (Firefox ignores fixed z-index in this context).
      const fsEl = document.fullscreenElement;
      if (fsEl) {
        if (!fsEl.contains(lightbox)) fsEl.appendChild(lightbox);
        lightbox.style.position = "absolute";
        lightbox.style.zIndex = "2147483647";
      } else {
        if (lightbox.parentElement !== document.body)
          document.body.appendChild(lightbox);
        lightbox.style.position = "";
        lightbox.style.zIndex = "";
      }

      lightbox.classList.add("osm-lightbox--active");
      document.body.style.overflow = "hidden";
      lightbox.focus();
    }

    function hideLightbox() {
      lightbox.classList.remove("osm-lightbox--active");
      document.body.style.overflow = "";
      // Return lightbox to body and reset inline styles for next use.
      if (lightbox.parentElement !== document.body) {
        document.body.appendChild(lightbox);
      }
      lightbox.style.position = "";
      lightbox.style.zIndex = "";
    }

    function goToImage(idx) {
      if (idx < 0 || idx >= currentImages.length) return;
      currentIdx = idx;
      lightboxImg.src = currentImages[idx];
      lightboxInfo.textContent = `${idx + 1} / ${currentImages.length}`;
    }

    closeBtn.addEventListener("click", hideLightbox);
    overlay.addEventListener("click", hideLightbox);
    container.addEventListener("click", hideLightbox);
    lightboxImg.addEventListener("click", (e) => e.stopPropagation());

    prevBtn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      goToImage(currentIdx - 1);
      prevBtn.blur();
    });

    nextBtn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      goToImage(currentIdx + 1);
      nextBtn.blur();
    });

    document.addEventListener(
      "keydown",
      (e) => {
        if (!lightbox.classList.contains("osm-lightbox--active")) return;
        if (e.key === "Escape") {
          e.preventDefault();
          e.stopPropagation();
          hideLightbox();
        }
        if (e.key === "ArrowLeft") {
          e.preventDefault();
          e.stopPropagation();
          goToImage(currentIdx - 1);
        }
        if (e.key === "ArrowRight") {
          e.preventDefault();
          e.stopPropagation();
          goToImage(currentIdx + 1);
        }
      },
      true,
    ); // Use capture phase to intercept first

    // Touch swipe support for mobile
    let touchStartX = 0;
    const SWIPE_THRESHOLD = 50;

    lightbox.addEventListener("touchstart", (e) => {
      touchStartX = e.changedTouches[0].screenX;
    }, { passive: true });

    lightbox.addEventListener("touchend", (e) => {
      if (!lightbox.classList.contains("osm-lightbox--active")) return;
      const dx = e.changedTouches[0].screenX - touchStartX;
      if (Math.abs(dx) < SWIPE_THRESHOLD) return;
      if (dx > 0) {
        goToImage(currentIdx - 1); // swipe right → previous
      } else {
        goToImage(currentIdx + 1); // swipe left → next
      }
    });

    document.addEventListener(
      "click",
      (e) => {
        if (e.target.classList.contains("osm-popup-photo")) {
          e.preventDefault();
          e.stopPropagation();

          const img = e.target;
          const src = img.getAttribute("data-fullsrc");
          const idx = parseInt(img.getAttribute("data-photo-idx"), 10);

          const gallery = img.closest(".osm-popup-gallery");
          const images = Array.from(
            gallery.querySelectorAll(".osm-popup-photo"),
          ).map((el) => el.getAttribute("data-fullsrc"));

          showLightbox(images.indexOf(src), images);
        }
      },
      true,
    ); // Use capture phase

    return showLightbox;
  }

  // ── Sortable place-list tables ────────────────────────────────
  function cellSortValue(cell) {
    // Prefer explicit data-sort-value (set on Name cells to exclude icon text)
    if (cell.dataset.sortValue !== undefined) return cell.dataset.sortValue;
    return cell.textContent.trim();
  }

  function compareValues(a, b, dir) {
    // Date (YYYY-MM-DD or similar) — checked before numeric so "2018-11-26"
    // isn't truncated to 2018 by parseFloat
    const aD = Date.parse(a),
      bD = Date.parse(b);
    if (!isNaN(aD) && !isNaN(bD)) return dir === "asc" ? aD - bD : bD - aD;
    // Numeric
    const aN = parseFloat(a),
      bN = parseFloat(b);
    if (!isNaN(aN) && !isNaN(bN)) return dir === "asc" ? aN - bN : bN - aN;
    // String
    return dir === "asc" ? a.localeCompare(b) : b.localeCompare(a);
  }

  function setSortIcon(th, dir) {
    const existing = th.querySelector(".osm-sort-icon");
    if (existing) existing.remove();
    const icon = document.createElement("span");
    icon.className = "osm-sort-icon";
    if (dir === "asc") {
      icon.textContent = "▲";
      icon.dataset.active = "1";
    } else if (dir === "desc") {
      icon.textContent = "▼";
      icon.dataset.active = "1";
    } else {
      icon.textContent = "⇅";
    } // unsorted hint
    th.appendChild(icon);
  }

  function sortTable(table, th, colIdx, originalRows) {
    const thElements = table.querySelectorAll("thead th");
    const currentDir = th.dataset.sort; // "asc" | "desc" | undefined
    // Cycle: none → asc → desc → none
    const newDir = !currentDir ? "asc" : currentDir === "asc" ? "desc" : null;

    // Reset all headers to neutral

    thElements.forEach((h) => {
      delete h.dataset.sort;
      setSortIcon(h, null);
    });

    const tbody = table.querySelector("tbody");

    if (newDir === null) {
      // Restore original order
      originalRows.forEach((r) => tbody.appendChild(r));
      return;
    }

    th.dataset.sort = newDir;
    setSortIcon(th, newDir);

    // Sort within each contiguous run of data rows so group_summary_at
    // header rows stay pinned in place and groups aren't reshuffled into
    // each other. Header rows have a colspan and lack the column we're
    // sorting on, so including them in the sort produced garbage.
    const children = Array.from(tbody.children);
    const reattach = [];
    let run = [];
    const flushRun = () => {
      if (run.length === 0) return;
      run.sort((a, b) =>
        compareValues(
          cellSortValue(a.cells[colIdx]),
          cellSortValue(b.cells[colIdx]),
          newDir,
        ),
      );
      reattach.push(...run);
      run = [];
    };
    for (const row of children) {
      if (row.classList.contains("osm-group-header")) {
        flushRun();
        reattach.push(row);
      } else {
        run.push(row);
      }
    }
    flushRun();
    reattach.forEach((r) => tbody.appendChild(r));
  }

  // ── Tag filtering for place-list tables ────────────────────────
  function initTableTagFilter(table, originalRows, countEl) {
    const wrapper = table.closest(".osm-place-list-wrapper");
    let activeTag = null;
    let chipEl = null;

    function updateCount() {
      if (!countEl) return;
      const visible = originalRows.filter(
        (r) =>
          !r.classList.contains("osm-group-header") &&
          r.style.display !== "none",
      ).length;
      countEl.textContent =
        typeof i18n.placeCount === "function"
          ? i18n.placeCount(visible)
          : `${visible} places`;
    }

    function clearFilter() {
      activeTag = null;
      originalRows.forEach((r) => (r.style.display = ""));
      if (chipEl) {
        chipEl.remove();
        chipEl = null;
      }
      updateCount();
    }

    function applyFilter(tag) {
      if (tag === activeTag) {
        clearFilter();
        return;
      }
      activeTag = tag;
      originalRows.forEach((r) => {
        const badges = Array.from(
          r.querySelectorAll(".osm-badge--tag"),
        ).map((b) => b.textContent.trim());
        r.style.display = badges.includes(tag) ? "" : "none";
      });

      // Show or update filter chip
      if (!chipEl) {
        chipEl = document.createElement("span");
        chipEl.className = "osm-tag-filter-chip";
        chipEl.addEventListener("click", clearFilter);
        countEl.parentElement.insertBefore(chipEl, countEl);
      }
      chipEl.textContent = tag + " ✕";
      updateCount();
    }

    table.addEventListener("click", (e) => {
      const badge = e.target.closest(".osm-badge--tag");
      if (!badge || !table.contains(badge)) return;
      e.preventDefault();
      applyFilter(badge.textContent.trim());
    });
  }

  // ── Multi-level group_summary_at: click/keyboard to collapse a subtree ───
  function initGroupCollapse(table) {
    const tbody = table.querySelector("tbody");
    if (!tbody) return;
    const headers = Array.from(
      tbody.querySelectorAll("tr.osm-group-header"),
    );
    if (headers.length === 0) return;

    function recompute() {
      // Walk top-down. `collapsedAt` is the depth of the shallowest
      // currently-collapsed ancestor; rows beneath stay hidden until we
      // emerge to a header at that depth or shallower.
      let collapsedAt = null;
      Array.from(tbody.children).forEach((row) => {
        if (row.classList.contains("osm-group-header")) {
          const d = Number(row.dataset.depth) || 0;
          if (collapsedAt !== null && d <= collapsedAt) {
            collapsedAt = null;
          }
          const hidden = collapsedAt !== null && d > collapsedAt;
          row.classList.toggle("osm-row-hidden", hidden);
          if (
            !hidden &&
            row.classList.contains("osm-group-header--collapsed") &&
            collapsedAt === null
          ) {
            collapsedAt = d;
          }
        } else {
          row.classList.toggle("osm-row-hidden", collapsedAt !== null);
        }
      });
    }

    headers.forEach((h) => {
      h.setAttribute("role", "button");
      h.setAttribute("tabindex", "0");
      h.setAttribute("aria-expanded", "true");
      function toggle(e) {
        // Let real anchor clicks (e.g. inside a header title) behave normally
        if (e && e.target && e.target.closest("a")) return;
        const collapsed = h.classList.toggle("osm-group-header--collapsed");
        h.setAttribute("aria-expanded", collapsed ? "false" : "true");
        recompute();
      }
      h.addEventListener("click", toggle);
      h.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          toggle(e);
        }
      });
    });

    // If the URL targets a header inside this table, expand its ancestors
    // so the deep-link actually reveals the row.
    function expandToHash() {
      const id = (location.hash || "").slice(1);
      if (!id) return;
      const target = tbody.querySelector(`#${CSS.escape(id)}`);
      if (!target) return;
      let row = target.previousElementSibling;
      let depth = Number(target.dataset.depth) || 0;
      while (row && depth > 0) {
        if (row.classList.contains("osm-group-header")) {
          const d = Number(row.dataset.depth) || 0;
          if (d < depth) {
            row.classList.remove("osm-group-header--collapsed");
            row.setAttribute("aria-expanded", "true");
            depth = d;
          }
        }
        row = row.previousElementSibling;
      }
      target.classList.remove("osm-group-header--collapsed");
      target.setAttribute("aria-expanded", "true");
      recompute();
      target.scrollIntoView({ behavior: "smooth", block: "start" });
    }
    expandToHash();
    window.addEventListener("hashchange", expandToHash);
  }

  function initSortableTables(showLightbox) {
    document.querySelectorAll(".osm-place-list").forEach((table) => {
      const tbody = table.querySelector("tbody");
      // Snapshot original row order for reset
      const originalRows = Array.from(tbody.querySelectorAll("tr"));
      // Place count (and tag-filter visible-count) reflects real places, not
      // synthetic group_summary_at header rows.
      const dataRows = originalRows.filter(
        (r) => !r.classList.contains("osm-group-header"),
      );

      table.querySelectorAll("thead th").forEach((th, colIdx) => {
        if (th.classList.contains("osm-list-image-col-header")) return;
        th.setAttribute("data-sortable", "");
        th.style.cursor = "pointer";
        setSortIcon(th, null); // show ⇅ on every header from the start
        th.addEventListener("click", () =>
          sortTable(table, th, colIdx, originalRows),
        );
      });

      // Fill place count label
      const wrapper = table.closest(".osm-place-list-wrapper");
      const countEl = wrapper && wrapper.querySelector(".osm-place-list-count");
      if (countEl) {
        const n = dataRows.length;
        countEl.textContent =
          typeof i18n.placeCount === "function"
            ? i18n.placeCount(n)
            : `${n} places`;
      }

      // Tag filtering
      initTableTagFilter(table, originalRows, countEl);

      // Multi-level group_summary_at collapse
      initGroupCollapse(table);

      // Image icon → lightbox
      initImageRowExpand(table, showLightbox);
    });
  }

  function initImageRowExpand(table, showLightbox) {
    if (!showLightbox) return;
    const tbody = table.querySelector("tbody");
    if (!tbody) return;

    // Per-row images live in a single JSON sidecar next to the table
    // (one parse vs. one decode per row in the old data-images path).
    const wrapper = table.closest(".osm-place-list-wrapper");
    const sidecar = wrapper && wrapper.querySelector("script.osm-list-images");
    let imagesBySlug = null;
    function getImagesBySlug() {
      if (imagesBySlug !== null) return imagesBySlug;
      try {
        imagesBySlug = sidecar ? JSON.parse(sidecar.textContent) : {};
      } catch (e) {
        imagesBySlug = {};
      }
      return imagesBySlug;
    }

    tbody.addEventListener("click", (e) => {
      const iconCell = e.target.closest("td.osm-list-image-icon");
      if (!iconCell) return;
      const row = iconCell.closest("tr.osm-has-images");
      if (!row) return;
      e.stopPropagation();

      const slug = row.dataset.osmPlaceSlug;
      const images = slug ? getImagesBySlug()[slug] : null;
      if (images && images.length) showLightbox(0, images);
    });
  }

  function initCaptionToggle() {
    document.querySelectorAll(".osm-map-caption").forEach((caption) => {
      caption.addEventListener("click", () => {
        caption.classList.toggle("osm-map-caption--expanded");
      });
    });
  }

  function initAllMaps() {
    const showLightbox = setupPhotoLightbox();
    initSortableTables(showLightbox);
    initCaptionToggle();

    const mapEls = document.querySelectorAll(".osm-map");

    if ("IntersectionObserver" in window) {
      const observer = new IntersectionObserver(
        (entries) => {
          for (const entry of entries) {
            if (entry.isIntersecting) {
              observer.unobserve(entry.target);
              initMap(entry.target);
            }
          }
        },
        { rootMargin: "200px" },
      );
      mapEls.forEach((el) => observer.observe(el));
    } else {
      // Fallback: init all immediately
      mapEls.forEach(initMap);
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initAllMaps);
  } else {
    initAllMaps();
  }
})();
