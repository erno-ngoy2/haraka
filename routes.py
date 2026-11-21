"""Les routes suivies et « sur quelle route suis-je ? ».

Deux sortes de routes :
  - les 10 routes de DÉPART (data/routes.json) : fixes, une ligne droite départ -> arrivée ;
  - les routes CRÉÉES sur le terrain depuis l'app (11, 12, ...) : rangées dans la base
    (stockage.py), elles commencent par un seul point puis s'allongent à chaque point ajouté.

Une route est un TRACÉ (liste de points). Un point est « sur » une route s'il est à moins de
config.DISTANCE_MAX_ROUTE_M du tracé. La même règle est codée dans l'app Android
(DetecteurRoute.kt) : garder les deux pareilles.
"""
import functools
import json
import math
import time

import config
import stockage

RAYON_TERRE_M = 6_371_000
_cache = {"quand": 0.0, "routes": None}


@functools.lru_cache(maxsize=1)
def charger_depart():
    return json.loads(config.ROUTES_JSON.read_text(encoding="utf-8"))


def _depuis_json(r):
    pts = [r["depart"], r["arrivee"]]
    return {**r, "origine": "depart",
            "points": [{"latitude": p["latitude"], "longitude": p["longitude"]} for p in pts]}


def _depuis_base(r):
    """Route créée (base) -> même forme que les routes de départ."""
    pts = [{"latitude": p["lat"], "longitude": p["lon"]} for p in r.get("points", [])]
    if not pts:
        return None
    return {
        "id": r["id"], "nom": r["nom"], "origine": "terrain",
        "description": r.get("description", ""),
        "depart": {"lieu": "Premier point", **pts[0]},
        "arrivee": {"lieu": "Dernier point", **pts[-1]},
        "points": pts,
        "cree_le": r.get("cree_le"),
    }


def liste(rafraichir=False):
    """Toutes les routes : les 10 de départ puis celles créées (relues au plus toutes les
    CACHE_ROUTES_S secondes). Si la base est indisponible, seulement les 10 de départ."""
    depart = [_depuis_json(r) for r in charger_depart()["routes"]]
    maintenant = time.monotonic()
    if rafraichir or _cache["routes"] is None or maintenant - _cache["quand"] > config.CACHE_ROUTES_S:
        try:
            creees = [r for r in map(_depuis_base, stockage.lister_routes_creees()) if r]
        except stockage.StockageIndisponible:
            creees = []
        _cache.update(quand=maintenant, routes=creees)
    return depart + _cache["routes"]


def invalider_cache():
    _cache["routes"] = None


def prochain_numero_minimum():
    return max(r["id"] for r in charger_depart()["routes"]) + 1


# --- Géométrie -------------------------------------------------------------------

def _xy(lat0, lon0, lat, lon):
    """Mètres autour de (lat0, lon0) : x vers l'est, y vers le nord."""
    k = math.cos(math.radians(lat0))
    return (math.radians(lon - lon0) * k * RAYON_TERRE_M, math.radians(lat - lat0) * RAYON_TERRE_M)


def _distance_segment(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy
    t = 0.0 if l2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / l2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def distance_trace_m(lat, lon, points):
    """Distance (m) du point au tracé (1 point = distance à ce point)."""
    xy = [_xy(lat, lon, p["latitude"], p["longitude"]) for p in points]
    if len(xy) == 1:
        return math.hypot(*xy[0])
    return min(_distance_segment(0, 0, *a, *b) for a, b in zip(xy, xy[1:]))


def plus_proche(lat, lon, routes=None):
    """{"id", "nom", "distance_m", "sur_la_route"} pour la route la plus proche."""
    r, d = min(((r, distance_trace_m(lat, lon, r["points"])) for r in (routes or liste())),
               key=lambda t: t[1])
    return {"id": r["id"], "nom": r["nom"], "distance_m": round(d),
            "sur_la_route": d <= config.DISTANCE_MAX_ROUTE_M}


def milieu(route):
    """Point au milieu du tracé, en longueur (pour une prévision « pour la route »)."""
    pts = route["points"]
    if len(pts) == 1:
        return pts[0]["latitude"], pts[0]["longitude"]
    lat0, lon0 = pts[0]["latitude"], pts[0]["longitude"]
    xy = [_xy(lat0, lon0, p["latitude"], p["longitude"]) for p in pts]
    longueurs = [math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(xy, xy[1:])]
    reste = sum(longueurs) / 2
    for (a, b), l, pa, pb in zip(zip(xy, xy[1:]), longueurs, pts, pts[1:]):
        if reste <= l and l > 0:
            t = reste / l
            return (pa["latitude"] + t * (pb["latitude"] - pa["latitude"]),
                    pa["longitude"] + t * (pb["longitude"] - pa["longitude"]))
        reste -= l
    return pts[-1]["latitude"], pts[-1]["longitude"]


def inserer_point(points, nouveau):
    """Insère `nouveau` là où il allonge le moins le tracé (avant le début, entre deux
    points, ou après la fin) : les points peuvent être ajoutés dans n'importe quel ordre."""
    if not points:
        return [nouveau]
    lat0, lon0 = nouveau["lat"], nouveau["lon"]
    xy = [_xy(lat0, lon0, p["lat"], p["lon"]) for p in points]

    def d(a, b):
        return math.hypot(a[0] - b[0], a[1] - b[1])

    o = (0.0, 0.0)  # le nouveau point
    choix = [(d(o, xy[0]), 0), (d(o, xy[-1]), len(points))]
    choix += [(d(xy[i], o) + d(o, xy[i + 1]) - d(xy[i], xy[i + 1]), i + 1) for i in range(len(xy) - 1)]
    _, position = min(choix)
    return points[:position] + [nouveau] + points[position:]
