import logging
import heapq
from typing import Dict, Any, List, Tuple, Set, Optional
import networkx as nx
from shapely.geometry import LineString
import math

log = logging.getLogger(__name__)

# Node tuple: (u, v, key)
BlockedSet = Set[Tuple[str, str, int]]

def _dist_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6371000
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1))*math.cos(math.radians(lat2))*math.sin(dlng/2)**2
    return 2 * R * math.asin(math.sqrt(a))

def _dist_heuristic(G: nx.MultiDiGraph, node_a: Any, node_b: Any) -> float:
    """
    Haversine distance proxy used for A* heuristic.
    Must not overestimate distance to guarantee shortest path.
    """
    a_data = G.nodes[node_a]
    b_data = G.nodes[node_b]
    if "y" not in a_data or "x" not in a_data or "y" not in b_data or "x" not in b_data:
        return 0.0

    return _dist_m(a_data["y"], a_data["x"], b_data["y"], b_data["x"])

def _heuristic(G: nx.MultiDiGraph, current_node: Any, end_node: Any) -> float:
    """
    Travel time (seconds) heuristic.
    We assume max speed ~ 13 m/s (approx 50 km/h) for bounding.
    """
    dist = _dist_heuristic(G, current_node, end_node)
    return dist / 13.0

def _nearest_node(G: nx.MultiDiGraph, lat: float, lng: float) -> Any:
    best_node = None
    best_dist = float("inf")

    # Limit search radius to roughly 20km (0.2 degrees approx)
    max_dist = 20000

    for n, data in G.nodes(data=True):
        if "y" not in data or "x" not in data:
            continue
        ny, nx_ = data["y"], data["x"]
        
        # quick bounding box check
        if abs(ny - lat) > 0.3 or abs(nx_ - lng) > 0.3:
            continue

        dy = (ny - lat) * 111000
        dx = (nx_ - lng) * 111000
        dist = (dx**2 + dy**2) ** 0.5
        if dist < best_dist and dist < max_dist:
            best_dist = dist
            best_node = n

    return best_node

def _extract_edge_coords(G: nx.MultiDiGraph, u: Any, v: Any, key: int) -> List[List[float]]:
    """
    Extract the full list of [lat, lng] waypoints for an edge.
    Ensures geometric alignment from u to v.
    """
    data = G[u][v][key]
    u_y, u_x = G.nodes[u].get("y"), G.nodes[u].get("x")
    v_y, v_x = G.nodes[v].get("y"), G.nodes[v].get("x")

    if "geometry" in data:
        geom = data["geometry"]
        coords = [[lat, lng] for lng, lat in geom.coords]
        if u_y is not None and u_x is not None and coords:
            # Check Euclidean dist from u to first vs last coord to ensure sequence flow
            d_start = (coords[0][0] - u_y)**2 + (coords[0][1] - u_x)**2
            d_end = (coords[-1][0] - u_y)**2 + (coords[-1][1] - u_x)**2
            if d_start > d_end:
                coords.reverse()
        return coords
        
    if u_y is not None and u_x is not None and v_y is not None and v_x is not None:
        return [[u_y, u_x], [v_y, v_x]]
    return []

