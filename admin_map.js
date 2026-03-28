/* ────────────────────────────────────────────────────────────────────────────
   admin_map.js  –  Bangkok Precision Routing  |  Admin edge-blocking panel
   Loads OSM edges onto the map; click to block/unblock; sidebar list sync.
──────────────────────────────────────────────────────────────────────────── */

"use strict";

// ── Map init ──────────────────────────────────────────────────────────────────
const BANGKOK_CENTER = [13.75, 100.52];
const map = L.map("map", { zoomControl: true, preferCanvas: true }).setView(BANGKOK_CENTER, 13);

L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  attribution: "© OpenStreetMap contributors",
  maxZoom: 19,
}).addTo(map);

// ── Layer groups (toggle-able) ────────────────────────────────────────────────
const layers = {
  walk:     L.layerGroup().addTo(map),
  drive:    L.layerGroup().addTo(map),
  rail:     L.layerGroup().addTo(map),
  transfer: L.layerGroup().addTo(map),
};
const layerVisible = { walk: true, drive: true, rail: true, transfer: true };

// ── Colour palette ────────────────────────────────────────────────────────────
const MODE_COLORS = {
  walk:     "#3b82f6",
  drive:    "#f97316",
  rail:     "#a855f7",
  transfer: "#14b8a6",
};
const BLOCKED_COLOR = "#ef4444";

// ── State ─────────────────────────────────────────────────────────────────────
// Map from edgeId → { polyline, u, v, key, mode, name, blocked }
const edgeMap = {};
let blockedEdges = {};  // edgeId → { u, v, key }
let graphReady = false;

// ── DOM refs ──────────────────────────────────────────────────────────────────
const statusDot     = document.getElementById("statusDot");
const statusText    = document.getElementById("statusText");
const blockedCount  = document.getElementById("blockedCount");
const blockedList   = document.getElementById("blockedList");
const btnLoadEdges  = document.getElementById("btnLoadEdges");
const toast         = document.getElementById("toast");

