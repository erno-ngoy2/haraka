"""GET /carte : tout ce que l'écran Visualisation de l'app affiche, en un seul appel.

  - avancement de la CARTOGRAPHIE : combien de routes ont des points, sont dessinées, prêtes ;
  - avancement de l'ENTRAÎNEMENT DE L'IA : jours et observations collectés, modèle réel ou non ;
  - pour chaque route qui a des points : son avancement et, seulement à partir de
    POINTS_ETAT_TEMPS_REEL points (« plus de 5 »), son ÉTAT EN TEMPS RÉEL :
        1. une observation faite sur cette route depuis moins de FRAICHEUR_OBSERVATION_MIN
           minutes (source "observation") ;
        2. sinon la prévision du modèle pour l'heure actuelle (source "prevision").
    Avant ce seuil, pas d'état : on n'affiche rien plutôt qu'un « dense » inventé.

Paramètre facultatif : ?pluie=1 (l'app connaît la météo).
"""
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
from flask import Blueprint, jsonify, request

import config
import prevision
import routes
import stockage

bp = Blueprint("carte", __name__)
_cache = {"carte": {}, "ia": (0.0, None)}


def avancement_ia():
    """Pourcentage d'avancement de l'entraînement, en 3 parts égales :
       données (jours avec des données / MIN_JOURS_ENTRAINEMENT), volume (exemples / MIN_LIGNES_ENTRAINEMENT),
       modèle (100 % si un modèle entraîné sur de VRAIES données est en ligne).
       Exemples = signalements avec niveau + positions du Mode conduite / POSITIONS_PAR_EXEMPLE
       (une case de 100 m traversée ≈ quelques points GPS ≈ un exemple zone + heure)."""
    quand, valeur = _cache["ia"]
    if valeur is not None and time.monotonic() - quand < config.CACHE_AVANCEMENT_IA_S:
        return valeur
    try:
        obs = stockage.lire_observations()
        positions, jours_gps = stockage.stats_collecte()
    except stockage.StockageIndisponible:
        obs, positions, jours_gps = pd.DataFrame(), 0, 0
    signalements = len(obs)
    jours = 0
    if signalements:
        ts = pd.to_datetime(obs["ts"], utc=True, format="ISO8601", errors="coerce").dt.tz_convert(config.FUSEAU)
        jours = int(ts.dt.date.nunique())
    jours = max(jours, jours_gps)
    exemples = signalements + positions // config.POSITIONS_PAR_EXEMPLE
    try:
        modele = prevision.infos_modele()
    except FileNotFoundError:
        modele = None
    parts = {
        "donnees": round(min(jours / config.MIN_JOURS_ENTRAINEMENT, 1) * 100, 1),
        "volume": round(min(exemples / config.MIN_LIGNES_ENTRAINEMENT, 1) * 100, 1),
        "modele": 0 if (modele is None or modele["donnees_test"]) else 100,
    }
    valeur = {
        "pourcentage": round(sum(parts.values()) / 3, 1),
        "parts": parts,
        "observations": exemples, "objectif_observations": config.MIN_LIGNES_ENTRAINEMENT,
        "signalements": signalements, "positions_gps": positions,
        "jours": jours, "objectif_jours": config.MIN_JOURS_ENTRAINEMENT,
        "modele": modele,
    }
    _cache["ia"] = (time.monotonic(), valeur)
    return valeur


def _etats(liste, pluie):
    """État en temps réel des routes qui ont assez de points."""
    eligibles = [r for r in liste if r["etat_disponible"]]
    if not eligibles:
        return {}
    maintenant = datetime.now(timezone.utc)
    try:
        recentes = stockage.observations_recentes(maintenant - timedelta(minutes=config.FRAICHEUR_OBSERVATION_MIN))
    except stockage.StockageIndisponible:
        recentes = []
    derniere = {}
    for o in recentes:
        rid = o.get("route_id")
        if rid is None or not o.get("niveau"):
            continue
        if rid not in derniere or str(o["ts"]) > str(derniere[rid]["ts"]):
            derniere[rid] = o
    local = datetime.now(ZoneInfo(config.FUSEAU))
    etats = {}
    for r in eligibles:
        o = derniere.get(r["id"])
        if o is not None:
            age = (maintenant - datetime.fromisoformat(str(o["ts"]).replace("Z", "+00:00"))).total_seconds() / 60
            etats[r["id"]] = {"niveau": o["niveau"], "source": "observation", "il_y_a_min": max(0, round(age))}
            continue
        m = routes.milieu(r)
        try:
            p = prevision.previsions(m[0], m[1], local.date(), local.hour, 1, pluie)[0]
            etats[r["id"]] = {"niveau": p["niveau"], "source": "prevision",
                              "risque_embouteillage": p["risque_embouteillage"]}
        except FileNotFoundError:
            pass
    return etats


@bp.get("/carte")
def carte():
    pluie = 1 if request.args.get("pluie") in ("1", "true") else 0
    cle = pluie
    quand, valeur = _cache["carte"].get(cle, (0.0, None))
    if valeur is not None and time.monotonic() - quand < config.CACHE_CARTE_S:
        return jsonify(valeur)

    liste = routes.liste()
    etats = _etats(liste, pluie)
    n = len(liste)
    cartographie = {
        "routes_total": n,
        "routes_avec_points": sum(r["nb_points"] > 0 for r in liste),
        "routes_dessinees": sum(r["dessinee"] for r in liste),
        "routes_pretes": sum(r["prete"] for r in liste),
        "points_total": sum(r["nb_points"] for r in liste),
        "pourcentage": round(sum(min(r["nb_points"] / config.POINTS_ROUTE_PRETE, 1) for r in liste) / n * 100, 1) if n else 0,
    }
    try:
        donnees_test = prevision.infos_modele()["donnees_test"]
    except FileNotFoundError:
        donnees_test = True
    valeur = {
        "genere_le": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "heure": datetime.now(ZoneInfo(config.FUSEAU)).hour,
        "donnees_test": donnees_test,
        "seuils": {"dessinee": config.POINTS_ROUTE_DESSINEE, "etat": config.POINTS_ETAT_TEMPS_REEL,
                   "prete": config.POINTS_ROUTE_PRETE},
        "cartographie": cartographie,
        "ia": avancement_ia(),
        # seulement les routes qui ont des points (les autres : 0 point, pas d'état)
        "routes": [{"id": r["id"], "nb_points": r["nb_points"], "avancement": r["avancement"],
                    "dessinee": r["dessinee"], "prete": r["prete"], "etat": etats.get(r["id"])}
                   for r in liste if r["nb_points"] > 0],
    }
    _cache["carte"][cle] = (time.monotonic(), valeur)
    return jsonify(valeur)


def invalider_cache():
    _cache["carte"] = {}
