import csv
import json
import math
from pathlib import Path

from flask import Flask, render_template, request

import price
from dijkstra import dijkstra_shortest_path, load_graph_from_csv

HERE = Path(__file__).parent
app = Flask(__name__, static_folder="public", static_url_path="")

MAP = json.load(open(HERE / "map_data.json", encoding="utf-8"))
SPECIAL_STATIONS = MAP["special"]
TRAM = "Tokyo Sakura Tram"
SPEED = {"Tokyo Sakura Tram": 12.5, "Asakusa Line": 27.5, "Mita Line": 28.5,
         "Nippori-Toneri Liner": 26.9, "Oedo Line": 25.8, "Shinjuku Line": 32.1}
FARE_TYPES = ["adult_ic", "adult_ticket", "children_ic", "children_ticket"]
FARE_LABELS = {"adult_ic": "Adult · IC", "adult_ticket": "Adult · Ticket",
               "children_ic": "Child · IC", "children_ticket": "Child · Ticket"}

graph = load_graph_from_csv(HERE / "station_graph_edges.csv")
hub_a, hub_b = MAP["hub"]
graph[hub_a][hub_b] = 0.0
graph[hub_b][hub_a] = 0.0

edge_line = {(hub_a, hub_b): "Transfer", (hub_b, hub_a): "Transfer"}
for row in csv.DictReader(open(HERE / "station_graph_edges.csv", encoding="utf-8")):
    a = row["Station A"].strip()
    b = row["Station B"].strip()
    if "Sakura" in row["railway"]:
        name = TRAM
    else:
        name = row["railway"].replace("Toei ", "")
    edge_line[(a, b)] = name
    edge_line[(b, a)] = name


def distance_of(path):
    return sum(graph[path[i]][path[i + 1]] for i in range(len(path) - 1))


def top_routes(start, end, count=3):
    best = dijkstra_shortest_path(graph, start, end)[1]
    if not best:
        return []
    found = [best]
    candidates = []
    while len(found) < count:
        last = found[-1]
        for i in range(len(last) - 1):
            root = last[:i + 1]
            copy = {station: dict(neighbours) for station, neighbours in graph.items()}
            for path in found:
                if path[:i + 1] == root:
                    copy[path[i]].pop(path[i + 1], None)
            for station in root[:-1]:
                for neighbour in list(copy[station]):
                    copy[neighbour].pop(station, None)
                copy[station] = {}
            rest = dijkstra_shortest_path(copy, last[i], end)[1]
            if rest:
                new_path = root[:-1] + rest
                already_listed = [c[1] for c in candidates]
                if new_path not in found and new_path not in already_listed:
                    candidates.append((distance_of(new_path), new_path))
        if not candidates:
            break
        candidates.sort()
        found.append(candidates.pop(0)[1])
    return found


def describe(path):
    legs = []
    for i in range(len(path) - 1):
        a = path[i]
        b = path[i + 1]
        line = edge_line[(a, b)]
        km = graph[a][b]
        if legs and legs[-1]["line"] == line:
            legs[-1]["to"] = b
            legs[-1]["stops"] += 1
            legs[-1]["km"] += km
        else:
            legs.append({"line": line, "from": a, "to": b, "km": km, "stops": 1})

    total_km = distance_of(path)
    transfer_stations = [leg["from"] for leg in legs[1:]]

    tram_km = sum(leg["km"] for leg in legs if leg["line"] == TRAM)
    toei_km = sum(leg["km"] for leg in legs if leg["line"] not in (TRAM, "Transfer"))

    split_station = None
    for station in transfer_stations:
        if station in SPECIAL_STATIONS:
            split_station = station
            break

    if split_station:
        i = path.index(split_station)
        values = price.fare_transfer(distance_of(path[:i + 1]), distance_of(path[i:]))
        mode = f"Split fare via {split_station}"
    elif tram_km:
        values = price.fare_sakura(tram_km, toei_km)
        mode = "Sakura Tram combination fare"
    else:
        values = price.fare(total_km)
        mode = "Standard shortest-route fare"

    fares = {}
    for i in range(len(FARE_TYPES)):
        fares[FARE_TYPES[i]] = values[i]

    minutes = sum(leg["km"] / SPEED.get(leg["line"], 28) * 60 for leg in legs) + (len(legs) - 1) * 4
    for leg in legs:
        leg["color"] = MAP["colors"].get(leg["line"], "#888")

    lines = []
    for leg in legs:
        if leg["line"] != "Transfer" and leg["line"] not in lines:
            lines.append(leg["line"])

    return {"stations": path, "legs": legs, "km": total_km, "minutes": round(minutes),
            "transfers": len(transfer_stations), "fares": fares, "mode": mode, "lines": lines}