// ── Toast ─────────────────────────────────────────────────────────────────────
let toastTimer = null;
function showToast(msg, type = "info", ms = 3500) {
  toast.textContent = msg;
  toast.className = `show ${type}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toast.className = ""; }, ms);
}

// ── Status polling ────────────────────────────────────────────────────────────
function pollStatus() {
  fetch("/api/status")
    .then(r => r.json())
    .then(d => {
      if (d.ready) {
        graphReady = true;
        statusDot.className = "dot ready";
        statusText.textContent = `✅ Graph ready — ${d.nodes.toLocaleString()} nodes`;
        btnLoadEdges.disabled = false;
        showToast("Graph loaded — click 'Load Road Edges' to begin.", "success");
        syncBlockedList();
        loadStations();
        loadRailEdges();
      } else if (d.error) {
        statusDot.className = "dot error";
        statusText.textContent = "❌ " + d.error;
      } else {
        statusText.textContent = "⏳ Building graph…";
        setTimeout(pollStatus, 3000);
      }
    })
    .catch(() => setTimeout(pollStatus, 5000));
}
pollStatus();

// ── Utility: edge ID string ───────────────────────────────────────────────────
function edgeId(u, v, key) { return `${u}::${v}::${key}`; }

// ── Fetch & draw road edges ───────────────────────────────────────────────────
btnLoadEdges.addEventListener("click", () => {
  if (!graphReady) { showToast("Graph not ready yet.", "error"); return; }
  btnLoadEdges.disabled = true;
  btnLoadEdges.innerHTML = '<span class="spinner"></span> Loading…';
  loadEdges(1);
});

function loadEdges(page) {
  fetch(`/api/graph/edges?page=${page}&per_page=5000`)
    .then(r => r.json())
    .then(data => {
      drawEdges(data.edges);
      if (data.page * data.per_page < data.total) {
        setTimeout(() => loadEdges(page + 1), 100);
      } else {
        btnLoadEdges.innerHTML = `✅ Loaded ${data.total.toLocaleString()} edges`;
        showToast("All edges loaded.", "success");
      }
    })
    .catch(err => {
      showToast("Failed to load edges: " + err.message, "error");
      btnLoadEdges.disabled = false;
      btnLoadEdges.textContent = "Load Road Edges";
    });
}

function drawEdges(edges) {
  edges.forEach(e => {
    if (!e.coords || e.coords.length < 2) return;
    const eid = edgeId(e.u, e.v, e.key);
    const mode = e.mode || "walk";
    const isRail = mode === "rail" || mode === "transfer";
    const color = e.blocked ? BLOCKED_COLOR : (MODE_COLORS[mode] || "#888");

    const line = L.polyline(e.coords, {
      color,
      weight: isRail ? 5 : 2.5,
      opacity: isRail ? 0.85 : 0.55,
      lineJoin: "round",
    });

    const targetLayer = layers[mode] || layers.walk;
    line.addTo(targetLayer);

    edgeMap[eid] = { polyline: line, u: e.u, v: e.v, key: e.key, mode, name: e.name, blocked: e.blocked };

    if (e.blocked) blockedEdges[eid] = { u: e.u, v: e.v, key: e.key };

    attachEdgeClick(eid, line, e);
  });
  renderBlockedList();
}

function loadRailEdges() {
  fetch("/api/graph/rail_edges")
    .then(r => r.json())
    .then(data => {
      data.edges.forEach(e => {
        if (!e.coords || e.coords.length < 2) return;
        const eid = edgeId(e.u, e.v, e.key);
        if (edgeMap[eid]) return;   // already drawn
        const color = MODE_COLORS[e.mode] || "#a855f7";
        const line = L.polyline(e.coords, { color, weight: 6, opacity: 0.9 });
        const targetLayer = layers[e.mode] || layers.rail;
        line.addTo(targetLayer);
        edgeMap[eid] = { polyline: line, u: e.u, v: e.v, key: e.key, mode: e.mode, name: e.name, blocked: false };
        attachEdgeClick(eid, line, e);
      });
    })
    .catch(() => {});
}

function loadStations() {
  fetch("/api/graph/stations")
    .then(r => r.json())
    .then(data => {
      data.stations.forEach(s => {
        if (!s.lat || !s.lng) return;
        const marker = L.circleMarker([s.lat, s.lng], {
          radius: 6,
          fillColor: "#fff",
          color: "#000",
          weight: 2,
          fillOpacity: 1
        }).bindTooltip(s.name || "Station", {permanent: true, direction: 'right', className: 'station-tooltip'});
        
        let popupContent = `<div style="font-family:'Inter',sans-serif;font-size:0.8rem;">`;
        if (s.name) popupContent += `<b>🚇 ${s.name}</b><br>`;
        popupContent += `<span style="color:#666">ID: ${s.id}</span></div>`;
        marker.bindPopup(popupContent);
        
        marker.addTo(layers.rail);
      });
    })
    .catch(err => console.error("Failed to load stations", err));
}

// ── Edge click popup ──────────────────────────────────────────────────────────
function attachEdgeClick(eid, line, e) {
  line.on("click", (ev) => {
    L.DomEvent.stopPropagation(ev);
    const state = edgeMap[eid];
    const alreadyBlocked = !!blockedEdges[eid];
    const shortId = `${String(e.u).slice(-6)}→${String(e.v).slice(-6)}`;
    const modeIcon = { walk:"🚶", drive:"🚗", rail:"🚇", transfer:"🔀" }[state.mode] || "•";

    const popup = L.popup({ maxWidth: 280 })
      .setLatLng(ev.latlng)
      .setContent(`
        <div style="font-family:'Inter',sans-serif;font-size:0.78rem;">
          <div style="font-weight:700;margin-bottom:6px;">${modeIcon} ${state.name || "Edge"}</div>
          <div style="color:#94a3b8;margin-bottom:8px;font-size:0.7rem;">ID: ${shortId}</div>
          <div style="margin-bottom:8px;">Mode: <b>${state.mode}</b></div>
          <button id="popup-btn-${eid.replace(/::/g, "_")}"
            style="
              width:100%;padding:7px;border:none;border-radius:6px;cursor:pointer;
              font-family:'Inter',sans-serif;font-size:0.78rem;font-weight:600;
              background:${alreadyBlocked ? "#10b981" : "#ef4444"};color:white;
            ">
            ${alreadyBlocked ? "✅ Unblock Edge" : "🚫 Block Edge"}
          </button>
        </div>
      `)
      .openOn(map);

    // Bind button after popup renders
    setTimeout(() => {
      const btn = document.getElementById(`popup-btn-${eid.replace(/::/g, "_")}`);
      if (!btn) return;
      btn.addEventListener("click", () => {
        map.closePopup(popup);
        if (edgeMap[eid].blocked) {
          unblockEdge(eid);
        } else {
          blockEdge(eid);
        }
      });
    }, 80);
  });

  // Hover highlight
  line.on("mouseover", () => {
    if (!edgeMap[eid].blocked) line.setStyle({ opacity: 1, weight: 4 });
  });
  line.on("mouseout", () => {
    if (!edgeMap[eid].blocked) {
      const isRail = edgeMap[eid].mode === "rail";
      line.setStyle({ opacity: isRail ? 0.85 : 0.55, weight: isRail ? 5 : 2.5 });
    }
  });
}

// ── Block / Unblock ───────────────────────────────────────────────────────────
function blockEdge(eid) {
  const e = edgeMap[eid];
  fetch("/api/admin/block", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ u: e.u, v: e.v, key: e.key }),
  })
    .then(r => r.json())
    .then(d => {
      if (d.ok) {
        e.blocked = true;
        blockedEdges[eid] = { u: e.u, v: e.v, key: e.key };
        e.polyline.setStyle({ color: BLOCKED_COLOR, opacity: 1, weight: 5 });
        blockedCount.textContent = d.blocked_count;
        renderBlockedList();
        showToast(`Edge blocked. Total: ${d.blocked_count}`, "error");
      }
    })
    .catch(err => showToast("Block failed: " + err.message, "error"));
}

function unblockEdge(eid) {
  const e = edgeMap[eid];
  fetch("/api/admin/unblock", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ u: e.u, v: e.v, key: e.key }),
  })
    .then(r => r.json())
    .then(d => {
      if (d.ok) {
        e.blocked = false;
        delete blockedEdges[eid];
        const origColor = MODE_COLORS[e.mode] || "#888";
        const isRail = e.mode === "rail" || e.mode === "transfer";
        e.polyline.setStyle({ color: origColor, opacity: isRail ? 0.85 : 0.55, weight: isRail ? 5 : 2.5 });
        blockedCount.textContent = d.blocked_count;
        renderBlockedList();
        showToast(`Edge unblocked. Total: ${d.blocked_count}`, "success");
      }
    })
    .catch(err => showToast("Unblock failed: " + err.message, "error"));
}

// ── Unblock All ───────────────────────────────────────────────────────────────
function unblockAll() {   // called from inline HTML onclick
  fetch("/api/admin/unblock_all", { method: "POST" })
    .then(r => r.json())
    .then(d => {
      if (!d.ok) return;
      Object.keys(blockedEdges).forEach(eid => {
        const e = edgeMap[eid];
        if (!e) return;
        e.blocked = false;
        const origColor = MODE_COLORS[e.mode] || "#888";
        const isRail = e.mode === "rail" || e.mode === "transfer";
        e.polyline.setStyle({ color: origColor, opacity: isRail ? 0.85 : 0.55, weight: isRail ? 5 : 2.5 });
      });
      blockedEdges = {};
      blockedCount.textContent = "0";
      renderBlockedList();
      showToast("All edges unblocked.", "success");
    });
}
window.unblockAll = unblockAll;  // expose for inline HTML

// ── Render sidebar blocked list (from current in-memory state) ────────────────
function renderBlockedList() {
  const ids = Object.keys(blockedEdges);
  blockedCount.textContent = ids.length;
  if (ids.length === 0) {
    blockedList.innerHTML = '<div style="color:var(--text-muted);font-size:0.73rem;text-align:center;padding:16px 0;">No blocked edges</div>';
    return;
  }
  blockedList.innerHTML = ids.map(eid => {
    const e = edgeMap[eid];
    const label = e ? (e.name || eid.slice(0, 20) + "…") : eid.slice(0, 20) + "…";
    const icon  = e ? ({ walk:"🚶",drive:"🚗",rail:"🚇",transfer:"🔀" }[e.mode] || "•") : "•";
    return `<div class="blocked-item">
      <span class="blocked-id">${icon} ${label}</span>
      <button onclick="unblockEdge('${eid}')" class="btn btn-ghost btn-sm" style="color:#10b981;border-color:#10b981;">✓</button>
    </div>`;
  }).join("");
}
window.unblockEdge = unblockEdge;  // expose for sidebar items

// ── Layer toggle ──────────────────────────────────────────────────────────────
function toggleLayer(mode) {    // called from inline HTML
  layerVisible[mode] = !layerVisible[mode];
  const btn = document.getElementById(`layer${mode.charAt(0).toUpperCase() + mode.slice(1)}`);
  if (layerVisible[mode]) {
    layers[mode].addTo(map);
    btn && btn.classList.add("active");
  } else {
    layers[mode].remove();
    btn && btn.classList.remove("active");
  }
}
window.toggleLayer = toggleLayer;  // expose for inline HTML

// ── Fetch + apply blocked list from server ───────────────────────────────────
function syncBlockedList() {
  fetch("/api/admin/blocked")
    .then(r => r.json())
    .then(d => {
      blockedEdges = {};
      (d.blocked || []).forEach(({ u, v, key }) => {
        const eid = edgeId(u, v, key);
        blockedEdges[eid] = { u, v, key };
        if (edgeMap[eid]) {
          edgeMap[eid].blocked = true;
          edgeMap[eid].polyline.setStyle({ color: BLOCKED_COLOR, opacity: 1, weight: 5 });
        }
      });
      renderBlockedList();
    });
}