def astar_route(
    G: nx.MultiDiGraph,
    start_lat: float,
    start_lng: float,
    end_lat: float,
    end_lng: float,
    mode: str = "multimodal",
    blocked: Optional[BlockedSet] = None,
) -> Dict:
    """
    Run A* from (start_lat, start_lng) to (end_lat, end_lng).

    mode:
        'walk'       - only edges with mode=='walk'
        'drive'      - only edges with mode in ('walk','drive','transfer')
        'multimodal' - all edges (walk + drive + rail + transfer)

    blocked: set of (u, v, key) tuples to treat as impassable.

    Returns a dict:
        coords      : [[lat,lng], ...] full geometry waypoints
        segments    : list of {mode, from_lat, from_lng, to_lat, to_lng, distance_m}
        distance_m  : total route length
        time_s      : total estimated travel time
        start_node  : id of nearest start node
        end_node    : id of nearest end node
    """
    if blocked is None:
        blocked = set()

    # Mode filter
    allowed_modes = {
        "walk": {"walk"},
        "drive": {"walk", "drive", "transfer"},
        "multimodal": {"walk", "drive", "rail", "transfer"},
    }.get(mode, {"walk", "drive", "rail", "transfer"})

    log.info("A* route: (%.5f,%.5f) -> (%.5f,%.5f) mode=%s", start_lat, start_lng, end_lat, end_lng, mode)

    start_node = _nearest_node(G, start_lat, start_lng)
    end_node = _nearest_node(G, end_lat, end_lng)

    if start_node is None or end_node is None:
        raise ValueError("Could not find nearest nodes for given coordinates.")

    if start_node == end_node:
        d_direct = _dist_m(start_lat, start_lng, end_lat, end_lng)
        return {
            "coords": [[start_lat, start_lng], [end_lat, end_lng]],
            "segments": [{
                "mode": "walk",
                "from_lat": start_lat, "from_lng": start_lng,
                "to_lat": end_lat, "to_lng": end_lng,
                "distance_m": round(d_direct, 1),
                "name": "Walk directly"
            }],
            "distance_m": round(d_direct, 1),
            "time_s": round(d_direct / 1.2, 1),
            "start_node": str(start_node),
            "end_node": str(end_node),
        }

    # -- A* search ---------------------------------------------------------
    # heap: (f_score, g_score, node)
    h0 = _heuristic(G, start_node, end_node)
    open_heap = [(h0, 0.0, start_node)]
    # came_from[node] = (parent_node, u, v, edge_key)
    came_from: Dict[Any, Tuple] = {start_node: (None, None, None, None)}
    g_score: Dict[Any, float] = {start_node: 0.0}
    visited = set()

    found = False
    while open_heap:
        f, g, current = heapq.heappop(open_heap)

        if current in visited:
            continue
        visited.add(current)

        if current == end_node:
            found = True
            break

        for neighbor, edge_dict in G[current].items():
            if neighbor in visited:
                continue
            for k, edata in edge_dict.items():
                edge_mode = edata.get("mode", "walk")
                if edge_mode not in allowed_modes:
                    continue
                if edata.get("blocked", False):
                    continue
                if (current, neighbor, k) in blocked:
                    continue

                travel_time = edata.get("travel_time", edata.get("length", 1.0) / 1.2)
                
                # Áp dụng Hình phạt Thời gian (Time Penalty) 3 phút cho chuyển tiếp ga
                if edge_mode == "transfer":
                    travel_time += 180.0

                tentative_g = g + travel_time

                if tentative_g < g_score.get(neighbor, float("inf")):
                    g_score[neighbor] = tentative_g
                    h = _heuristic(G, neighbor, end_node)
                    f_new = tentative_g + h
                    heapq.heappush(open_heap, (f_new, tentative_g, neighbor))
                    came_from[neighbor] = (current, current, neighbor, k)

    if not found:
        raise ValueError(
            f"No path found between nodes {start_node} and {end_node} "
            f"with mode='{mode}' and {len(blocked)} blocked edges."
        )

    # -- Reconstruct path --------------------------------------------------
    edge_path: List[Tuple[Any, Any, int]] = []
    node = end_node
    while True:
        parent_node, u, v, k = came_from[node]
        if parent_node is None:
            break
        edge_path.append((u, v, k))
        node = parent_node
    edge_path.reverse()

    # -- Build output ------------------------------------------------------
    all_coords: List[List[float]] = []
    segments: List[Dict] = []
    total_dist = 0.0
    total_time = 0.0

    start_d = G.nodes[start_node]
    d_start = _dist_m(start_lat, start_lng, start_d.get("y", start_lat), start_d.get("x", start_lng))
    if d_start > 2.0:
        all_coords.extend([[start_lat, start_lng], [start_d.get("y", start_lat), start_d.get("x", start_lng)]])
        segments.append({
            "mode": "walk",
            "from_lat": start_lat, "from_lng": start_lng,
            "to_lat": start_d.get("y", start_lat), "to_lng": start_d.get("x", start_lng),
            "distance_m": round(d_start, 1),
            "name": "Walk to road"
        })
        total_dist += d_start
        total_time += d_start / 1.2

    for u, v, k in edge_path:
        edata = G[u][v][k]
        coords = _extract_edge_coords(G, u, v, k)

        # Avoid duplicate waypoints at edge joins
        if all_coords and coords and all_coords[-1] == coords[0]:
            coords = coords[1:]
        all_coords.extend(coords)

        dist = edata.get("length", 0.0)
        t = edata.get("travel_time", 0.0)
        # Add 180-second penalty for transfer links in the final output calculation
        if edata.get("mode", "walk") == "transfer":
            t += 180.0

        total_dist += dist
        total_time += t

        u_d, v_d = G.nodes[u], G.nodes[v]
        segments.append({
            "mode": edata.get("mode", "walk"),
            "from_lat": u_d.get("y", 0),
            "from_lng": u_d.get("x", 0),
            "to_lat": v_d.get("y", 0),
            "to_lng": v_d.get("x", 0),
            "distance_m": round(dist, 1),
            "name": edata.get("name", ""),
        })

    end_d = G.nodes[end_node]
    d_end = _dist_m(end_d.get("y", end_lat), end_d.get("x", end_lng), end_lat, end_lng)
    if d_end > 2.0:
        if all_coords and all_coords[-1] == [end_d.get("y", end_lat), end_d.get("x", end_lng)]:
            pass
        else:
            all_coords.append([end_d.get("y", end_lat), end_d.get("x", end_lng)])
        all_coords.append([end_lat, end_lng])
        segments.append({
            "mode": "walk",
            "from_lat": end_d.get("y", end_lat), "from_lng": end_d.get("x", end_lng),
            "to_lat": end_lat, "to_lng": end_lng,
            "distance_m": round(d_end, 1),
            "name": "Walk to destination"
        })
        total_dist += d_end
        total_time += d_end / 1.2

    return {
        "coords": all_coords,
        "segments": segments,
        "distance_m": round(total_dist, 1),
        "time_s": round(total_time, 1),
        "start_node": str(start_node),
        "end_node": str(end_node),
    }
