/* ────────────────────────────────────────────────────────────────────────────
   user_map.js  –  Bangkok Precision Routing  |  User-facing Leaflet map
   Polls /api/status, lets user click-to-set Start/End, runs A*, draws route.
──────────────────────────────────────────────────────────────────────────── */

"use strict";

// ── Constants ────────────────────────────────────────────────────────────────
const MODE_COLORS = {
  walk:     "#3b82f6",
  drive:    "#f97316",
  rail:     "#a855f7",
  transfer: "#14b8a6",
};
const BANGKOK_CENTER = [13.75, 100.52];

// ── Map init ─────────────────────────────────────────────────────────────────
const map = L.map("map", { zoomControl: true, attributionControl: true, preferCanvas: true })
  .setView(BANGKOK_CENTER, 13);

L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  attribution: "© OpenStreetMap contributors",
  maxZoom: 19,
}).addTo(map);

// ── State ────────────────────────────────────────────────────────────────────
let startMarker = null;
let endMarker   = null;
let routeLayers = [];        // all drawn polylines
let clickPhase  = "start";   // "start" | "end"
let graphReady  = false;
let selectedMode = "multimodal";

// ── DOM refs ─────────────────────────────────────────────────────────────────
const statusDot      = document.getElementById("statusDot");
const statusText     = document.getElementById("statusText");
const btnRoute       = document.getElementById("btnRoute");
const btnRouteLabel  = document.getElementById("btnRouteLabel");
const btnClear       = document.getElementById("btnClear");
const clickTarget    = document.getElementById("clickTarget");
const resultSection  = document.getElementById("resultSection");
const resDist        = document.getElementById("resDist");
const resTime        = document.getElementById("resTime");
const resPoints      = document.getElementById("resPoints");
const segmentsList   = document.getElementById("segmentsList");
const toast          = document.getElementById("toast");
const startLatEl     = document.getElementById("startLat");
const startLngEl     = document.getElementById("startLng");
const endLatEl       = document.getElementById("endLat");
const endLngEl       = document.getElementById("endLng");

// ── Toast ────────────────────────────────────────────────────────────────────
let toastTimer = null;
function showToast(msg, type = "info", ms = 3500) {
  toast.textContent = msg;
  toast.className = `show ${type}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toast.className = ""; }, ms);
}

// ── Status polling ───────────────────────────────────────────────────────────
function pollStatus() {
  fetch("/api/status")
    .then(r => r.json())
    .then(d => {
      if (d.ready) {
        graphReady = true;
        statusDot.className  = "dot ready";
        statusText.textContent = `✅ Graph ready (${d.nodes.toLocaleString()} stations)`;
        drawStations();
        btnRoute.disabled = false;
        showToast("Graph loaded — ready to route!", "success");
      } else if (d.error) {
        statusDot.className  = "dot error";
        statusText.textContent = "❌ " + d.error;
      } else {
        statusText.textContent = "⏳ Building graph… please wait (~1-3 min first run)";
        setTimeout(pollStatus, 3000);
      }
    })
    .catch(() => {
      statusText.textContent = "⚠ Server unreachable, retrying…";
      setTimeout(pollStatus, 5000);
    });
}
pollStatus();

// ── Marker icons ─────────────────────────────────────────────────────────────
function makeIcon(color, label) {
  return L.divIcon({
    className: "",
    iconAnchor: [16, 32],
    popupAnchor: [0, -34],
    html: `<div style="
      width:32px;height:32px;border-radius:50% 50% 50% 0;
      background:${color};border:2px solid white;
      transform:rotate(-45deg);
      box-shadow:0 2px 8px rgba(0,0,0,.5);
      display:flex;align-items:center;justify-content:center;
    "><span style="transform:rotate(45deg);color:#fff;font-size:13px;font-weight:700;">${label}</span></div>`,
  });
}
const ICON_START = makeIcon("#3b82f6", "S");
const ICON_END   = makeIcon("#ef4444", "E");

