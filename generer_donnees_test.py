"""Génère des données ARTIFICIELLES pour tester le pipeline (trafic.csv).

Ces données imitent la réalité avec des règles simples. Elles ne servent qu'à
vérifier que le code fonctionne. Le modèle entraîné dessus n'a AUCUNE valeur réelle :
remplace trafic.csv par celui produit par aggregation.py dès que tu as de vraies données.

Usage : python generer_donnees_test.py
"""
import numpy as np
import pandas as pd

import config

rng = np.random.default_rng(42)
N = 30000

df = pd.DataFrame({
    "x": rng.uniform(config.LAT_CENTRE - 0.06, config.LAT_CENTRE + 0.06, N),
    "y": rng.uniform(config.LON_CENTRE - 0.06, config.LON_CENTRE + 0.06, N),
    "heure": rng.integers(0, 24, N),
    "jour_semaine": rng.integers(0, 7, N),          # 0 = lundi ... 6 = dimanche
    "ferie": rng.choice([0, 1], N, p=[0.93, 0.07]),
    "pluie": rng.choice([0, 1], N, p=[0.75, 0.25]),
    "circonstance": rng.choice(config.CIRCONSTANCES, N, p=[0.85, 0.04, 0.06, 0.05]),
})

# type de férié : seulement quand ferie = 1
df["type_ferie"] = "aucun"
est_ferie = df.ferie == 1
df.loc[est_ferie, "type_ferie"] = rng.choice(
    ["embouteillage", "calme", "a_observer"], est_ferie.sum(), p=[0.3, 0.2, 0.5])

# --- Règles qui fabriquent le niveau (uniquement pour le test) ---------------
dist = np.sqrt((df.x - config.LAT_CENTRE) ** 2 + (df.y - config.LON_CENTRE) ** 2)
proximite_centre = 1 - dist / dist.max()
pointe = (df.heure.between(7, 9) | df.heure.between(16, 19)).astype(int)
jour_travaille = ((df.jour_semaine < 5) & (df.ferie == 0)).astype(int)
effet_circ = df.circonstance.map(
    {"normal": 0, "accident": 2.5, "travaux": 1.5, "evenement": 1.5})
ferie_chargé = ((df.type_ferie == "embouteillage") & df.heure.between(17, 20)).astype(int)

score = (
    2.2 * pointe * jour_travaille
    + 0.8 * pointe * (1 - jour_travaille)
    + 2.0 * ferie_chargé
    + 1.5 * proximite_centre
    + 1.0 * df.pluie
    + effet_circ
    + rng.normal(0, 0.5, N)
)

df["niveau"] = pd.cut(
    score, bins=[-np.inf, 1.8, 3.6, np.inf], labels=config.NIVEAUX).astype(str)

df.to_csv(config.TRAFIC_CSV, index=False)
print(f"{len(df)} lignes écrites dans {config.TRAFIC_CSV.name}")
print(df.niveau.value_counts(normalize=True).round(2))
