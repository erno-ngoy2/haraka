"""Entraîne le modèle à partir de trafic.csv et le sauvegarde dans model_trafic.joblib.

Usage : python train.py
"""
import sys

import joblib
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

import config


def main():
    if not config.TRAFIC_CSV.exists():
        sys.exit("trafic.csv introuvable. Lance d'abord aggregation.py "
                 "(ou generer_donnees_test.py pour tester).")

    df = pd.read_csv(config.TRAFIC_CSV)
    requises = config.FEATURES + [config.CIBLE]
    manquantes = [c for c in requises if c not in df.columns]
    if manquantes:
        sys.exit(f"Colonnes manquantes dans trafic.csv : {manquantes}")

    df = df.dropna(subset=requises)
    if len(df) < 50 or df[config.CIBLE].nunique() < 2:
        sys.exit(f"Pas assez de données ({len(df)} lignes, "
                 f"{df[config.CIBLE].nunique()} niveau(x)). Continue la collecte.")

    # Avec une colonne 'date' : on entraîne sur le passé et on teste sur le plus récent
    if "date" in df.columns:
        df = df.sort_values("date").reset_index(drop=True)
        coupe = int(len(df) * 0.8)
        train, test = df.iloc[:coupe], df.iloc[coupe:]
        print("Découpage chronologique : 80 % les plus anciens pour apprendre, "
              "20 % les plus récents pour tester.")
    else:
        try:
            train, test = train_test_split(df, test_size=0.2, random_state=42,
                                           stratify=df[config.CIBLE])
        except ValueError:
            train, test = train_test_split(df, test_size=0.2, random_state=42)
        print("Découpage aléatoire (pas de colonne 'date').")

    print(f"\nExemples : {len(train)} pour apprendre, {len(test)} pour tester")
    print(df[config.CIBLE].value_counts().to_string(), "\n")

    pre = ColumnTransformer(
        [("cat", OneHotEncoder(handle_unknown="ignore"), config.CATEGORIES)],
        remainder="passthrough")
    model = Pipeline([
        ("pre", pre),
        ("rf", RandomForestClassifier(
            n_estimators=config.N_ARBRES, min_samples_leaf=config.FEUILLE_MIN,
            class_weight="balanced", random_state=42, n_jobs=-1)),
    ])
    model.fit(train[config.FEATURES], train[config.CIBLE])

    pred = model.predict(test[config.FEATURES])
    print(classification_report(test[config.CIBLE], pred, zero_division=0))

    try:
        noms = model.named_steps["pre"].get_feature_names_out()
        poids = model.named_steps["rf"].feature_importances_
        top = sorted(zip(noms, poids), key=lambda t: -t[1])[:8]
        print("Informations les plus utilisées par le modèle :")
        for nom, p in top:
            print(f"  {nom.split('__')[-1]:<28} {p:.2f}")
    except Exception:
        pass

    # compress=3 : fichier environ 3 fois plus petit (il part sur GitHub puis Vercel)
    joblib.dump(model, config.MODELE, compress=3)
    taille = config.MODELE.stat().st_size / 1e6
    print(f"\nModèle sauvegardé : {config.MODELE.name} ({taille:.1f} Mo)")
    if config.MODELE_DONNEES_TEST:
        print("Rappel : config.MODELE_DONNEES_TEST = True, l'API signale un modèle de test. "
              "Passe-le à False seulement si trafic.csv vient de vraies données.")


if __name__ == "__main__":
    main()
