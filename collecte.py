"""Collecte : reçoit ce que l'app envoie (routes branchées dans app.py).

Avec la clé (en-tête X-Cle = variable d'environnement TRAFIC_CLE) :
    POST /collecte       points GPS, par lots de 500 maximum
    POST /signalement    incident (accident, travaux...) ou lieu (bar, hôtel, magasin...)
    POST /observation    enregistrement manuel : niveau de trafic constaté sur place

Sans clé (lecture seule, utilisées par l'app) :
    GET  /routes               les 10 routes de départ (data/routes.json)
    GET  /signalement/types    la liste des signalements possibles (data/types_signalement.json)
    GET  /feries               les jours fériés (data/feries.csv)

Pour chaque signalement et observation, le serveur recalcule lui-même la route la plus
proche (routes.py) : c'est cette valeur qui fait foi, pas celle envoyée par l'app.
Les données vont dans Firestore en ligne, dans SQLite en local (voir stockage.py).
"""
import functools
import hmac
import json
import os
from datetime import datetime

from flask import Blueprint, jsonify, request

import config
import prevision
import routes
import stockage

bp = Blueprint("collecte", __name__)
MAX_COMMENTAIRE = 300
MAX_CONTEXTE = 3000   # taille max (caractères JSON) du contexte envoyé par l'app


@functools.lru_cache(maxsize=1)
def catalogue():
    return json.loads(config.TYPES_SIGNALEMENT_JSON.read_text(encoding="utf-8"))


def types_signalement():
    """{"accident": {"id", "nom", "categorie", "circonstance"}, ...}"""
    return {t["id"]: t for t in catalogue()["types"]}


def cle_ok():
    cle = os.environ.get("TRAFIC_CLE")
    return cle is None or hmac.compare_digest(request.headers.get("X-Cle", ""), cle)


