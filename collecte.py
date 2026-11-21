"""Collecte : reçoit les positions GPS et les signalements de l'app.

Routes (branchées dans app.py) :
    POST /collecte      points GPS, par lots de 500 maximum
    POST /signalement   accident / travaux / evenement

Protection simple (OBLIGATOIRE une fois en ligne) :
    définis la variable d'environnement TRAFIC_CLE ; l'app doit alors envoyer
    l'en-tête HTTP  X-Cle: <la même valeur>.
Les données vont dans Firestore en ligne, dans SQLite en local (voir stockage.py).
"""
import hmac
import os
from datetime import datetime

from flask import Blueprint, jsonify, request

import config
import stockage

bp = Blueprint("collecte", __name__)
TYPES_SIGNALEMENT = {"accident", "travaux", "evenement"}


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
    try:
        type_ = str(d["type"]).lower()
        if type_ not in TYPES_SIGNALEMENT or not ts_valide(d["ts"]):
            raise ValueError
        lat, lon = float(d["lat"]), float(d["lon"])
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError
    except (KeyError, ValueError, TypeError):
        return jsonify({"erreur": "champs requis : ts, lat, lon, type "
                                  f"({'/'.join(sorted(TYPES_SIGNALEMENT))})"}), 400
    try:
        stockage.ajouter_signalement({"device": str(d.get("device", "?"))[:64],
                                      "ts": str(d["ts"]), "lat": lat, "lon": lon, "type": type_})
    except stockage.StockageIndisponible as e:
        return jsonify({"erreur": str(e)}), 503
    return jsonify({"ok": True})
