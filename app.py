"""
app.py  —  Flask backend for Bangkok A* Multi-Modal Routing
Run with: python app.py
"""

import logging
import threading
from flask import Flask, request, jsonify, render_template

from graph_builder import build_graph
from astar import astar_route

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

app = Flask(__name__)

# ─── Shared state ────────────────────────────────────────────────────────────
state = {
    "G": None,
    "ready": False,
    "error": None
}
_G_lock = threading.Lock()
_blocked: set = set()        # set of (u, v, key) tuples


def _load_graph_background():
    try:
        log.info("Loading super-graph in background …")
        G = build_graph()
        with _G_lock:
            state["G"] = G
            state["ready"] = True
        log.info("Graph ready: %d nodes, %d edges", G.number_of_nodes(), G.number_of_edges())
    except Exception as exc:
        state["error"] = str(exc)
        log.error("Graph load failed: %s", exc)


# Start loading immediately when the module is imported
threading.Thread(target=_load_graph_background, daemon=True).start()


# ─── Helper ──────────────────────────────────────────────────────────────────
def _require_graph():
    if state["error"]:
        return None, jsonify({"error": f"Graph load failed: {state['error']}"}), 500
    if not state["ready"] or state["G"] is None:
        return None, jsonify({"error": "Graph still loading. Please wait and try again."}), 503
    return state["G"], None, None


# ─── Pages ───────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/admin")
def admin():
    return render_template("admin.html")


# ─── Status ──────────────────────────────────────────────────────────────────
@app.route("/api/status")
def api_status():
    G = state["G"]
    return jsonify({
        "ready": state["ready"],
        "error": state["error"],
        "nodes": G.number_of_nodes() if G else 0,
        "edges": G.number_of_edges() if G else 0,
        "blocked_count": len(_blocked),
    })


# ─── Routing ─────────────────────────────────────────────────────────────────
@app.route("/api/route", methods=["POST"])
def api_route():
    G, err_resp, status = _require_graph()
    if err_resp:
        return err_resp, status

    data = request.get_json(force=True)
    try:
        start_lat = float(data["start_lat"])
        start_lng = float(data["start_lng"])
        end_lat   = float(data["end_lat"])
        end_lng   = float(data["end_lng"])
        mode      = data.get("mode", "multimodal")
    except (KeyError, TypeError, ValueError) as exc:
        return jsonify({"error": f"Bad request: {exc}"}), 400

    try:
        with _G_lock:
            result = astar_route(G, start_lat, start_lng, end_lat, end_lng,
                                 mode=mode, blocked=set(_blocked))
        return jsonify({
            "ok": True,
            "coords":     result["coords"],
            "segments":   result["segments"],
            "distance_m": result["distance_m"],
            "time_s":     result["time_s"],
        })
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception as exc:
        log.exception("Route error")
        return jsonify({"error": str(exc)}), 500


