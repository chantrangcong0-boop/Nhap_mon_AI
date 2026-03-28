"""
graph_builder.py
Loads OpenStreetMap data for central Bangkok using OSMnx,
builds a combined super-graph with walk + drive + rail layers,
and connects them with virtual transfer edges at transit stations.
"""

import os
import time
import pickle
import logging
import networkx as nx
import osmnx as ox
from shapely.geometry import LineString

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# ── osmnx global settings ────────────────────────────────────────────────────
# Use local cache so retries don't re-download already-fetched data.
os.makedirs(os.path.join(os.path.dirname(__file__), "osmnx_cache"), exist_ok=True)
ox.settings.use_cache = True
ox.settings.cache_folder = os.path.join(os.path.dirname(__file__), "osmnx_cache")
ox.settings.requests_timeout = 15
ox.settings.overpass_rate_limit = False

# Fallback Overpass endpoints — tried in order if the primary fails
_OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]

# osmnx ≥ 2.0 changed graph_from_bbox signature and auto-computes edge lengths.
_OX_V2 = int(ox.__version__.split(".")[0]) >= 2


def _graph_from_bbox(north, south, east, west, network_type, retain_all, max_retries=4):
    """Wrapper for osmnx graph_from_bbox compatible with v1 and v2, with retries + endpoint fallback."""
    last_err = None
    endpoints = _OVERPASS_ENDPOINTS
    for attempt in range(max_retries):
        endpoint = endpoints[attempt % len(endpoints)]
        ox.settings.overpass_endpoint = endpoint
        log.info("OSM download attempt %d via %s …", attempt + 1, endpoint)
        try:
            if _OX_V2:
                return ox.graph_from_bbox(
                    bbox=(west, south, east, north),
                    network_type=network_type,
                    retain_all=retain_all,
                )
            else:
                return ox.graph_from_bbox(
                    north, south, east, west,
                    network_type=network_type,
                    retain_all=retain_all,
                )
        except Exception as exc:
            last_err = exc
            wait = 8 * (attempt + 1)
            log.warning("Attempt %d failed: %s — retry in %ds …", attempt + 1, exc, wait)
            time.sleep(wait)
    raise RuntimeError(f"OSM download failed after {max_retries} attempts") from last_err


def _add_edge_lengths(G):
    """Add edge lengths; in osmnx v2 they are computed automatically."""
    if not _OX_V2:
        G = ox.add_edge_lengths(G)
    return G


# ─── Central Bangkok bounding box ────────────────────────────────────────────
# Core area for fast testing (20x20km bbox for rail mapping)
BBOX = {
    "north": 13.850,
    "south": 13.650,
    "east":  100.650,
    "west":  100.450,
}


CACHE_PATH = os.path.join(os.path.dirname(__file__), "graph_cache.pkl")

# Speed lookup (m/s)
SPEED = {
    "walk":  1.2,   # ~4.3 km/h
    "drive": 8.33,  # ~30 km/h urban
    "rail":  13.89, # ~50 km/h BTS/MRT
}

