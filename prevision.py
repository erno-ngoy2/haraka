"""Prévisions heure par heure + règles pour les jours fériés.

Usage direct (démo) : python prevision.py
Utilisé aussi par api.py.
"""
import functools
from datetime import date

import joblib
import pandas as pd

import config


@functools.lru_cache(maxsize=1)
def charger_modele():
    if not config.MODELE.exists():
        raise FileNotFoundError("model_trafic.joblib introuvable : lance train.py d'abord.")
    return joblib.load(config.MODELE)


@functools.lru_cache(maxsize=1)
def charger_feries():
    return pd.read_csv(config.FERIES_CSV, dtype=str)


def ligne_ferie(jour):
    """Ligne du fichier feries.csv pour ce jour, ou None."""
    f = charger_feries()
    ligne = f[f.mois_jour == jour.strftime("%m-%d")]
    return None if ligne.empty else ligne.iloc[0]


def ajuster_ferie(res, jour):
    """Applique tes règles (feries.csv) par-dessus la prédiction du modèle.

    Utile tant que le modèle n'a pas vu assez de jours fériés pour les apprendre.
    """
    l = ligne_ferie(jour)
    if l is None or l.effet not in ("embouteillage", "calme"):
        return res
    debut = int(l.heure_debut) if pd.notna(l.heure_debut) else 0
    fin = int(l.heure_fin) if pd.notna(l.heure_fin) else 23
    for r in res:
        if debut <= r["heure"] <= fin:
            if l.effet == "embouteillage":
                r["risque_embouteillage"] = max(r["risque_embouteillage"], 0.8)
                r["niveau"] = "embouteillage"
            else:
                r["risque_embouteillage"] = min(r["risque_embouteillage"], 0.1)
                r["niveau"] = "fluide"
            r["ajuste_par_regle"] = True
    return res


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
