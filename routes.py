"""Les 10 routes de départ (data/routes.json) et « sur quelle route suis-je ? ».

Chaque route est simplifiée en une ligne droite départ -> arrivée. Un point est
« sur » une route s'il est à moins de config.DISTANCE_MAX_ROUTE_M de cette ligne.
La même règle est codée dans l'app Android (RouteDetector.kt) : garder les deux pareilles.
"""
import functools
import json
import math

import config

RAYON_TERRE_M = 6_371_000


@functools.lru_cache(maxsize=1)
def charger():
    return json.loads(config.ROUTES_JSON.read_text(encoding="utf-8"))


def liste():
    return charger()["routes"]


def _distance_segment_m(lat, lon, a, b):
    """Distance (m) du point au segment [a, b], en projetant autour du point."""
    k = math.cos(math.radians(lat))

    def xy(la, lo):
        return (math.radians(lo - lon) * k * RAYON_TERRE_M, math.radians(la - lat) * RAYON_TERRE_M)

    ax, ay = xy(a["latitude"], a["longitude"])
    bx, by = xy(b["latitude"], b["longitude"])
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy
    t = 0.0 if l2 == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / l2))
    px, py = ax + t * dx, ay + t * dy
    return math.hypot(px, py)


def plus_proche(lat, lon):
    """{"id", "nom", "distance_m", "sur_la_route"} pour la route la plus proche."""
    meilleure = min(
        ((r, _distance_segment_m(lat, lon, r["depart"], r["arrivee"])) for r in liste()),
        key=lambda t: t[1])
    r, d = meilleure
    return {"id": r["id"], "nom": r["nom"], "distance_m": round(d),
            "sur_la_route": d <= config.DISTANCE_MAX_ROUTE_M}


def milieu(route):
    """Point au milieu de la route (pour demander une prévision « pour la route »)."""
    return ((route["depart"]["latitude"] + route["arrivee"]["latitude"]) / 2,
            (route["depart"]["longitude"] + route["arrivee"]["longitude"]) / 2)