# ─── Bangkok BTS / MRT station locations (lat, lng, name) ────────────────────
TRANSIT_STATIONS = [
    # BTS Sukhumvit Line
    (13.8021, 100.5531, "Mo Chit"),         # 0
    (13.7951, 100.5540, "Saphan Khwai"),    # 1
    (13.7882, 100.5527, "Ari"),             # 2
    (13.7839, 100.5497, "Sanam Pao"),       # 3
    (13.7785, 100.5469, "Victory Monument"),# 4
    (13.7744, 100.5404, "Phaya Thai"),      # 5
    (13.7670, 100.5383, "Ratchathewi"),     # 6
    (13.7455, 100.5340, "Siam BTS"),        # 7 (Interchange)
    (13.7449, 100.5494, "Chit Lom"),        # 8
    (13.7435, 100.5497, "Phloen Chit"),     # 9
    (13.7405, 100.5554, "Nana"),            # 10
    (13.7360, 100.5601, "Asok BTS"),        # 11 (Interchange MRT)
    (13.7316, 100.5677, "Phrom Phong"),     # 12
    (13.7257, 100.5760, "Thong Lo"),        # 13
    (13.7202, 100.5826, "Ekkamai"),         # 14
    (13.7144, 100.5904, "On Nut"),          # 15
    
    # BTS Silom Line
    (13.7465, 100.5293, "National Stadium"),# 16
    # Siam sits here, connected from 16 -> 7 -> 17
    (13.7397, 100.5393, "Ratchadamri"),     # 17
    (13.7312, 100.5273, "Sala Daeng BTS"),  # 18 (Interchange MRT)
    (13.7272, 100.5227, "Chong Nonsi"),     # 19
    (13.7195, 100.5162, "Surasak"),         # 20
    (13.7188, 100.5134, "Saphan Taksin"),   # 21
    
    # MRT Blue Line (Heading East/North)
    (13.7380, 100.5162, "Hua Lamphong"),    # 22
    (13.7327, 100.5284, "Sam Yan"),         # 23
    (13.7289, 100.5372, "Silom MRT"),       # 24 (Interchange BTS 18)
    (13.7254, 100.5457, "Lumphini"),        # 25
    (13.7226, 100.5539, "Khlong Toei"),     # 26
    (13.7227, 100.5604, "Queen Sirikit"),   # 27
    (13.7387, 100.5614, "Sukhumvit MRT"),   # 28 (Interchange BTS 11)
    (13.7490, 100.5630, "Phetchaburi MRT"), # 29
    (13.7578, 100.5654, "Phra Ram 9"),      # 30
]

# Accurate sequence linking
RAIL_CONNECTIONS = [
    # Sukhumvit
    (0,1),(1,2),(2,3),(3,4),(4,5),(5,6),(6,7),(7,8),(8,9),(9,10),(10,11),(11,12),(12,13),(13,14),(14,15),
    # Silom
    (16,7),(7,17),(17,18),(18,19),(19,20),(20,21),
    # MRT
    (22,23),(23,24),(24,25),(25,26),(26,27),(27,28),(28,29),(29,30),
]


def _assign_edge_attrs(G, mode: str):
    """Add 'mode' and 'travel_time' attributes to every edge in G."""
    speed = SPEED[mode]
    for u, v, k, data in G.edges(keys=True, data=True):
        length = data.get("length", 1.0)
        data["mode"] = mode
        data["travel_time"] = length / speed
        data["blocked"] = False
    return G


def _build_rail_graph() -> nx.MultiDiGraph:
    """Construct synthetic rail graph from station list + connections."""
    R = nx.MultiDiGraph()
    for idx, (lat, lng, name) in enumerate(TRANSIT_STATIONS):
        R.add_node(f"rail_{idx}", y=lat, x=lng, name=name, mode="rail")

    for a, b in RAIL_CONNECTIONS:
        lat_a, lng_a, _ = TRANSIT_STATIONS[a]
        lat_b, lng_b, _ = TRANSIT_STATIONS[b]
        dist = ox.distance.great_circle(lat_a, lng_a, lat_b, lng_b)
        geom = LineString([(lng_a, lat_a), (lng_b, lat_b)])
        attrs = {
            "length": dist,
            "mode": "rail",
            "travel_time": dist / SPEED["rail"],
            "blocked": False,
            "geometry": geom,
            "name": f"{TRANSIT_STATIONS[a][2]} → {TRANSIT_STATIONS[b][2]}",
        }
        R.add_edge(f"rail_{a}", f"rail_{b}", **attrs)
        R.add_edge(f"rail_{b}", f"rail_{a}", **attrs)  # bidirectional
        
    # Auto-add transfer edges between stations within 400m of each other
    for idx1, (lat1, lng1, n1) in enumerate(TRANSIT_STATIONS):
        for idx2, (lat2, lng2, n2) in enumerate(TRANSIT_STATIONS):
            if idx1 < idx2:
                dist = ox.distance.great_circle(lat1, lng1, lat2, lng2)
                if dist <= 400.0:
                    geom = LineString([(lng1, lat1), (lng2, lat2)])
                    attrs = {
                        "length": dist,
                        "mode": "transfer",
                        "travel_time": dist / SPEED["walk"],
                        "blocked": False,
                        "geometry": geom,
                        "name": f"Walk Transfer: {n1} ↔ {n2}",
                    }
                    R.add_edge(f"rail_{idx1}", f"rail_{idx2}", **attrs)
                    R.add_edge(f"rail_{idx2}", f"rail_{idx1}", **attrs)

    return R


