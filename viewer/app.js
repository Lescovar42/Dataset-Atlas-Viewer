/* Georeferenced map coordinates are meters relative to the catalog origin. */
let catalog;
let map;
let imageLayer;
let labelLayer;
let boundaryLayer;
let selectedTile = null;
let marker;
let view = "overlay";
let comparison = 0.5;
let inspectorRequest;
const $ = (selector) => document.querySelector(selector);
const percentage = (value) => `${value.toFixed(1)}%`;

function toMap(east, north) {
  return L.latLng(north - catalog.origin[1], east - catalog.origin[0]);
}

function boundsFor(bounds) {
  return L.latLngBounds(
    toMap(bounds[0], bounds[1]),
    toMap(bounds[2], bounds[3]),
  );
}

function showError(message) {
  $("#error-toast").textContent = message;
  $("#error-toast").hidden = false;
}

function updateLoading() {
  $("#map-loading").hidden = ![imageLayer, labelLayer].some(
    (layer) => layer && map.hasLayer(layer) && layer.isLoading(),
  );
}

function newLayer(kind, pane) {
  const mode = $("#band-mode").value;
  const enhance = $("#enhance").checked ? 1 : 0;
  const layer = L.tileLayer(
    `/tiles/${kind}/{z}/{x}/{y}.png?mode=${mode}&enhance=${enhance}`,
    {
      pane,
      bounds: boundsFor(catalog.bounds),
      minZoom: -7,
      maxZoom: 2,
      tileSize: 256,
      noWrap: true,
      keepBuffer: 1,
      updateWhenIdle: true,
    },
  );
  layer.on("loading load", updateLoading);
  layer.on("tileerror", () =>
    showError("A map tile could not load. Refresh the page to retry."),
  );
  return layer;
}

function updateCompare() {
  if (!map) return;
  const { x: width, y: height } = map.getSize();
  const position =
    L.DomUtil.getPosition(map.getPane("mapPane")) || L.point(0, 0);
  const pane = map.getPane("labels");
  pane.style.clipPath =
    view === "compare"
      ? `polygon(${width * comparison - position.x}px ${-position.y}px, ${width - position.x}px ${-position.y}px, ${width - position.x}px ${height - position.y}px, ${width * comparison - position.x}px ${height - position.y}px)`
      : "";
  $("#compare-divider").style.left = `${comparison * 100}%`;
  $(".compare-handle").setAttribute(
    "aria-valuenow",
    Math.round(comparison * 100),
  );
}

function refreshLayers() {
  if (imageLayer) map.removeLayer(imageLayer);
  if (labelLayer) map.removeLayer(labelLayer);
  imageLayer = newLayer("image", "imagery");
  labelLayer = newLayer(view === "overlay" ? "overlay" : "label", "labels");
  if (view !== "label") imageLayer.addTo(map);
  if (view !== "image") labelLayer.addTo(map);
  labelLayer.setOpacity(
    view === "overlay" ? Number($("#opacity").value) / 100 : 1,
  );
  $("#opacity").disabled = view !== "overlay";
  $("#band-mode").disabled = view === "label";
  $("#enhance").disabled = view === "label" || $("#band-mode").value === "ndvi";
  $("#compare-divider").hidden = view !== "compare";
  $("#compare-caption").hidden = view !== "compare";
  const ndvi = view === "image" && $("#band-mode").value === "ndvi";
  $("#map-legend").hidden = view === "image" && !ndvi;
  $(".legend-classes").hidden = ndvi;
  $(".ndvi-legend").hidden = !ndvi;
  $("#legend-title").textContent = ndvi
    ? "NDVI · IMAGE VALUES"
    : "REFERENCE LABELS";
  updateCompare();
  updateLoading();
}

function selectTile(tile) {
  selectedTile = tile;
  document.querySelectorAll(".tile-card").forEach((card) => {
    const active = card.dataset.id === tile?.id;
    card.classList.toggle("active", active);
    card.setAttribute("aria-pressed", String(active));
  });
  $("#map-title").textContent = tile
    ? `Tile ${tile.id}`
    : `All ${catalog.tiles.length} tiles`;
  $("#map-subtitle").textContent = tile
    ? `16 km² · ${percentage(tile.impervious_percent)} impervious`
    : `${Math.round(catalog.tiles.length * 16)} km² · 0.5 m imagery`;
  map.fitBounds(boundsFor(tile ? tile.bounds : catalog.bounds), {
    padding: [45, 45],
    animate: false,
  });
}

