"""Les routes suivies, leurs points relevés sur le terrain, et « sur quelle route suis-je ? ».

Trois sortes de routes (toutes au même format) :
  - "depart"    : les 10 routes de départ (data/routes.json, ids 1 à 10). Leur départ et leur
                  arrivée servent seulement de « référence » tant qu'il n'y a pas de points ;
  - "catalogue" : les rues de Lubumbashi relevées sur internet (data/rues_lubumbashi.json,
                  ids 1001...) : un nom, sans coordonnées au départ ;
  - "terrain"   : les routes créées depuis l'app (ids 11, 12, ...).

La VRAIE forme d'une route vient des points relevés sur le terrain (rangés dans la base,
un document par route, voir stockage.py). Seuils dans config.py :
  POINTS_ROUTE_DESSINEE (4)   les points se relient en une route sur la carte
  POINTS_ETAT_TEMPS_REEL (6)  l'état de la route (fluide / dense / embouteillage) s'affiche
  POINTS_ROUTE_PRETE (11)     la route a sa forme : 100 %, « prête »

La même géométrie est codée dans l'app Android (DetecteurRoute.kt) : garder les deux pareilles.
"""
import functools
import json
import math
import re
import time
import unicodedata

import config
import stockage

RAYON_TERRE_M = 6_371_000
_cache = {"quand": 0.0, "docs": None}
TYPES_VOIE = {"avenue": "avenue", "av": "avenue", "boulevard": "boulevard", "bd": "boulevard",
              "rue": "rue", "route": "route", "chaussee": "chaussée"}
ARTICLES = {"de", "du", "des", "la", "le", "les", "l", "d"}


# --- Noms --------------------------------------------------------------------------

def _sans_accents(texte):
    return "".join(c for c in unicodedata.normalize("NFD", texte) if unicodedata.category(c) != "Mn")


def _mots(nom):
    nom = re.sub(r"\(.*?\)", " ", _sans_accents(str(nom)).lower())
    return re.findall(r"[a-z0-9]+", nom)


def type_de_voie(nom):
    m = _mots(nom)
    return TYPES_VOIE.get(m[0], "autre") if m else "autre"


def cle_nom(nom):
    """Nom réduit pour comparer : sans type de voie, articles, accents, tirets ni espaces.
    « Avenue Kasa-Vubu (tronçon) » et « Av. Kasavubu » -> « kasavubu »."""
    m = _mots(nom)
    if m and m[0] in TYPES_VOIE:
        m = m[1:]
    return "".join(x for x in m if x not in ARTICLES)


# --- Liste des routes ----------------------------------------------------------------

@functools.lru_cache(maxsize=1)
def charger_depart():
    return json.loads(config.ROUTES_JSON.read_text(encoding="utf-8"))


@functools.lru_cache(maxsize=1)
def charger_catalogue():
    if not config.RUES_JSON.exists():
        return []
    return json.loads(config.RUES_JSON.read_text(encoding="utf-8"))["rues"]


@functools.lru_cache(maxsize=1)
def statiques():
    """Routes de départ + catalogue, sans points de terrain."""
    depart = [{
        "id": r["id"], "nom": r["nom"], "type": type_de_voie(r["nom"]), "origine": "depart",
        "description": r.get("description", ""),
        "reference": [{"latitude": p["latitude"], "longitude": p["longitude"]} for p in (r["depart"], r["arrivee"])],
    } for r in charger_depart()["routes"]]
    catalogue = [{"id": r["id"], "nom": r["nom"], "type": r.get("type", type_de_voie(r["nom"])),
                  "origine": "catalogue", "description": "", "reference": []} for r in charger_catalogue()]
    return depart + catalogue


def _docs_base(rafraichir=False):
    """Documents de la base (points de terrain + routes créées), relus au plus toutes les
    CACHE_ROUTES_S secondes. Base indisponible -> aucun."""
    maintenant = time.monotonic()
    if rafraichir or _cache["docs"] is None or maintenant - _cache["quand"] > config.CACHE_ROUTES_S:
        try:
            docs = stockage.lister_routes_creees()
        except stockage.StockageIndisponible:
            docs = []
        _cache.update(quand=maintenant, docs=docs)
    return _cache["docs"]