// ── Click-to-set coordinates ─────────────────────────────────────────────────
map.on("click", e => {
  const { lat, lng } = e.latlng;
  if (clickPhase === "start") {
    if (startMarker) startMarker.remove();
    startMarker = L.marker([lat, lng], { icon: ICON_START }).addTo(map)
      .bindPopup(`<b>Start</b><br>${lat.toFixed(5)}, ${lng.toFixed(5)}`);
    startLatEl.value = lat.toFixed(6);
    startLngEl.value = lng.toFixed(6);
    clickPhase = "end";
    clickTarget.textContent = "End";
    showToast("Start set. Click map to set End point.", "info");
  } else {
    if (endMarker) endMarker.remove();
    endMarker = L.marker([lat, lng], { icon: ICON_END }).addTo(map)
      .bindPopup(`<b>End</b><br>${lat.toFixed(5)}, ${lng.toFixed(5)}`);
    endLatEl.value = lat.toFixed(6);
    endLngEl.value = lng.toFixed(6);
    clickPhase = "start";
    clickTarget.textContent = "Start";
    showToast("End set. Click 'Find Route' or click again to reset Start.", "info");
  }
});

// ── Mode pills ───────────────────────────────────────────────────────────────
document.querySelectorAll(".mode-pill").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".mode-pill").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    selectedMode = btn.dataset.mode;
  });
});

// ── Route request ─────────────────────────────────────────────────────────────
btnRoute.addEventListener("click", () => {
  const startLat   = parseFloat(startLatEl.value);
  const startLng   = parseFloat(startLngEl.value);
  const endLat     = parseFloat(endLatEl.value);
  const endLng     = parseFloat(endLngEl.value);
  if ([startLat, startLng, endLat, endLng].some(isNaN)) {
    showToast("Please set both Start and End coordinates.", "error");
    return;
  }
  findRoute(startLat, startLng, endLat, endLng);
});

function findRoute(sLat, sLng, eLat, eLng) {
  clearRoute();
  btnRoute.disabled = true;
  btnRouteLabel.innerHTML = '<span class="spinner"></span> Routing…';

  fetch("/api/route", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      start_lat: sLat, start_lng: sLng,
      end_lat:   eLat, end_lng:   eLng,
      mode:      selectedMode,
    }),
  })
    .then(async r => {
        const data = await r.json();
        if (!r.ok) throw new Error(data.error || "Unknown server error");
        return data;
    })
    .then(data => {
      btnRoute.disabled = !graphReady;
      btnRouteLabel.textContent = "Find Route";
      
      drawRoute(data.coords, data.segments);
      showStats(data, data.segments);
      showToast("Route found!", "success");
    })
    .catch(err => {
      btnRoute.disabled = !graphReady;
      btnRouteLabel.textContent = "Find Route";
      showToast("Routing failed: " + err.message, "error");
    });
}

// ── Draw route ───────────────────────────────────────────────────────────────
function drawRoute(coords, segments) {
  if (!coords || coords.length < 2) return;

  if (segments && segments.length > 0) {
    // Draw per-segment coloured polylines
    let segStart = 0;
    segments.forEach(seg => {
      const segEnd = findSegmentEnd(coords, seg, segStart);
      const slice = coords.slice(segStart, segEnd + 1);
      if (slice.length >= 2) {
        const line = L.polyline(slice, {
          color: MODE_COLORS[seg.mode] || "#3b82f6",
          weight: seg.mode === "rail" ? 5 : 4,
          opacity: 0.9,
          lineJoin: "round",
          lineCap: "round",
          dashArray: (seg.mode === "transfer" || seg.mode === "walk") ? "6,5" : null,
        }).addTo(map);
        line.bindTooltip(
          `${seg.mode.toUpperCase()} — ${(seg.distance_m / 1000).toFixed(2)} km` +
          (seg.name ? `<br>${seg.name}` : ""),
          { sticky: true }
        );
        routeLayers.push(line);
      }
      segStart = Math.max(segStart, segEnd);
    });
  } else {
    // Fallback: single blue polyline
    const line = L.polyline(coords, { color: "#3b82f6", weight: 4, opacity: 0.9 }).addTo(map);
    routeLayers.push(line);
  }

  // Fit map to route
  const group = L.featureGroup(routeLayers);
  map.fitBounds(group.getBounds().pad(0.1));
}