function updateScale() {
  const metersPerPixel = 1 / map.getZoomScale(map.getZoom(), 0);
  const maximum = metersPerPixel * 80;
  const magnitude = 10 ** Math.floor(Math.log10(maximum));
  const value = [1, 2, 5, 10]
    .map((v) => v * magnitude)
    .filter((v) => v <= maximum)
    .pop();
  $("#scale-text").textContent =
    value >= 1000 ? `${value / 1000} km` : `${value} m`;
  $("#scale-bar").style.width = `${value / metersPerPixel}px`;
}

async function inspect(event) {
  if (inspectorRequest) inspectorRequest.abort();
  inspectorRequest = new AbortController();
  const east = event.latlng.lng + catalog.origin[0];
  const north = event.latlng.lat + catalog.origin[1];
  if (marker) map.removeLayer(marker);
  marker = L.circleMarker(event.latlng, {
    radius: 6,
    color: "white",
    weight: 2,
    fillColor: "#143c2b",
    fillOpacity: 1,
  }).addTo(map);
  $("#inspector-content").innerHTML = '<p class="muted">Reading pixel…</p>';
  try {
    const response = await fetch(`/api/inspect?x=${east}&y=${north}`, {
      signal: inspectorRequest.signal,
    });
    if (!response.ok) throw new Error("Pixel request failed.");
    const pixel = await response.json();
    if (!pixel.covered) {
      $("#inspector-content").innerHTML =
        '<p class="muted">Outside the image coverage. Select a point inside one of the five tiles.</p>';
      return;
    }
    const label =
      pixel.label === 1
        ? "Impervious"
        : pixel.label === 0
          ? "Non-impervious"
          : "No label";
    const names = ["Red", "Green", "Blue", "NIR"];
    $("#inspector-content").innerHTML = `
      <div class="pixel-class ${pixel.label === 0 ? "pervious-label" : ""}"><strong>${label}</strong><span>${pixel.label ?? "—"}</span></div>
      <div class="pixel-meta">Tile <strong>${pixel.tile}</strong> · row ${pixel.row}, col ${pixel.col}<br>${pixel.latitude.toFixed(6)}°, ${pixel.longitude.toFixed(6)}°</div>
      <div class="band-values">${names.map((name, i) => `<div><span>${name}</span><b>${pixel.bands[i]}</b></div>`).join("")}</div>
      <div class="pixel-meta">NDVI <strong>${pixel.ndvi === null ? "undefined" : pixel.ndvi.toFixed(3)}</strong><br>E ${pixel.east.toFixed(1)} m · N ${pixel.north.toFixed(1)} m</div>`;
  } catch (error) {
    if (error.name !== "AbortError")
      $("#inspector-content").innerHTML =
        '<p class="muted">Could not read this pixel. Click to retry.</p>';
  }
}

