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
    doc = {"device": str(d.get("device", "?"))[:64], "ts": str(d["ts"]), "lat": lat, "lon": lon,
           "route": routes.plus_proche(lat, lon)}
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
    try:
        fonction(doc)
    except stockage.StockageIndisponible as e:
        return jsonify({"erreur": str(e)}), 503
    return jsonify({"ok": True, "route": doc["route"]})


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
    """Les 10 routes de départ + celles créées sur le terrain (11, 12, ...)."""
    d = routes.charger_depart()
    return jsonify({k: v for k, v in d.items() if k != "routes"} | {"routes": routes.liste()})


def _normaliser_nom(nom):
    return " ".join(str(nom).lower().replace("avenue", "av").replace("av.", "av")
                    .replace("boulevard", "bd").replace("bd.", "bd").split())


@bp.post("/routes")
def creer_route():
    """Crée une route qui ne fait pas encore partie de la liste, à partir du point où l'on est."""
    if not cle_ok():
        return jsonify({"erreur": "clé invalide"}), 401
    d = request.get_json(silent=True) or {}
    try:
        nom = " ".join(str(d["nom"]).split())[:config.LONGUEUR_MAX_NOM_ROUTE]
        if len(nom) < 3:
            raise ValueError("nom")
        base = base_commune(d)
    except (KeyError, ValueError, TypeError):
        return jsonify({"erreur": "champs requis : nom (3 caractères minimum), ts, lat, lon"}), 400

    toutes = routes.liste(rafraichir=True)
    proche = routes.plus_proche(base["lat"], base["lon"], toutes)
    if proche["sur_la_route"]:
        return jsonify({"erreur": f"Tu es déjà sur la route {proche['id']} ({proche['nom']}), "
                                  f"à {proche['distance_m']} m.", "route": proche}), 409
    meme_nom = [r for r in toutes if _normaliser_nom(r["nom"]) == _normaliser_nom(nom)]
    if meme_nom:
        r = meme_nom[0]
        return jsonify({"erreur": f"La route « {r['nom']} » existe déjà (n° {r['id']}) : "
                                  "ajoute plutôt ce point à cette route.",
                        "route": {"id": r["id"], "nom": r["nom"]}}), 409

    doc = {"nom": nom, "description": str(d.get("description") or "").strip()[:200],
           "points": [{"lat": base["lat"], "lon": base["lon"], "ts": base["ts"], "device": base["device"]}],
           "cree_par": base["device"], "cree_le": base["ts"]}
    if "contexte" in base:
        doc["contexte_creation"] = base["contexte"]
    try:
        numero = stockage.creer_route(doc, routes.prochain_numero_minimum())
    except stockage.StockageIndisponible as e:
        return jsonify({"erreur": str(e)}), 503
    routes.invalider_cache()
    return jsonify({"ok": True, "route": {"id": numero, "nom": nom, "points": 1}}), 201


@bp.post("/routes/<int:numero>/points")
def ajouter_point(numero):
    """Ajoute le point où l'on est au tracé d'une route créée sur le terrain."""
    if not cle_ok():
        return jsonify({"erreur": "clé invalide"}), 401
    if numero < routes.prochain_numero_minimum():
        return jsonify({"erreur": "Les 10 routes de départ ne se modifient pas."}), 403
    d = request.get_json(silent=True) or {}
    try:
        base = base_commune(d)
    except (KeyError, ValueError, TypeError):
        return jsonify({"erreur": "champs requis : ts, lat, lon"}), 400
    route = next((r for r in routes.liste(rafraichir=True) if r["id"] == numero), None)
    if route is None:
        return jsonify({"erreur": f"Route {numero} introuvable."}), 404
    distance = routes.distance_trace_m(base["lat"], base["lon"], route["points"])
    if distance > config.DISTANCE_MAX_AJOUT_POINT_M:
        return jsonify({"erreur": f"Ce point est à {round(distance)} m de la route {numero} : "
                                  "c'est sans doute une autre route, crée-la plutôt."}), 400
    if len(route["points"]) >= config.MAX_POINTS_ROUTE:
        return jsonify({"erreur": f"La route {numero} a déjà {config.MAX_POINTS_ROUTE} points."}), 400

    nouveau = {"lat": base["lat"], "lon": base["lon"], "ts": base["ts"], "device": base["device"]}
    ajoute = {"fait": False}

    def transformer(points):
        deja_la = any(
            routes.distance_trace_m(nouveau["lat"], nouveau["lon"], [{"latitude": p["lat"], "longitude": p["lon"]}])
            < config.DISTANCE_MIN_ENTRE_POINTS_M for p in points)
        if deja_la:
            return points  # un point existe déjà à cet endroit
        ajoute["fait"] = True
        return routes.inserer_point(points, nouveau)

    try:
        maj = stockage.modifier_points_route(numero, transformer)
    except stockage.StockageIndisponible as e:
        return jsonify({"erreur": str(e)}), 503
    if maj is None:
        return jsonify({"erreur": f"Route {numero} introuvable."}), 404
    routes.invalider_cache()
    return jsonify({"ok": True, "ajoute": ajoute["fait"],
                    "route": {"id": numero, "nom": maj["nom"], "points": len(maj["points"])}})


@bp.get("/signalement/types")
def liste_types():
    return jsonify(catalogue())


@bp.get("/feries")
def liste_feries():
    return jsonify({"feries": prevision.charger_feries().to_dict("records")})