def map_svg(path):
    k = math.cos(math.radians(35.7))
    xy = {n: (s["lng"] * k, -s["lat"]) for n, s in MAP["stations"].items()}
    left, top = min(x for x, y in xy.values()), min(y for x, y in xy.values())
    scale = 760 / (max(x for x, y in xy.values()) - left)
    P = {n: ((x - left) * scale, (y - top) * scale) for n, (x, y) in xy.items()}

    shown = [P[n] for n in path] or list(P.values())
    m = 80 if path else 20
    x0, y0 = min(x for x, y in shown) - m, min(y for x, y in shown) - m
    w = max(x for x, y in shown) + m - x0
    h = max(y for x, y in shown) + m - y0
    u = w / 800

    used = set(zip(path, path[1:])) | set(zip(path[1:], path))
    svg = [f'<svg viewBox="{x0:.0f} {y0:.0f} {w:.0f} {h:.0f}" width="100%" height="100%">']
    for a in graph:
        for b in graph[a]:
            if a < b:
                color = MAP["colors"].get(edge_line[(a, b)], "#888")
                hot = (a, b) in used
                svg.append(f'<line x1="{P[a][0]:.0f}" y1="{P[a][1]:.0f}" x2="{P[b][0]:.0f}" y2="{P[b][1]:.0f}" '
                           f'stroke="{color}" stroke-width="{(6 if hot else 2.5) * u:.1f}" '
                           f'opacity="{0.3 if path and not hot else 1}"/>')
    for n, (x, y) in P.items():
        if n in path:
            svg.append(f'<circle cx="{x:.0f}" cy="{y:.0f}" r="{5 * u:.1f}" fill="#ffffff" '
                       f'stroke="#0b1b4d" stroke-width="{2 * u:.1f}"><title>{n}</title></circle>')
        else:
            svg.append(f'<circle cx="{x:.0f}" cy="{y:.0f}" r="{2.5 * u:.1f}" fill="#7f9fd6"><title>{n}</title></circle>')
    for n in path[:1] + path[-1:]:
        svg.append(f'<text x="{P[n][0] + 9 * u:.0f}" y="{P[n][1] - 9 * u:.0f}" fill="#0b1b4d" '
                   f'stroke="#ffffff" stroke-width="{4 * u:.1f}" paint-order="stroke" '
                   f'font-size="{15 * u:.0f}" font-weight="700">{n}</text>')
    return "".join(svg) + "</svg>"


@app.route("/")
def index():
    start = request.args.get("from", "").strip()
    end = request.args.get("to", "").strip()
    try:
        pick = int(request.args.get("route", 0))
    except ValueError:
        pick = 0

    routes = []
    error = None
    if start or end:
        if start not in graph or end not in graph:
            error = "Station not recognized. Pick from the suggestion list."
        elif start == end:
            error = "Pick two different stations."
        else:
            for path in top_routes(start, end, 3):
                routes.append(describe(path))

    if pick < 0 or pick >= len(routes):
        pick = 0
    path = routes[pick]["stations"] if routes else []
    return render_template("index.html", stations=sorted(graph), frm=start, to=end, routes=routes,
                           pick=pick, error=error, labels=FARE_LABELS, map=map_svg(path))


if __name__ == "__main__":
    app.run(debug=True)
