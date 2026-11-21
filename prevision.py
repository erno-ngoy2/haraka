"""Prévisions heure par heure + règles pour les jours fériés.

Usage direct (démo) : python prevision.py
Utilisé aussi par api.py.
"""
import functools
import math
from datetime import date

import joblib
import numpy as np
import pandas as pd

import config


def lire_paquet(chemin):
    """Le fichier contient {"modele": ..., "meta": {...}} (train.py). Un ancien fichier
    qui ne contient que le modèle est accepté et considéré comme un modèle de test."""
    obj = joblib.load(chemin)
    if isinstance(obj, dict) and "modele" in obj:
        return obj
    return {"modele": obj, "meta": {"version": "ancien", "source": "inconnue", "donnees_test": True}}


@functools.lru_cache(maxsize=1)
def charger_paquet():
    if not config.MODELE.exists():
        raise FileNotFoundError("model_trafic.joblib introuvable : lance train.py d'abord.")
    return lire_paquet(config.MODELE)


def charger_modele():
    return charger_paquet()["modele"]


def infos_modele():
    """Ce que l'API dit du modèle (sans la liste des zones)."""
    m = charger_paquet()["meta"]
    return {
        "version": m.get("version"),
        "entraine_le": m.get("entraine_le"),
        "donnees_test": bool(m.get("donnees_test", True)),
        "periode": [m.get("date_min"), m.get("date_max")],
        "f1_macro_test": (m.get("test") or {}).get("f1_macro"),
    }


def distance_zone_connue_m(x, y):
    """Distance (mètres) entre (x, y) et la zone la plus proche vue à l'entraînement.
    None si le modèle ne contient pas la liste des zones."""
    zones = charger_paquet()["meta"].get("zones")
    if zones is None or len(zones) == 0:
        return None
    z = np.asarray(zones, dtype=float)
    dlat = np.radians(z[:, 0] - x)
    dlon = np.radians(z[:, 1] - y) * math.cos(math.radians(x))
    return float(6_371_000 * np.sqrt(dlat ** 2 + dlon ** 2).min())


@functools.lru_cache(maxsize=1)
def charger_feries():
    """data/feries.csv, colonnes : jour, nom, effet, creneaux, zones.

    jour     : "MM-JJ" = chaque année (ex. 12-25) ; "AAAA-MM-JJ" = cette date seulement
               (ex. 2026-01-05, report d'un férié tombé un week-end). La date exacte gagne.
    effet    : embouteillage | calme | a_observer
    creneaux : heures d'embouteillage, "13-22" = de 13h00 à 22h00 (heures 13 à 21) ;
               plusieurs créneaux séparés par ";" (ex. "11-14;16-22"). Vide = toute la journée.
    zones    : description des lieux touchés (pour l'instant à titre d'information).
    """
    return pd.read_csv(config.FERIES_CSV, dtype=str, encoding="utf-8").fillna("")


def ligne_ferie(jour):
    """Ligne du fichier feries.csv pour ce jour (objet date), ou None."""
    f = charger_feries()
    for cle in (jour.isoformat(), jour.strftime("%m-%d")):
        ligne = f[f.jour == cle]
        if not ligne.empty:
            return ligne.iloc[0]
    return None


def heures_creneaux(texte):
    """ "11-14;16-22" -> {11, 12, 13, 16, ..., 21}. Vide -> toutes les heures."""
    if not texte.strip():
        return set(range(24))
    heures = set()
    for morceau in texte.split(";"):
        debut, fin = (int(v) for v in morceau.split("-"))
        heures |= set(range(debut, fin))
    return heures


def ajuster_ferie(res, jour):
    """Applique tes règles (feries.csv) par-dessus la prédiction du modèle.

    Utile tant que le modèle n'a pas vu assez de jours fériés pour les apprendre.
      - embouteillage : pendant les créneaux, niveau embouteillage (risque >= 0.8) ;
        en dehors, la prévision du modèle est gardée ;
      - calme : toute la journée fluide (risque <= 0.1).
    """
    l = ligne_ferie(jour)
    if l is None or l.effet not in ("embouteillage", "calme"):
        return res
    heures = heures_creneaux(l.creneaux) if l.effet == "embouteillage" else set(range(24))
    for r in res:
        if r["heure"] in heures:
            if l.effet == "embouteillage":
                r["risque_embouteillage"] = max(r["risque_embouteillage"], 0.8)
                r["niveau"] = "embouteillage"
            else:
                r["risque_embouteillage"] = min(r["risque_embouteillage"], 0.1)
                r["niveau"] = "fluide"
            r["ajuste_par_regle"] = True
    return res


def info_ferie(jour):
    """Pour l'API : {"nom", "effet", "zones"} ou None."""
    l = ligne_ferie(jour)
    return None if l is None else {"nom": l.nom, "effet": l.effet, "zones": l.zones}


def previsions(x, y, jour, heure_depart, nb_heures=6, pluie=0, circonstance="normal"):
    """Prévisions pour nb_heures à partir de heure_depart (jusqu'à minuit).

    jour : objet datetime.date. Le jour de la semaine et le férié en sont déduits.
    """
    if not 0 <= heure_depart <= 23:
        raise ValueError("heure_depart doit être entre 0 et 23")
    nb_heures = max(1, min(int(nb_heures), 24))
    heures = list(range(heure_depart, min(24, heure_depart + nb_heures)))

    l = ligne_ferie(jour)
    ferie, type_ferie = (1, l.effet) if l is not None else (0, "aucun")

    X = pd.DataFrame([{
        "x": x, "y": y, "heure": h, "jour_semaine": jour.weekday(),
        "ferie": ferie, "type_ferie": type_ferie,
        "pluie": int(pluie), "circonstance": circonstance,
    } for h in heures])[config.FEATURES]

    model = charger_modele()
    probas = model.predict_proba(X)
    classes = [str(c) for c in model.classes_]
    i_jam = classes.index("embouteillage") if "embouteillage" in classes else None

    res = []
    for h, p in zip(heures, probas):
        res.append({
            "heure": h,
            "niveau": classes[int(p.argmax())],
            "risque_embouteillage": round(float(p[i_jam]), 2) if i_jam is not None else 0.0,
            "probabilites": {c: round(float(v), 2) for c, v in zip(classes, p)},
            "ajuste_par_regle": False,
        })
    return ajuster_ferie(res, jour)


def meilleure_heure(res):
    """L'heure avec le plus faible risque d'embouteillage (la plus tôt en cas d'égalité)."""
    return min(res, key=lambda r: (r["risque_embouteillage"], r["heure"]))


if __name__ == "__main__":
    j = date(2026, 11, 23)  # un lundi
    res = previsions(config.LAT_CENTRE, config.LON_CENTRE, j, heure_depart=6, pluie=1)
    for r in res:
        print(r["heure"], "h", r["niveau"], r["risque_embouteillage"])
    print("Meilleure heure pour partir :", meilleure_heure(res)["heure"], "h")