# ─── Admin – block / unblock ──────────────────────────────────────────────────
@app.route("/api/admin/block", methods=["POST"])
def api_block():
    data = request.get_json(force=True)
    try:
        u   = data["u"]
        v   = data["v"]
        key = int(data.get("key", 0))
    except (KeyError, TypeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400

    _blocked.add((u, v, key))
    # Also block the reverse direction if it exists
    G, _, _ = _require_graph()
    if G and G.has_edge(v, u):
        _blocked.add((v, u, key))

    log.info("Blocked edge (%s, %s, %d). Total blocked: %d", u, v, key, len(_blocked))
    return jsonify({"ok": True, "blocked_count": len(_blocked)})


@app.route("/api/admin/unblock", methods=["POST"])
def api_unblock():
    data = request.get_json(force=True)
    try:
        u   = data["u"]
        v   = data["v"]
        key = int(data.get("key", 0))
    except (KeyError, TypeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400

    _blocked.discard((u, v, key))
    _blocked.discard((v, u, key))
    return jsonify({"ok": True, "blocked_count": len(_blocked)})


@app.route("/api/admin/unblock_all", methods=["POST"])
def api_unblock_all():
    _blocked.clear()
    return jsonify({"ok": True})


@app.route("/api/admin/blocked", methods=["GET"])
def api_blocked_list():
    return jsonify({
        "blocked": [{"u": u, "v": v, "key": k} for u, v, k in _blocked]
    })


# ─── Admin – edge list for map rendering ─────────────────────────────────────
@app.route("/api/graph/edges")
def api_edges():
    """
    Return a sample of edges for the admin map.
    We stream only edges that have geometry so the frontend
    can draw them interactively. Paginated via ?page=N&per_page=M.
    """
    G, err_resp, status = _require_graph()
    if err_resp:
        return err_resp, status

    page     = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 500))
    mode_filter = request.args.get("mode", None)  # e.g. "rail"

    edges_out = []
    for u, v, k, data in G.edges(keys=True, data=True):
        if mode_filter and data.get("mode") != mode_filter:
            continue
        geom = data.get("geometry")
        if geom is None:
            u_d, v_d = G.nodes[u], G.nodes[v]
            if "y" not in u_d or "y" not in v_d:
                continue
            coords = [[u_d["y"], u_d["x"]], [v_d["y"], v_d["x"]]]
        else:
            coords = [[lat, lng] for lng, lat in geom.coords]

        edges_out.append({
            "u":     str(u),
            "v":     str(v),
            "key":   k,
            "mode":  data.get("mode", "walk"),
            "name":  str(data.get("name", "")),
            "coords": coords,
            "blocked": (str(u), str(v), k) in {(str(a), str(b), c) for a, b, c in _blocked},
        })

    start = (page - 1) * per_page
    chunk = edges_out[start: start + per_page]
    return jsonify({
        "total":    len(edges_out),
        "page":     page,
        "per_page": per_page,
        "edges":    chunk,
    })


@app.route("/api/graph/rail_edges")
def api_rail_edges():
    """Return only rail edges + transfer edges for overlay."""
    G, err_resp, status = _require_graph()
    if err_resp:
        return err_resp, status

    edges_out = []
    seen = set()
    for u, v, k, data in G.edges(keys=True, data=True):
        mode = data.get("mode", "")
        if mode not in ("rail", "transfer"):
            continue
        pair = (min(str(u), str(v)), max(str(u), str(v)))
        if pair in seen:
            continue
        seen.add(pair)
        geom = data.get("geometry")
        if geom is None:
            u_d, v_d = G.nodes[u], G.nodes[v]
            if "y" not in u_d or "y" not in v_d:
                continue
            coords = [[u_d["y"], u_d["x"]], [v_d["y"], v_d["x"]]]
        else:
            coords = [[lat, lng] for lng, lat in geom.coords]
        edges_out.append({
            "u":      str(u),
            "v":      str(v),
            "key":    k,
            "mode":   mode,
            "name":   str(data.get("name", "")),
            "coords": coords,
        })
    return jsonify({"edges": edges_out})


@app.route("/api/graph/stations")
def api_stations():
    G, err, status = _require_graph()
    if err: return err, status
    
    stations = []
    edges = []
    seen_edges = set()
    
    for n, data in G.nodes(data=True):
        if str(n).startswith("rail_"):
            stations.append({
                "id": str(n),
                "lat": data.get("y"),
                "lng": data.get("x"),
                "name": data.get("name", "")
            })
        
    for u, v, k, data in G.edges(keys=True, data=True):
        mode = data.get("mode", "rail")
        if mode not in ("rail", "transfer"):
            continue
        pair = tuple(sorted([str(u), str(v)]))
        if pair not in seen_edges:
            seen_edges.add(pair)
            geom = data.get("geometry")
            if geom:
                coords = [[lat, lng] for lng, lat in geom.coords]
                edges.append({"coords": coords, "mode": mode})
                
    return jsonify({"stations": stations, "edges": edges})

# ─── Run ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)