/**
 * Estimate where a segment ends in the global coords array.
 * Segments are in order, so we just walk forward by distance.
 */
function findSegmentEnd(coords, seg, startIdx) {
  let accumulated = 0;
  const target = seg.distance_m;
  for (let i = startIdx; i < coords.length - 1; i++) {
    const d = haversine(coords[i], coords[i + 1]);
    accumulated += d;
    if (accumulated >= target * 0.98) return i + 1;
  }
  return coords.length - 1;
}

function haversine([lat1, lng1], [lat2, lng2]) {
  const R = 6371000;
  const dLat = (lat2 - lat1) * Math.PI / 180;
  const dLng = (lng2 - lng1) * Math.PI / 180;
  const a = Math.sin(dLat / 2) ** 2 +
    Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(a));
}

// ── Route stats panel ────────────────────────────────────────────────────────
function showStats(stats, segments) {
  const km = (stats.distance_m / 1000).toFixed(2);
  const mins = Math.ceil(stats.time_s / 60);
  resDist.textContent   = `${km} km`;
  resTime.textContent   = mins < 60 ? `${mins} min` : `${(mins/60).toFixed(1)} h`;
  resPoints.textContent = "";

  segmentsList.innerHTML = "";
  (segments || []).forEach(seg => {
    const km_s = (seg.distance_m / 1000).toFixed(2);
    const icon = { walk:"🚶", drive:"🚗", rail:"🚇", transfer:"🔀" }[seg.mode] || "•";
    const div = document.createElement("div");
    div.className = "segment-item";
    div.innerHTML = `
      <span class="seg-mode-badge seg-${seg.mode}">${icon} ${seg.mode}</span>
      <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:0.7rem;color:var(--text-muted);">
        ${seg.name || "—"}
      </span>
      <span style="white-space:nowrap;font-size:0.72rem;">${km_s} km</span>
    `;
    segmentsList.appendChild(div);
  });

  resultSection.style.display = "block";
}

let stationsLayer = L.layerGroup().addTo(map);

function drawStations() {
  fetch("/api/graph/stations")
    .then(r => r.json())
    .then(data => {
      stationsLayer.clearLayers();
      
      // Draw edges (train tracks)
      if (data.edges) {
        data.edges.forEach(e => {
          let color = e.mode === "transfer" ? "#888" : "#2196F3";
          let dash = e.mode === "transfer" ? "5,5" : "";
          L.polyline(e.coords, {color: color, weight: 3, opacity: 0.6, dashArray: dash}).addTo(stationsLayer);
        });
      }
      
      // Draw nodes (stations)
      if (data.stations) {
        data.stations.forEach(s => {
          if (s.lat && s.lng) {
            L.circleMarker([s.lat, s.lng], {
              radius: 6,
              fillColor: "#fff",
              color: "#000",
              weight: 2,
              fillOpacity: 1
            }).bindTooltip(s.name, {permanent: true, direction: 'right', className: 'station-tooltip'})
              .addTo(stationsLayer);
          }
        });
      }
    }).catch(console.error);
}

// ── Clear ────────────────────────────────────────────────────────────────────
function clearRoute() {
  routeLayers.forEach(l => l.remove());
  routeLayers = [];
  resultSection.style.display = "none";
}

btnClear.addEventListener("click", () => {
  clearRoute();
  if (startMarker) { startMarker.remove(); startMarker = null; }
  if (endMarker)   { endMarker.remove();   endMarker = null; }
  startLatEl.value = "13.7455";
  startLngEl.value = "100.5340";
  endLatEl.value   = "13.7312";
  endLngEl.value   = "100.5273";
  clickPhase = "start";
  clickTarget.textContent = "Start";
  showToast("Cleared.", "info");
});