def _add_transfer_edges(G: nx.MultiDiGraph, R: nx.MultiDiGraph, transfer_radius_m: float = 200.0):
    """
    For each rail station, find the nearest road node in G
    and add a bidirectional 'transfer' edge so passengers can
    switch between walking/driving and the rail network.
    """
    road_nodes = {n: (data["y"], data["x"]) for n, data in G.nodes(data=True)
                  if "y" in data and "x" in data and not str(n).startswith("rail_")}
    node_ids = list(road_nodes.keys())
    node_coords = list(road_nodes.values())

    for rail_node, rdata in R.nodes(data=True):
        r_lat, r_lng = rdata["y"], rdata["x"]
        # Find nearest road node
        best_dist = float("inf")
        best_node = None
        for nid, (nlat, nlng) in zip(node_ids, node_coords):
            d = ox.distance.great_circle(r_lat, r_lng, nlat, nlng)
            if d < best_dist:
                best_dist = d
                best_node = nid

        if best_node is not None and best_dist <= transfer_radius_m:
            walk_time = best_dist / SPEED["walk"]
            geom = LineString([(r_lng, r_lat), (road_nodes[best_node][1], road_nodes[best_node][0])])
            transfer_attrs = {
                "length": best_dist,
                "mode": "transfer",
                "travel_time": walk_time,
                "blocked": False,
                "geometry": geom,
                "name": f"Transfer to {rdata.get('name', rail_node)}",
            }
            G.add_edge(best_node, rail_node, **transfer_attrs)
            G.add_edge(rail_node, best_node, **transfer_attrs)

    return G


def build_graph(force_rebuild: bool = False) -> nx.MultiDiGraph:
    """
    Main entry point. Loads (or rebuilds) the super-graph.
    Returns a MultiDiGraph with walk and rail layers.
    """
    if not force_rebuild and os.path.exists(CACHE_PATH):
        log.info("Loading cached graph from %s …", CACHE_PATH)
        with open(CACHE_PATH, "rb") as f:
            return pickle.load(f)

    log.info("Building strictly rail graph …")
    G_rail = _build_rail_graph()

    lats = [lat for lat, lng, _ in TRANSIT_STATIONS]
    lngs = [lng for lat, lng, _ in TRANSIT_STATIONS]
    north, south = max(lats) + 0.015, min(lats) - 0.015
    east, west = max(lngs) + 0.015, min(lngs) - 0.015

    log.info(f"Fetching walk network for BBOX: N:{north:.4f} S:{south:.4f} E:{east:.4f} W:{west:.4f} ...")
    G_walk = _graph_from_bbox(north, south, east, west, "walk", retain_all=True)
    G_walk = _add_edge_lengths(G_walk)
    G_walk = _assign_edge_attrs(G_walk, "walk")

    log.info("Combining walk and rail graphs ...")
    G_super = nx.compose(G_walk, G_rail)
    
    log.info("Adding transfer edges ...")
    G_super = _add_transfer_edges(G_super, G_rail, transfer_radius_m=200.0)

    log.info("Super-graph: %d nodes, %d edges", G_super.number_of_nodes(), G_super.number_of_edges())

    with open(CACHE_PATH, "wb") as f:
        pickle.dump(G_super, f)
    log.info("Graph cached to %s", CACHE_PATH)

    return G_super
