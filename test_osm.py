import osmnx as ox
import logging
logging.basicConfig(level=logging.INFO)

ox.settings.requests_timeout = 10
ox.settings.overpass_rate_limit = False
ox.settings.overpass_endpoint = "https://overpass-api.de/api/interpreter"

try:
    print("Testing 1x1km drive network around Siam...")
    G = ox.graph_from_bbox(bbox=(100.530, 13.740, 100.540, 13.750), network_type="drive")
    print("Success! Nodes:", len(G.nodes))
except Exception as e:
    print("Failed:", e)
