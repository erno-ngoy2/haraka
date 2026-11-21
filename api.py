"""Prévisions pour l'app mobile (route branchée dans app.py).

POST /previsions
  corps JSON : {"x": -11.66, "y": 27.48, "heure_depart": 7,
                "date": "2026-11-23",        (optionnel, par défaut aujourd'hui)
                "pluie": 1,                  (optionnel, 0 ou 1)
                "circonstance": "normal",    (optionnel : normal/accident/travaux/evenement)
                "nb_heures": 6}              (optionnel)
  La réponse contient "donnees_test": true tant que le modèle vient de données artificielles.
"""
from datetime import date, datetime
from zoneinfo import ZoneInfo

from flask import Blueprint, jsonify, request

import config
import prevision

bp = Blueprint("previsions", __name__)


@bp.post("/previsions")
def route_previsions():
    d = request.get_json(silent=True) or {}
    try:
        x, y = float(d["x"]), float(d["y"])
        heure_depart = int(d["heure_depart"])
        if d.get("date"):
            jour = date.fromisoformat(str(d["date"]))
        else:
            jour = datetime.now(ZoneInfo(config.FUSEAU)).date()
        res = prevision.previsions(
            x, y, jour, heure_depart,
            nb_heures=int(d.get("nb_heures", 6)),
            pluie=1 if int(d.get("pluie", 0)) else 0,
            circonstance=str(d.get("circonstance", "normal")).lower())
    except FileNotFoundError as e:
        return jsonify({"erreur": str(e)}), 503
    except (KeyError, ValueError, TypeError):
        return jsonify({"erreur": "Champs requis : x, y, heure_depart (0-23). "
                                  "Optionnels : date (AAAA-MM-JJ), pluie, circonstance, nb_heures."}), 400

    return jsonify({
        "date": jour.isoformat(),
        "donnees_test": config.MODELE_DONNEES_TEST,
        "previsions": res,
        "meilleure_heure": prevision.meilleure_heure(res),
    })
