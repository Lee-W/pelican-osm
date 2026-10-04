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
 *     placeCount:    (n) => `${n} 個地點`,
 *     viewInTable:   "在表格中檢視",
 *     resetView:     "重設地圖範圍",
 *     fieldLabels:   { date: "日期", location: "地點" },
 *   };
 */
(function () {
  "use strict";

  // Every component carries its build-time catalog. The generated default is
  // only for old markup. Legacy window.OSM_I18N overrides still win.
  function contextFor(element) {
    const component = window.Tabular.componentI18n(element);
    const fallback = window.OSM_DEFAULT_I18N || { words: {} };
    const words = { ...fallback.words, ...component.words };
    const overrides = window.OSM_I18N || {};
    const locale = component.locale || fallback.locale || "en";
    const result = { ...words, ...overrides, locale,
      dir: element.closest("[dir]")?.dir || "ltr",
      fieldLabels: { ...Object.fromEntries(Object.entries(words)
        .filter(([key]) => key.startsWith("field.")).map(([key, value]) => [key.slice(6), value])),
        ...overrides.fieldLabels },
    };
    result.format = (key, values) => window.Tabular.formatMessage(result[key], values, locale);
    result.formatCount = typeof result.placeCount === "function" ? result.placeCount
      : (n) => result.format("placeCount", { n });
    return result;
  }

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
    "osm_type",
    "osm_id",
  ]);

  const OSM_ENTITY_TYPES = new Set(["node", "way", "relation"]);

  function fieldLabel(key, perMapLabels, i18n) {
    // Precedence: schema-supplied per-map label (locale-aware, built at
    // render time) > globally configured/built-in i18n.fieldLabels >
    // derived from the field name itself.
    if (perMapLabels && Object.hasOwn(perMapLabels, key)) return perMapLabels[key];
    if (Object.hasOwn(i18n.fieldLabels, key)) return i18n.fieldLabels[key];
    return key.charAt(0).toUpperCase() + key.slice(1).replace(/_/g, " ");
  }

  // ── Popup builder ─────────────────────────────────────────────
  function buildPopupHtml(props, lat, lon, images, perMapLabels, i18n) {
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
      .filter(([key]) => !HIDDEN_FIELDS.has(key) && !key.startsWith("_osm_"))
      .map(
        ([key, value]) =>
          `<div class="osm-popup-field"><span class="osm-popup-label">${esc(fieldLabel(key, perMapLabels, i18n))}:</span> <span lang="${esc(props._osm_languages?.[key] || i18n.locale)}">${esc(value)}</span></div>`,
      )
      .join("");

    // lat/lon come from GeoJSON coordinates (numbers); safe to interpolate.
    // Point 🗺️ at the OSM entity page when the place carries a valid
    // osm_type + osm_id; otherwise fall back to a coordinate link.
    const osmUrl =
      OSM_ENTITY_TYPES.has(props.osm_type) &&
      props.osm_id !== undefined &&
      props.osm_id !== null
        ? `https://www.openstreetmap.org/${props.osm_type}/${encodeURIComponent(props.osm_id)}`
        : `https://www.openstreetmap.org/?mlat=${lat}&mlon=${lon}&zoom=16`;
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
                  text = i18n.link;
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
                `<button type="button" class="osm-popup-photo-button" aria-label="${esc(i18n.photo)} ${idx + 1}">` +
                `<img src="${esc(safeUrl(img))}" alt="${esc(i18n.photo)}" class="osm-popup-photo" data-fullsrc="${esc(safeUrl(img))}" data-photo-idx="${idx}"></button>`,
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
      `<div class="osm-popup" lang="${esc(i18n.locale)}" dir="${esc(i18n.dir)}">` +
      `<strong class="osm-popup-name" lang="${esc(props._osm_languages?.name || i18n.locale)}">${esc(props.name)}</strong>` +
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
  function searchText(value) {
    if (Array.isArray(value)) return value.map(searchText).join(" ");
    if (value && typeof value === "object") {
      return Object.entries(value)
        .filter(([key]) => !key.startsWith("_") && key !== "translations")
        .map(([, part]) => searchText(part)).join(" ");
    }
    return String(value ?? "");
  }

  function addFeatures(
    layer,
    features,
    fragment,
    markers,
    imagesMap,
    layerField,
    perMapLabels,
    i18n,
  ) {
    for (const feature of features) {
      if (feature.geometry?.type !== "Point") continue;
      const props = feature.properties || {};
      if (props.name === undefined || props.name === null) continue;

      // Fragment filter: match by id or name
      if (fragment && props.id !== fragment && props.name !== fragment && props._osm_source_name !== fragment)
        continue;

      const [lon, lat] = feature.geometry.coordinates;
      const marker = props._osm_icon
        ? L.marker([lat, lon], {
            title: props.name || i18n.fieldLabels.name,
            alt: props.name || i18n.fieldLabels.name,
            icon: L.divIcon({
              html: `<span class="osm-marker-icon">${esc(props._osm_icon)}</span>`,
              className: "osm-marker-icon-wrapper",
              iconSize: [28, 28],
              iconAnchor: [14, 28],
              popupAnchor: [0, -28],
            }),
          })
        : L.marker([lat, lon], { title: props.name || i18n.fieldLabels.name, alt: props.name || i18n.fieldLabels.name });
      marker.addTo(layer);
      const placeKey = props.id || props._osm_source_name || props.name;
      marker._osmPlaceId = props.id || null;
      marker._osmPlaceName = props.name;
      marker._osmSourceName = props._osm_source_name;
      marker._osmPlaceSlug = props.slug || null;
      marker._osmTags = Array.isArray(props.tags) ? props.tags : [];
      marker._osmLayer = layerField ? (props[layerField] || null) : null;
      marker._osmSearch = window.Tabular.normalizeSearch(
        [props._osm_search ?? searchText(props), props._osm_source_name || ""].join(" "),
      );
      const images = imagesMap[placeKey] || [];
      marker.bindPopup(buildPopupHtml(props, lat, lon, images, perMapLabels, i18n), {
        maxWidth: 280,
      });
      markers.push(marker);
    }
  }

  // ── Fullscreen handler ────────────────────────────────────────
  function setupFullscreenButton(mapContainer, mapElement, i18n) {
    const fsBtn = document.createElement("button");
    fsBtn.type = "button";
    fsBtn.className = "osm-fullscreen-btn";
    fsBtn.setAttribute("title", i18n.fullscreen);
    fsBtn.setAttribute("aria-label", i18n.fullscreen);
    fsBtn.setAttribute("aria-pressed", "false");
    fsBtn.innerHTML = "⛶";

    function syncFullscreen() {
      const block = mapContainer.closest(".osm-map-block");
      const active = document.fullscreenElement === block || block.classList.contains("osm-map-block--fullscreen");
      fsBtn.classList.toggle("osm-fullscreen-btn--active", active);
      fsBtn.setAttribute("aria-pressed", String(active));
    }

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
      syncFullscreen();
    });

    mapContainer.append(fsBtn);

    // Listen for native fullscreen changes
    document.addEventListener("fullscreenchange", () => {
      syncFullscreen();
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
        syncFullscreen();
      }
    });
  }

  // ── Reset view button ──────────────────────────────────────────
  function setupResetButton(mapEl, map, initialView, i18n) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "osm-reset-btn";
    btn.setAttribute("title", i18n.resetView);
    btn.setAttribute("aria-label", i18n.resetView);
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

    mapEl.append(btn);
  }

  // ── Shared filter state + marker reconciler ──────────────────
  function makeFilterState(map, markers, clusterGroup, initialView) {
    const state = { tag: null, layer: null, query: "" };

    function matches(marker, except) {
      return (!state.tag || except === "tag" || marker._osmTags.includes(state.tag)) &&
        (!state.layer || except === "layer" || marker._osmLayer === state.layer) &&
        marker._osmSearch.includes(window.Tabular.normalizeSearch(state.query.trim()));
    }

    function refitBounds(visible) {
      if (visible.length === 0) return;
      if (visible.length === 1) {
        map.setView(visible[0].getLatLng(), 14);
      } else {
        map.fitBounds(L.featureGroup(visible).getBounds().pad(0.15));
      }
    }

    function apply() {
      const { tag, layer, query } = state;
      const visible = markers.filter((marker) => matches(marker));
      map.closePopup();
      if (clusterGroup) {
        clusterGroup.clearLayers();
        clusterGroup.addLayers(visible);
      } else {
        const included = new Set(visible);
        markers.forEach((m) => {
          if (included.has(m)) m.addTo(map);
          else m.remove();
        });
      }
      if (!tag && !layer && !query.trim()) {
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

    return { state, apply: applyAndNotify, markers, onApply, matches };
  }

  function searchControls(root, i18n, onSearch, onClear) {
    const controls = document.createElement("div");
    controls.className = "osm-explorer-controls";
    const label = document.createElement("label");
    label.className = "osm-explorer-search";
    const text = document.createElement("span");
    text.className = "osm-sr-only";
    text.textContent = i18n.search;
    const input = document.createElement("input");
    input.type = "search";
    input.placeholder = i18n.search;
    input.addEventListener("input", () => onSearch(input.value));
    label.append(text, input);
    const clear = document.createElement("button");
    clear.type = "button";
    clear.className = "osm-explorer-clear";
    clear.textContent = i18n.clearFilter;
    clear.addEventListener("click", () => { input.value = ""; onClear(); });
    controls.append(label, clear);
    root.prepend(controls);
    return { controls, input, clear };
  }

  function setupMapExplorer(mapEl, filterCtx, i18n) {
    const root = mapEl.closest(".osm-map-block");
    const { state, markers, apply } = filterCtx;
    const tools = searchControls(root, i18n,
      (query) => { state.query = query; apply(); },
      () => { state.query = ""; state.tag = null; state.layer = null; apply(); });
    const panel = document.createElement("div");
    panel.className = "osm-map-filters";
    panel.id = `${mapEl.id}-filters`;
    panel.hidden = true;
    mapEl.after(panel);
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "osm-explorer-filter-toggle";
    toggle.setAttribute("aria-controls", panel.id);
    function syncToggle() {
      toggle.setAttribute("aria-expanded", String(!panel.hidden));
      const n = Number(Boolean(state.tag)) + Number(Boolean(state.layer));
      toggle.textContent = i18n.filters + (n ? ` (${n})` : "");
    }
    toggle.addEventListener("click", () => { panel.hidden = !panel.hidden; syncToggle(); });
    tools.controls.insertBefore(toggle, tools.clear);
    const count = document.createElement("div");
    count.className = "osm-explorer-count";
    count.setAttribute("role", "status");
    tools.controls.after(count);
    const empty = document.createElement("div");
    empty.className = "osm-map-no-results";
    empty.textContent = i18n.noResults;
    empty.hidden = true;
    mapEl.append(empty);
    filterCtx.onApply((visible) => {
      count.textContent = i18n.format("resultCount", { shown: visible.length, total: markers.length });
      empty.hidden = visible.length !== 0;
      tools.clear.disabled = !state.query && !state.tag && !state.layer;
      syncToggle();
    });
    return { panel, toggle };
  }

  // ── Map tag filter ────────────────────────────────────────────
  function setupMapTagFilter(panel, filterCtx, i18n) {
    const { state, apply } = filterCtx;
    const markers = filterCtx.markers;

    const allTags = new Set();
    for (const m of markers) {
      for (const t of m._osmTags) allTags.add(t);
    }
    if (allTags.size === 0) return;

    const bar = document.createElement("fieldset");
    bar.className = "osm-map-tag-bar";
    const legend = document.createElement("legend");
    legend.textContent = i18n.fieldLabels.tags;
    bar.append(legend);
    const options = document.createElement("div");
    options.className = "osm-map-filter-options";
    bar.append(options);

    function setTag(tag) {
      state.tag = tag;
      apply();
    }

    for (const tag of allTags) {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "osm-map-tag-chip";
      chip.dataset.tag = tag;
      chip.textContent = tag;
      chip.addEventListener("click", (e) => {
        e.preventDefault();
        setTag(state.tag === tag ? null : tag);
      });
      options.appendChild(chip);
    }

    panel.append(bar);

    // Facets consider the other filters so switching tags stays possible.
    filterCtx.onApply(() => {
      bar.querySelectorAll(".osm-map-tag-chip").forEach((chip) => {
        const tag = chip.dataset.tag;
        const active = state.tag === tag;
        chip.classList.toggle("osm-map-tag-chip--active", active);
        chip.setAttribute("aria-pressed", String(active));
        chip.disabled = !active && !markers.some((m) => filterCtx.matches(m, "tag") && m._osmTags.includes(tag));
      });
    });
  }

  // ── Layer filter (x-osm-map-layer field) ─────────────────────
  function setupMapLayerFilter(panel, filterCtx, i18n, perMapLabels, layerField) {
    const { state, apply, markers } = filterCtx;

    const allLayers = [...new Set(markers.map((m) => m._osmLayer).filter(Boolean))].sort((a, b) => window.Tabular.compare(a, b, "asc", i18n.locale));
    if (allLayers.length <= 1) return;

    const bar = document.createElement("fieldset");
    bar.className = "osm-map-layer-bar";
    const legend = document.createElement("legend");
    const layerLabel = perMapLabels?.[layerField] ?? i18n.layer;
    legend.textContent = layerLabel;
    bar.append(legend);
    const options = document.createElement("div");
    options.className = "osm-map-filter-options";
    bar.append(options);

    if (allLayers.length > 10) {
      // ── Select + explicit clear button ──────────────────────
      const labelEl = document.createElement("label");
      labelEl.className = "osm-map-layer-label";
      const labelText = document.createElement("span");
      labelText.className = "osm-sr-only";
      labelText.textContent = layerLabel;
      labelEl.append(labelText);

      const select = document.createElement("select");
      select.className = "osm-map-layer-select";
      select.setAttribute("aria-label", layerLabel);

      const defaultOpt = document.createElement("option");
      defaultOpt.value = "";
      defaultOpt.textContent = i18n.format("allLayers", { n: allLayers.length });
      select.appendChild(defaultOpt);
      for (const layer of allLayers) {
        const opt = document.createElement("option");
        opt.value = layer;
        opt.textContent = layer;
        select.appendChild(opt);
      }

      const clearBtn = document.createElement("button");
      clearBtn.type = "button";
      clearBtn.className = "osm-map-layer-clear";
      clearBtn.textContent = "✕";
      clearBtn.title = i18n.clearFilter;
      clearBtn.setAttribute("aria-label", i18n.clearFilter);
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
      options.appendChild(labelEl);
      options.appendChild(clearBtn);

      filterCtx.onApply(() => {
        // Ignore the selected layer when calculating available alternatives.
        const tagFiltered = new Set(
          markers
            .filter((m) => filterCtx.matches(m, "layer"))
            .map((m) => m._osmLayer)
            .filter(Boolean),
        );
        select.querySelectorAll("option").forEach((opt) => {
          if (!opt.value) return;
          opt.disabled = opt.value !== state.layer && !tagFiltered.has(opt.value);
        });
        select.value = state.layer || "";
        syncClearBtn();
      });

    } else {
      // ── Chips (≤ 10 layers) ──────────────────────────────────
      function setLayer(layer) {
        state.layer = layer;
        apply();
      }

      for (const layer of allLayers) {
        const chip = document.createElement("button");
        chip.type = "button";
        chip.className = "osm-map-layer-chip";
        chip.dataset.layer = layer;
        chip.textContent = layer;
        chip.addEventListener("click", (e) => {
          e.preventDefault();
          setLayer(state.layer === layer ? null : layer);
        });
        options.appendChild(chip);
      }

      filterCtx.onApply(() => {
        const visibleLayers = new Set(markers.filter((m) => filterCtx.matches(m, "layer")).map((m) => m._osmLayer));
        bar.querySelectorAll(".osm-map-layer-chip").forEach((chip) => {
          const layer = chip.dataset.layer;
          const active = state.layer === layer;
          chip.classList.toggle("osm-map-layer-chip--active", active);
          chip.setAttribute("aria-pressed", String(active));
          chip.disabled = !active && !visibleLayers.has(layer);
        });
      });
    }

    panel.prepend(bar);
  }

  // ── Map init ──────────────────────────────────────────────────
  async function initMap(el) {
    const i18n = contextFor(el);
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

    const map = L.map(el.id, { zoomControl: false });
    L.control.zoom({ zoomInTitle: i18n.zoomIn, zoomOutTitle: i18n.zoomOut }).addTo(map);
    el._leaflet_map = map;
    if ("ResizeObserver" in window) {
      new ResizeObserver(() => map.invalidateSize()).observe(el);
    }
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
          i18n,
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
          ? i18n.loadError
          : i18n.noPlaces;
      el.appendChild(msg);
      return;
    }

    // Add cluster group to map
    if (clusterGroup) {
      map.addLayer(clusterGroup);
    }

    // Setup fullscreen button
    setupFullscreenButton(el, el, i18n);

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
    setupResetButton(el, map, initialView, i18n);

    // Shared filter state — both tag and layer filters use AND logic
    const filterCtx = makeFilterState(map, markers, clusterGroup, initialView);

    // Tag filtering on map
    const explorer = setupMapExplorer(el, filterCtx, i18n);
    setupMapTagFilter(explorer.panel, filterCtx, i18n);

    // Layer filtering (x-osm-map-layer field)
    setupMapLayerFilter(explorer.panel, filterCtx, i18n, perMapLabels, layerField);
    explorer.toggle.hidden = !explorer.panel.children.length;
    explorer.panel.hidden ||= !explorer.panel.children.length;
    filterCtx.apply();
    map.invalidateSize();

    // Scroll popup to top on open so name/info is visible before photos
    map.on("popupopen", (e) => {
      requestAnimationFrame(() => {
        const popupEl = e.popup.getElement();
        if (!popupEl) return;
        const close = popupEl.querySelector(".leaflet-popup-close-button");
        if (close) { close.title = i18n.popupClose; close.setAttribute("aria-label", i18n.popupClose); }
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
          m._osmSourceName === hash ||
          (slugFromAnchor && m._osmPlaceSlug === slugFromAnchor),
      );
      if (!target) return;
      // Restore visibility before opening a place reached through a deep link.
      if (!filterCtx.matches(target)) {
        Object.assign(filterCtx.state, { query: "", tag: null, layer: null });
        el.closest(".osm-map-block").querySelector("input[type=search]").value = "";
        filterCtx.apply();
      }
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
    let currentI18n;
    let previousFocus;
    let previousOverflow;

    const lightboxHtml = `
      <div id="osm-photo-lightbox" class="osm-lightbox" role="dialog" aria-modal="true" tabindex="-1">
        <div class="osm-lightbox-overlay"></div>
        <div class="osm-lightbox-container" tabindex="-1">
          <button type="button" class="osm-lightbox-close">&times;</button>
          <button type="button" class="osm-lightbox-prev">&lsaquo;</button>
          <img class="osm-lightbox-image" src="" alt="">
          <button type="button" class="osm-lightbox-next">&rsaquo;</button>
          <div class="osm-lightbox-info" role="status"></div>
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

    function showLightbox(idx, images, origin) {
      if (!lightbox.classList.contains("osm-lightbox--active")) {
        previousFocus = document.activeElement;
        previousOverflow = document.body.style.overflow;
      }
      currentI18n = contextFor(origin);
      lightbox.setAttribute("aria-label", currentI18n.photo);
      lightbox.lang = currentI18n.locale;
      lightbox.dir = currentI18n.dir;
      lightboxImg.alt = currentI18n.photo;
      for (const [button, key] of [[closeBtn, "close"], [prevBtn, "previous"], [nextBtn, "next"]]) {
        button.title = currentI18n[key]; button.setAttribute("aria-label", currentI18n[key]);
      }
      currentIdx = idx;
      currentImages = images;
      prevBtn.disabled = idx === 0;
      nextBtn.disabled = idx === images.length - 1;
      lightboxImg.src = "";
      lightboxImg.src = images[idx];
      lightboxInfo.textContent = currentI18n.format("photoCount", { shown: idx + 1, total: images.length });

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
      closeBtn.focus();
    }

    function hideLightbox() {
      lightbox.classList.remove("osm-lightbox--active");
      document.body.style.overflow = previousOverflow || "";
      // Return lightbox to body and reset inline styles for next use.
      if (lightbox.parentElement !== document.body) {
        document.body.appendChild(lightbox);
      }
      lightbox.style.position = "";
      lightbox.style.zIndex = "";
      previousFocus?.focus();
    }

    function goToImage(idx) {
      if (idx < 0 || idx >= currentImages.length) return;
      currentIdx = idx;
      lightboxImg.src = currentImages[idx];
      prevBtn.disabled = idx === 0;
      nextBtn.disabled = idx === currentImages.length - 1;
      if (document.activeElement?.disabled) closeBtn.focus();
      lightboxInfo.textContent = currentI18n.format("photoCount", { shown: idx + 1, total: currentImages.length });
    }

    closeBtn.addEventListener("click", hideLightbox);
    overlay.addEventListener("click", hideLightbox);
    container.addEventListener("click", hideLightbox);
    lightboxImg.addEventListener("click", (e) => e.stopPropagation());

    prevBtn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      goToImage(currentIdx - 1);
    });

    nextBtn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      goToImage(currentIdx + 1);
    });

    document.addEventListener(
      "keydown",
      (e) => {
        if (!lightbox.classList.contains("osm-lightbox--active")) return;
        if (e.key === "Tab") {
          const buttons = [closeBtn, prevBtn, nextBtn].filter((button) => !button.disabled);
          const index = buttons.indexOf(document.activeElement);
          const next = (index + (e.shiftKey ? -1 : 1) + buttons.length) % buttons.length;
          e.preventDefault();
          buttons[next].focus();
        }
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
        const photoButton = e.target.closest(".osm-popup-photo-button");
        const img = photoButton?.querySelector(".osm-popup-photo") || e.target.closest(".osm-popup-photo");
        if (img) {
          e.preventDefault();
          e.stopPropagation();

          const src = img.getAttribute("data-fullsrc");

          const gallery = img.closest(".osm-popup-gallery");
          const images = Array.from(
            gallery.querySelectorAll(".osm-popup-photo"),
          ).map((el) => el.getAttribute("data-fullsrc"));

          showLightbox(images.indexOf(src), images, img);
        }
      },
      true,
    ); // Use capture phase

    return showLightbox;
  }

  // Table behavior is provided by pelican-tabular; OSM owns image lightboxes.
  function initSortableTables(showLightbox) {
    function attachTables() {
      if (!window.Tabular) return;
      document.querySelectorAll(".osm-place-list").forEach((table) => {
        const i18n = contextFor(table);
        const root = table.closest(".osm-place-list-wrapper");
        if (!root?.classList.contains("osm-explorer-list")) {
          if (i18n.placeCount) window.Tabular.initTable(table, { formatCount: i18n.formatCount });
          return;
        }
        const controller = window.Tabular.initTable(table, { formatCount: i18n.formatCount });
        if (!controller || root.querySelector(".osm-explorer-controls")) return;
        const tools = searchControls(root, i18n,
          (query) => {
            controller.getState().q = query;
            controller.update();
          },
          () => {
            const state = controller.getState();
            state.q = "";
            state.filters.tags = [];
            root.querySelector(".osm-tag-filter-chip")?.remove();
            controller.update();
          });
        const count = root.querySelector(".osm-place-list-count");
        const empty = document.createElement("p");
        empty.className = "osm-list-no-results";
        empty.textContent = i18n.noResults;
        empty.hidden = true;
        root.append(empty);
        function sync() {
          const state = controller.getState();
          tools.clear.disabled = !state.q && !state.filters.tags?.length;
          empty.hidden = [...table.querySelectorAll(".osm-place-row, .osm-group-header")].some((row) => !row.hidden);
        }
        if (count) {
          count.setAttribute("role", "status");
          new MutationObserver(sync).observe(count, { childList: true });
        }
        sync();
      });
    }
    attachTables();
    window.addEventListener("tabular:ready", attachTables);
    document.querySelectorAll(".osm-place-list").forEach((table) => {
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
      const iconCell = e.target.closest(".osm-list-photo-button, td.osm-list-image-icon");
      if (!iconCell) return;
      const row = iconCell.closest("tr.osm-has-images");
      if (!row) return;
      e.stopPropagation();

      const slug = row.dataset.osmPlaceSlug;
      const images = slug ? getImagesBySlug()[slug] : null;
      if (images && images.length) showLightbox(0, images, table);
    });
  }

  function initAllMaps() {
    const showLightbox = setupPhotoLightbox();
    initSortableTables(showLightbox);

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
