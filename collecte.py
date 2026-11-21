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
    return jsonify(routes.charger())


@bp.get("/signalement/types")
def liste_types():
    return jsonify(catalogue())


@bp.get("/feries")
def liste_feries():
    return jsonify({"feries": prevision.charger_feries().to_dict("records")})