def invalider_cache():
    _cache["docs"] = None


def statut(nb_points):
    """Avancement d'une route selon le nombre de points relevés."""
    return {
        "nb_points": nb_points,
        "avancement": round(min(nb_points / config.POINTS_ROUTE_PRETE, 1.0) * 100),
        "dessinee": nb_points >= config.POINTS_ROUTE_DESSINEE,
        "etat_disponible": nb_points >= config.POINTS_ETAT_TEMPS_REEL,
        "prete": nb_points >= config.POINTS_ROUTE_PRETE,
    }


def _points_terrain(doc):
    return [{"latitude": p["lat"], "longitude": p["lon"]} for p in doc.get("points", [])]


def liste(rafraichir=False):
    """Toutes les routes avec leurs points de terrain et leur avancement.
    Une rue du catalogue qui porte le même nom qu'une route créée sur le terrain est masquée
    (la route créée la remplace)."""
    docs = {d["id"]: d for d in _docs_base(rafraichir)}
    ids_statiques = {r["id"] for r in statiques()}
    creees = []
    for i, d in sorted(docs.items()):
        if i in ids_statiques or not d.get("points"):
            continue
        creees.append({"id": i, "nom": d["nom"], "type": type_de_voie(d["nom"]), "origine": "terrain",
                       "description": d.get("description", ""), "reference": [], "cree_le": d.get("cree_le")})
    cles_creees = {cle_nom(r["nom"]) for r in creees}
    resultat = []
    for r in list(statiques()) + creees:
        if r["origine"] == "catalogue" and cle_nom(r["nom"]) in cles_creees:
            continue
        doc = docs.get(r["id"], {})
        pts = _points_terrain(doc)
        trace = [{"latitude": p["lat"], "longitude": p["lon"]} for p in doc.get("trace", [])]
        # « points » : COMPTÉS dans l'avancement ; « trace » : forme exacte, NON comptée
        resultat.append({**r, "points": pts, "trace": trace, **statut(len(pts))})
    return resultat


def par_id(numero, routes=None):
    return next((r for r in (routes or liste()) if r["id"] == numero), None)


def prochain_numero_minimum():
    """Les routes créées sur le terrain sont numérotées après les 10 de départ (11, 12...)."""
    return max(r["id"] for r in charger_depart()["routes"]) + 1


# --- Géométrie -------------------------------------------------------------------

def geometrie(route):
    """Tracé utilisé pour la distance : les points de terrain dès qu'il y en a 2, sinon la
    ligne de référence (routes de départ), sinon le point unique, sinon rien."""
    trace = route.get("trace", [])
    if len(trace) >= 2:
        return trace          # forme exacte relevée en cartographie en direct
    pts = route.get("points", [])
    if len(pts) >= 2:
        return pts
    if route.get("reference"):
        return route["reference"]
    return pts


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
    """Distance (m) du point au tracé (1 point = distance à ce point ; aucun = infini)."""
    if not points:
        return float("inf")
    xy = [_xy(lat, lon, p["latitude"], p["longitude"]) for p in points]
    if len(xy) == 1:
        return math.hypot(*xy[0])
    return min(_distance_segment(0, 0, *a, *b) for a, b in zip(xy, xy[1:]))


def plus_proche(lat, lon, routes=None):
    """{"id", "nom", "distance_m", "sur_la_route"} pour la route la plus proche parmi celles qui
    ont une position connue (les rues du catalogue sans point ne peuvent pas être devinées)."""
    candidates = [(r, distance_trace_m(lat, lon, geometrie(r))) for r in (routes or liste())]
    candidates = [c for c in candidates if c[1] != float("inf")]
    if not candidates:
        return None
    r, d = min(candidates, key=lambda t: t[1])
    return {"id": r["id"], "nom": r["nom"], "distance_m": round(d),
            "sur_la_route": d <= config.DISTANCE_MAX_ROUTE_M}