def ts_valide(valeur):
    """Accepte un horodatage ISO 8601 (ex. 2026-11-21T10:30:00Z)."""
    try:
        datetime.fromisoformat(str(valeur).replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def point_valide(p):
    """Vérifie TOUS les champs lus ensuite, pour qu'un seul mauvais point
    soit ignoré au lieu de faire échouer tout l'envoi."""
    if not isinstance(p, dict):
        return False
    try:
        return (
            -90 <= float(p["lat"]) <= 90
            and -180 <= float(p["lon"]) <= 180
            and 0 <= float(p["vitesse_kmh"]) <= 200
            and ts_valide(p["ts"])
            and float(p.get("precision_m", 99)) >= 0
            and int(p.get("en_vehicule", 1)) in (0, 1)
        )
    except (KeyError, TypeError, ValueError, OverflowError):
        return False


def nettoyer(p):
    """Garde uniquement les champs connus, avec le bon type."""
    return {
        "ts": str(p["ts"]), "lat": float(p["lat"]), "lon": float(p["lon"]),
        "vitesse_kmh": float(p["vitesse_kmh"]),
        "precision_m": float(p.get("precision_m", 99)),
        "en_vehicule": int(p.get("en_vehicule", 1)),
    }


def base_commune(d):
    """Champs communs aux signalements et observations. Lève ValueError si invalide."""
    if not ts_valide(d.get("ts")):
        raise ValueError("ts")
    lat, lon = float(d["lat"]), float(d["lon"])
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("lat/lon")
    toutes = routes.liste()
    route_choisie = None
    if d.get("route_id") is not None:
        # la personne a dit sur quelle rue elle est : on la croit (et on ajoutera le point)
        route_choisie = routes.par_id(int(d["route_id"]), toutes)
        if route_choisie is None:
            raise ValueError("route_id inconnu")
    if route_choisie is not None:
        dist = routes.distance_trace_m(lat, lon, routes.geometrie(route_choisie))
        route = {"id": route_choisie["id"], "nom": route_choisie["nom"], "choisie": True,
                 "distance_m": None if dist == float("inf") else round(dist), "sur_la_route": True}
    else:
        route = routes.plus_proche(lat, lon, toutes)
    doc = {"device": str(d.get("device", "?"))[:64], "ts": str(d["ts"]), "lat": lat, "lon": lon,
           "route": route}
    if route and route.get("id") is not None:
        doc["route_id"] = route["id"] if route.get("sur_la_route") else None
    if d.get("precision_m") is not None:
        doc["precision_m"] = round(float(d["precision_m"]), 1)
    commentaire = str(d.get("commentaire") or "").strip()[:MAX_COMMENTAIRE]
    if commentaire:
        doc["commentaire"] = commentaire
    contexte = d.get("contexte")
    if isinstance(contexte, dict) and len(json.dumps(contexte)) <= MAX_CONTEXTE:
        doc["contexte"] = contexte   # météo, férié, heure locale... vus par l'app
    return doc


def enregistrer(fonction, doc):
    """Enregistre le document ; si la personne a choisi sa rue, le point est aussi ajouté
    au tracé de cette rue (c'est comme ça que la carte se dessine)."""
    try:
        fonction(doc)
        point = None
        if (doc.get("route") or {}).get("choisie"):
            maj, ajoute = routes.ajouter_point(doc["route"]["id"], _point(doc))
            point = {"ajoute": ajoute, **routes.statut(len((maj or {}).get("points", [])))}
    except stockage.StockageIndisponible as e:
        return jsonify({"erreur": str(e)}), 503
    return jsonify({"ok": True, "route": doc["route"], "point_route": point})


def _point(doc, d=None):
    """Point de tracé. Saisi à la main (coordonnées tapées) : marqué "saisie": "manuelle"."""
    p = {"lat": doc["lat"], "lon": doc["lon"], "ts": doc["ts"], "device": doc["device"]}
    if d is not None and d.get("saisie") == "manuelle":
        p["saisie"] = "manuelle"
    return p


# --- Écriture (clé obligatoire si TRAFIC_CLE est définie) --------------------

@bp.post("/collecte")
def collecte():
    if not cle_ok():
        return jsonify({"erreur": "clé invalide"}), 401
    d = request.get_json(silent=True) or {}
    device = str(d.get("device", "?"))[:64]
    points = d.get("points", [])
    if not isinstance(points, list):
        return jsonify({"erreur": "'points' doit être une liste"}), 400

    valides = [nettoyer(p) for p in points[:config.MAX_POINTS_PAR_ENVOI] if point_valide(p)]
    try:
        stockage.ajouter_points(device, valides)
    except stockage.StockageIndisponible as e:
        return jsonify({"erreur": str(e)}), 503
    return jsonify({"recu": len(valides), "ignore": len(points) - len(valides)})


@bp.post("/signalement")
def signalement():
    if not cle_ok():
        return jsonify({"erreur": "clé invalide"}), 401
    d = request.get_json(silent=True) or {}
    types = types_signalement()
    try:
        t = types[str(d["type"]).lower()]
        doc = base_commune(d)
    except (KeyError, ValueError, TypeError):
        return jsonify({"erreur": "champs requis : ts (ISO 8601), lat, lon, type parmi : "
                                  + ", ".join(types)}), 400
    doc.update({"type": t["id"], "nom_type": t["nom"], "categorie": t["categorie"],
                "circonstance": t["circonstance"]})
    return enregistrer(stockage.ajouter_signalement, doc)


@bp.post("/observation")
def observation():
    """Enregistrement manuel : « ici, maintenant, le trafic est fluide / dense / embouteillage »."""
    if not cle_ok():
        return jsonify({"erreur": "clé invalide"}), 401
    d = request.get_json(silent=True) or {}
    try:
        niveau = str(d["niveau"]).lower()
        circonstance = str(d.get("circonstance") or "normal").lower()
        if niveau not in config.NIVEAUX or circonstance not in config.CIRCONSTANCES:
            raise ValueError("niveau/circonstance")
        doc = base_commune(d)
        doc.update({"niveau": niveau, "circonstance": circonstance,
                    "pluie": 1 if int(d.get("pluie", 0)) else 0})
    except (KeyError, ValueError, TypeError):
        return jsonify({"erreur": "champs requis : ts, lat, lon, niveau ("
                                  + "/".join(config.NIVEAUX) + "). Optionnels : pluie (0/1), "
                                  "circonstance (" + "/".join(config.CIRCONSTANCES) + "), "
                                  "commentaire, precision_m, contexte"}), 400
    return enregistrer(stockage.ajouter_observation, doc)


# --- Lecture publique (pour l'app) ---------------------------------------------

@bp.get("/routes")
def liste_routes():
    """Toutes les routes : 10 de départ, rues du catalogue (1001...), routes créées (11...),
    avec leurs points relevés sur le terrain et leur avancement."""
    d = routes.charger_depart()
    return jsonify({"ville": d.get("ville"), "seuils": {
        "dessinee": config.POINTS_ROUTE_DESSINEE, "etat": config.POINTS_ETAT_TEMPS_REEL,
        "prete": config.POINTS_ROUTE_PRETE}, "routes": routes.liste()})


@bp.post("/routes")
def creer_route():
    """Crée une route absente de la liste (ni départ, ni catalogue), à partir du point où l'on est."""
    if not cle_ok():
        return jsonify({"erreur": "clé invalide"}), 401
    d = request.get_json(silent=True) or {}
    try:
        nom = " ".join(str(d["nom"]).split())[:config.LONGUEUR_MAX_NOM_ROUTE]
        if len(nom) < 3:
            raise ValueError("nom")
        base = base_commune({k: v for k, v in d.items() if k != "route_id"})
    except (KeyError, ValueError, TypeError):
        return jsonify({"erreur": "champs requis : nom (3 caractères minimum), ts, lat, lon"}), 400

    toutes = routes.liste(rafraichir=True)
    meme_nom = [r for r in toutes if routes.cle_nom(r["nom"]) == routes.cle_nom(nom)]
    if meme_nom:
        r = meme_nom[0]
        return jsonify({"erreur": f"« {r['nom']} » est déjà dans la liste (n° {r['id']}) : "
                                  "choisis-la et ajoute-lui ce point.",
                        "route": {"id": r["id"], "nom": r["nom"]}}), 409
    proche = routes.plus_proche(base["lat"], base["lon"], toutes)
    if proche and proche["sur_la_route"]:
        return jsonify({"erreur": f"Tu es déjà sur la route {proche['id']} ({proche['nom']}), "
                                  f"à {proche['distance_m']} m.", "route": proche}), 409

    doc = {"nom": nom, "origine": "terrain", "description": str(d.get("description") or "").strip()[:200],
           "points": [_point(base, d)], "cree_par": base["device"], "cree_le": base["ts"]}
    if "contexte" in base:
        doc["contexte_creation"] = base["contexte"]
    try:
        numero = stockage.creer_route(doc, routes.prochain_numero_minimum())
    except stockage.StockageIndisponible as e:
        return jsonify({"erreur": str(e)}), 503
    routes.invalider_cache()
    import carte
    carte.invalider_cache()
    return jsonify({"ok": True, "route": {"id": numero, "nom": nom, **routes.statut(1)}}), 201


@bp.post("/routes/<int:numero>/points")
def ajouter_point(numero):
    """Ajoute le point où l'on est au tracé d'une route (n'importe laquelle)."""
    if not cle_ok():
        return jsonify({"erreur": "clé invalide"}), 401
    d = request.get_json(silent=True) or {}
    try:
        base = base_commune({k: v for k, v in d.items() if k != "route_id"})
    except (KeyError, ValueError, TypeError):
        return jsonify({"erreur": "champs requis : ts, lat, lon"}), 400
    toutes = routes.liste(rafraichir=True)
    route = routes.par_id(numero, toutes)
    if route is None:
        return jsonify({"erreur": f"Route {numero} introuvable."}), 404
    distance = routes.distance_trace_m(base["lat"], base["lon"], routes.geometrie(route))
    if distance != float("inf") and distance > config.DISTANCE_MAX_AJOUT_POINT_M:
        return jsonify({"erreur": f"Ce point est à {round(distance)} m de « {route['nom']} » : "
                                  "c'est sans doute une autre rue."}), 400
    if route["nb_points"] >= config.MAX_POINTS_ROUTE:
        return jsonify({"erreur": f"« {route['nom']} » a déjà {config.MAX_POINTS_ROUTE} points."}), 400
    try:
        maj, ajoute = routes.ajouter_point(numero, _point(base, d), toutes)
    except stockage.StockageIndisponible as e:
        return jsonify({"erreur": str(e)}), 503
    if maj is None:
        return jsonify({"erreur": f"Route {numero} introuvable."}), 404
    return jsonify({"ok": True, "ajoute": ajoute,
                    "route": {"id": numero, "nom": maj["nom"], **routes.statut(len(maj["points"]))}})


@bp.get("/signalement/types")
def liste_types():
    return jsonify(catalogue())


@bp.get("/feries")
def liste_feries():
    return jsonify({"feries": prevision.charger_feries().to_dict("records")})