async function initialize() {
  try {
    const response = await fetch("/api/catalog");
    if (!response.ok) throw new Error("Could not open the dataset catalog.");
    catalog = await response.json();
    const impervious = catalog.tiles.reduce(
      (sum, tile) => sum + tile.impervious_pixels,
      0,
    );
    const valid = catalog.tiles.reduce(
      (sum, tile) => sum + tile.valid_pixels,
      0,
    );
    $("#area-total").textContent = `${valid / 1e6} km²`;
    $("#impervious-total").textContent = percentage((impervious / valid) * 100);
    $("#tile-count").textContent = catalog.tiles.length;
    $("#tile-list").innerHTML = catalog.tiles
      .map(
        (tile) =>
          `<button class="tile-card" data-id="${tile.id}" aria-pressed="false" title="Zoom to tile ${tile.id}"><img src="/api/thumbnail/${tile.id}.png" alt="Aerial preview of tile ${tile.id}"><span><strong>${tile.id}</strong><span class="tile-caption">${percentage(tile.impervious_percent)} impervious</span></span></button>`,
      )
      .join("");

    map = L.map("map", {
      crs: L.CRS.Simple,
      minZoom: -7,
      maxZoom: 2,
      zoomControl: false,
      attributionControl: true,
      zoomSnap: 0.25,
      zoomDelta: 1,
      wheelPxPerZoomLevel: 90,
    });
    map.attributionControl.setPrefix("");
    map.attributionControl.addAttribution("MassGIS · USGS · April 2005");
    map.createPane("imagery").style.zIndex = 200;
    map.createPane("labels").style.zIndex = 300;
    map.getPane("labels").style.pointerEvents = "none";
    // Clip the fixed map pane, while Leaflet moves each tile layer inside it.
    boundaryLayer = L.layerGroup().addTo(map);
    for (const tile of catalog.tiles) {
      L.rectangle(boundsFor(tile.bounds), {
        color: "#fff",
        weight: 1,
        opacity: 0.65,
        fill: false,
        dashArray: "5 6",
        interactive: false,
      })
        .bindTooltip(tile.id, {
          permanent: true,
          direction: "center",
          className: "tile-name",
        })
        .addTo(boundaryLayer);
    }
    selectTile(null);
    refreshLayers();
    updateScale();
    map.on("zoomend moveend resize", () => {
      updateScale();
      updateCompare();
    });
    map.on("move zoom", updateCompare);
    map.on("mousemove", (event) => {
      $("#coordinates").textContent =
        `E ${(event.latlng.lng + catalog.origin[0]).toFixed(0)} · N ${(event.latlng.lat + catalog.origin[1]).toFixed(0)}`;
    });
    map.on("click", inspect);
    $(".tile-list").addEventListener("click", (event) => {
      const card = event.target.closest(".tile-card");
      if (card)
        selectTile(catalog.tiles.find((tile) => tile.id === card.dataset.id));
    });
    $("#fit-all").onclick = () => selectTile(null);
    $("#fit-map").onclick = () => selectTile(selectedTile);
    $("#zoom-in").onclick = () => map.zoomIn();
    $("#zoom-out").onclick = () => map.zoomOut();
    document.querySelectorAll("[data-view]").forEach(
      (button) =>
        (button.onclick = () => {
          view = button.dataset.view;
          document.querySelectorAll("[data-view]").forEach((item) => {
            item.classList.toggle("active", item === button);
            item.setAttribute("aria-pressed", String(item === button));
          });
          refreshLayers();
        }),
    );
    $("#band-mode").onchange = refreshLayers;
    $("#enhance").onchange = refreshLayers;
    $("#opacity").oninput = () => {
      const value = Number($("#opacity").value);
      $("#opacity-value").textContent = `${value}%`;
      labelLayer.setOpacity(value / 100);
    };
    $("#boundaries").onchange = () => {
      if ($("#boundaries").checked) boundaryLayer.addTo(map);
      else map.removeLayer(boundaryLayer);
    };
    const handle = $(".compare-handle");
    handle.setAttribute("role", "slider");
    handle.setAttribute("tabindex", "0");
    handle.setAttribute("aria-label", "Imagery and labels comparison position");
    handle.setAttribute("aria-valuemin", "3");
    handle.setAttribute("aria-valuemax", "97");
    handle.onkeydown = (event) => {
      if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
      event.preventDefault();
      comparison = Math.max(
        0.03,
        Math.min(
          0.97,
          comparison + (event.key === "ArrowRight" ? 0.05 : -0.05),
        ),
      );
      updateCompare();
    };
    handle.onpointerdown = (event) => {
      event.preventDefault();
      event.stopPropagation();
      handle.setPointerCapture(event.pointerId);
    };
    handle.onpointermove = (event) => {
      if (!handle.hasPointerCapture(event.pointerId)) return;
      const rect = $("#map-wrap").getBoundingClientRect();
      comparison = Math.max(
        0.03,
        Math.min(0.97, (event.clientX - rect.left) / rect.width),
      );
      updateCompare();
    };
    handle.onpointerup = (event) =>
      handle.releasePointerCapture(event.pointerId);
    $("#startup").hidden = true;
  } catch (error) {
    $("#startup").innerHTML =
      `<p>Could not open the viewer.</p><p>${error.message}</p><button onclick="location.reload()">Try again</button>`;
  }
}

initialize();