def milieu(route):
    """Point au milieu du tracé, en longueur (pour une prévision « pour la route »)."""
    pts = geometrie(route)
    if not pts:
        return None
    if len(pts) == 1:
        return pts[0]["latitude"], pts[0]["longitude"]
    lat0, lon0 = pts[0]["latitude"], pts[0]["longitude"]
    xy = [_xy(lat0, lon0, p["latitude"], p["longitude"]) for p in pts]
    longueurs = [math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(xy, xy[1:])]
    reste = sum(longueurs) / 2
    for l, pa, pb in zip(longueurs, pts, pts[1:]):
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


def ajouter_point(numero, point, routes=None):
    """Ajoute un point relevé à la route `numero` (toute route : départ, catalogue, terrain).
    Retourne (route mise à jour, ajouté ?) ou (None, False) si la route n'existe pas.
    Un point à moins de DISTANCE_MIN_ENTRE_POINTS_M d'un point existant n'est pas ajouté."""
    route = par_id(numero, routes)
    if route is None:
        return None, False
    ajoute = {"fait": False}

    def transformer(points):
        deja_la = any(distance_trace_m(point["lat"], point["lon"], [{"latitude": p["lat"], "longitude": p["lon"]}])
                      < config.DISTANCE_MIN_ENTRE_POINTS_M for p in points)
        if deja_la or len(points) >= config.MAX_POINTS_ROUTE:
            return points
        ajoute["fait"] = True
        return inserer_point(points, point)

    si_absente = {"nom": route["nom"], "origine": route["origine"]}
    maj = stockage.modifier_points_route(numero, transformer, si_absente=si_absente)
    invalider_cache()
    import carte
    carte.invalider_cache()
    return maj, ajoute["fait"]



# --- Tracé de forme (cartographie en direct, positions toutes les 2 s) ----------------

def _simplifier(points, tolerance_m):
    """Douglas-Peucker : garde seulement les points nécessaires à la forme (à tolerance_m près)."""
    if len(points) <= 2:
        return points
    lat0, lon0 = points[0]["lat"], points[0]["lon"]
    xy = [_xy(lat0, lon0, p["lat"], p["lon"]) for p in points]
    garder = [False] * len(points)
    garder[0] = garder[-1] = True
    pile = [(0, len(points) - 1)]
    while pile:
        a, b = pile.pop()
        dmax, imax = 0.0, None
        for i in range(a + 1, b):
            d = _distance_segment(*xy[i], *xy[a], *xy[b])
            if d > dmax:
                dmax, imax = d, i
        if imax is not None and dmax > tolerance_m:
            garder[imax] = True
            pile += [(a, imax), (imax, b)]
    return [p for p, g in zip(points, garder) if g]


def ajouter_trace(numero, nouveaux, routes=None):
    """Ajoute des positions au TRACÉ de forme de la route (ne compte pas dans l'avancement).
    Retourne (route mise à jour, nombre de points du tracé) ou (None, 0)."""
    route = par_id(numero, routes)
    if route is None:
        return None, 0

    def transformer(trace):
        trace = list(trace)
        for p in nouveaux:
            proche = any(distance_trace_m(p["lat"], p["lon"], [{"latitude": q["lat"], "longitude": q["lon"]}])
                         < config.DISTANCE_MIN_TRACE_M for q in trace)
            if not proche:
                trace = inserer_point(trace, p)
        trace = _simplifier(trace, config.TOLERANCE_TRACE_M)
        return trace[-config.MAX_POINTS_TRACE:]

    si_absente = {"nom": route["nom"], "origine": route["origine"]}
    maj = stockage.modifier_points_route(numero, transformer, si_absente=si_absente, champ="trace")
    invalider_cache()
    import carte
    carte.invalider_cache()
    return maj, len((maj or {}).get("trace", []))
