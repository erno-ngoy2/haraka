"""Entraîne, évalue et (si c'est mieux) publie le modèle model_trafic.joblib.

Comme en production :
  1. Découpage CHRONOLOGIQUE : on apprend sur les jours anciens, on teste sur les 20 %
     de jours les plus récents (comme dans la vraie vie : on prédit l'avenir).
  2. Comparaison avec deux méthodes simples (« références ») :
       - classe la plus fréquente : prédit toujours le niveau le plus courant ;
       - habitude : le niveau le plus fréquent pour cette zone, cette heure, ce type de jour.
     Un modèle qui ne bat pas ces références n'apporte rien.
  3. Comparaison avec le modèle actuellement publié, sur les jours qu'il n'a jamais vus.
  4. Garde-fous : assez de lignes, assez de jours, pas de recul de qualité.
     Sinon le modèle publié n'est PAS remplacé (code de sortie 2).
  5. Si c'est accepté : réentraînement sur TOUTES les données, sauvegarde avec ses
     informations (version, période, scores, zones connues), copie de l'ancien modèle
     dans model_trafic.precedent.joblib, rapport dans rapports/.

Usage :
    python train.py            entraîne et publie si les garde-fous sont respectés
    python train.py --essai    évalue seulement, ne touche pas au modèle
    python train.py --forcer   publie même si un garde-fou bloque (à éviter)
"""
import argparse
import json
import platform
import shutil
import sys
from datetime import datetime, timezone

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, f1_score, recall_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

import config
import prevision


def lire_info_source():
    if config.TRAFIC_INFO.exists():
        return json.loads(config.TRAFIC_INFO.read_text(encoding="utf-8"))
    return {"source": "inconnue"}


def fabriquer_modele():
    pre = ColumnTransformer(
        [("cat", OneHotEncoder(handle_unknown="ignore"), config.CATEGORIES)],
        remainder="passthrough")
    return Pipeline([
        ("pre", pre),
        ("rf", RandomForestClassifier(
            n_estimators=config.N_ARBRES, min_samples_leaf=config.FEUILLE_MIN,
            class_weight="balanced", random_state=42, n_jobs=-1)),
    ])


def decouper(df, chrono):
    if chrono:
        jours = sorted(df["date"].unique())
        n_test = max(1, round(len(jours) * config.PART_TEST))
        jours_test = set(jours[-n_test:])
        test = df[df["date"].isin(jours_test)]
        train = df[~df["date"].isin(jours_test)]
        return train, test, f"chronologique : {len(jours) - n_test} jours pour apprendre, " \
                            f"{n_test} jours les plus récents pour tester ({min(jours_test)} -> {max(jours_test)})"
    try:
        train, test = train_test_split(df, test_size=config.PART_TEST, random_state=42,
                                       stratify=df[config.CIBLE])
    except ValueError:
        train, test = train_test_split(df, test_size=config.PART_TEST, random_state=42)
    return train, test, "aléatoire (pas de colonne 'date' : données de test)"


def mesurer(vrai, pred):
    vrai, pred = list(vrai), list(pred)
    r_emb = recall_score(vrai, pred, labels=["embouteillage"], average=None, zero_division=0)
    return {
        "lignes": len(vrai),
        "exactitude": round(float(accuracy_score(vrai, pred)), 3),
        "f1_macro": round(float(f1_score(vrai, pred, labels=config.NIVEAUX, average="macro",
                                         zero_division=0)), 3),
        "rappel_embouteillage": round(float(r_emb[0]), 3) if "embouteillage" in vrai else None,
    }


def type_jour(df):
    return np.where((df["jour_semaine"] < 5) & (df["ferie"] == 0), "ouvre", "repos")


def reference_habitude(train, test):
    """Niveau le plus fréquent pour (zone, heure, type de jour), sinon (heure, type de jour),
    sinon le niveau le plus fréquent tout court."""
    t, s = train.copy(), test.copy()
    for d in (t, s):
        d["zone"] = d["x"].round(config.DECIMALES_ZONE).astype(str) + "_" + \
            d["y"].round(config.DECIMALES_ZONE).astype(str)
        d["tj"] = type_jour(d)
    majoritaire = t[config.CIBLE].mode()[0]
    fin = t.groupby(["zone", "heure", "tj"])[config.CIBLE].agg(lambda v: v.mode()[0]).to_dict()
    large = t.groupby(["heure", "tj"])[config.CIBLE].agg(lambda v: v.mode()[0]).to_dict()
    return [fin.get((z, h, j), large.get((h, j), majoritaire))
            for z, h, j in zip(s["zone"], s["heure"], s["tj"])]


def comparer_avec_publie(candidat, test, source):
    """Score de l'ancien et du nouveau modèle sur les jours que l'ancien n'a jamais vus."""
    if not config.MODELE.exists():
        return {"statut": "aucun modèle publié"}
    ancien = prevision.lire_paquet(config.MODELE)
    m = ancien["meta"]
    if m.get("source") != source or source not in ("reel", "simulation"):
        return {"statut": f"modèle publié d'une autre source ({m.get('source')}) : pas de comparaison",
                "version_publiee": m.get("version")}
    t = test[test["date"] > m["date_max"]] if m.get("date_max") else test
    if len(t) < config.MIN_LIGNES_COMPARAISON:
        return {"statut": f"pas assez de nouveaux jours pour comparer ({len(t)} lignes)",
                "version_publiee": m.get("version")}
    return {
        "statut": "comparé",
        "version_publiee": m.get("version"),
        "publie": mesurer(t[config.CIBLE], ancien["modele"].predict(t[config.FEATURES])),
        "nouveau": mesurer(t[config.CIBLE], candidat.predict(t[config.FEATURES])),
    }


def garde_fous(df, info, score, refs, comparaison):
    """Liste des raisons de NE PAS publier (vide = on peut publier)."""
    if info.get("source") not in ("reel", "simulation"):
        return []  # données de test : on publie (l'API l'indique avec donnees_test)
    raisons = []
    if len(df) < config.MIN_LIGNES_ENTRAINEMENT:
        raisons.append(f"seulement {len(df)} lignes (minimum {config.MIN_LIGNES_ENTRAINEMENT})")
    if df["date"].nunique() < config.MIN_JOURS_ENTRAINEMENT:
        raisons.append(f"seulement {df['date'].nunique()} jours (minimum {config.MIN_JOURS_ENTRAINEMENT})")
    if score["f1_macro"] <= refs["classe_frequente"]["f1_macro"]:
        raisons.append("ne bat pas la référence « classe la plus fréquente »")
    if comparaison.get("statut") == "comparé":
        recul = comparaison["publie"]["f1_macro"] - comparaison["nouveau"]["f1_macro"]
        if recul > config.TOLERANCE_REGRESSION:
            raisons.append(f"moins bon que le modèle publié (F1 macro -{recul:.3f})")
    return raisons


def importances(modele):
    try:
        noms = modele.named_steps["pre"].get_feature_names_out()
        poids = modele.named_steps["rf"].feature_importances_
        return {n.split("__")[-1]: round(float(p), 3)
                for n, p in sorted(zip(noms, poids), key=lambda t: -t[1])[:8]}
    except Exception:
        return {}


def zones_connues(df):
    d = config.DECIMALES_ZONE
    z = np.unique(np.column_stack([df["x"].round(d), df["y"].round(d)]), axis=0)
    return z.astype(np.float32)


def afficher(titre, s):
    rappel = "-" if s["rappel_embouteillage"] is None else f"{s['rappel_embouteillage']:.3f}"
    print(f"  {titre:<34} exactitude {s['exactitude']:.3f}   F1 macro {s['f1_macro']:.3f}   "
          f"embouteillages trouvés {rappel}")


def main():
    p = argparse.ArgumentParser(description="Entraîne et publie le modèle de trafic.")
    p.add_argument("--essai", action="store_true", help="évaluer seulement, ne rien publier")
    p.add_argument("--forcer", action="store_true", help="publier même si un garde-fou bloque")
    args = p.parse_args()

    if not config.TRAFIC_CSV.exists():
        sys.exit("trafic.csv introuvable. Lance d'abord aggregation.py "
                 "(ou generer_donnees_test.py pour tester).")
    df = pd.read_csv(config.TRAFIC_CSV, dtype={"date": str})
    info = lire_info_source()
    source = info.get("source", "inconnue")
    requises = config.FEATURES + [config.CIBLE]
    manquantes = [c for c in requises if c not in df.columns]
    if manquantes:
        sys.exit(f"Colonnes manquantes dans trafic.csv : {manquantes}")
    df = df.dropna(subset=requises)
    df = df[df[config.CIBLE].isin(config.NIVEAUX)].reset_index(drop=True)
    if len(df) < 50 or df[config.CIBLE].nunique() < 2:
        sys.exit(f"Pas assez de données ({len(df)} lignes, "
                 f"{df[config.CIBLE].nunique()} niveau(x)). Continue la collecte.")
    chrono = "date" in df.columns and df["date"].nunique() >= 2

    print(f"Données : {len(df)} lignes, source « {source} »"
          + (f", {df['date'].nunique()} jours ({df['date'].min()} -> {df['date'].max()})" if chrono else ""))
    print(df[config.CIBLE].value_counts().to_string())

    # 1. Évaluation honnête
    train, test, decoupage = decouper(df, chrono)
    print(f"\nDécoupage {decoupage}")
    print(f"Exemples : {len(train)} pour apprendre, {len(test)} pour tester")
    candidat = fabriquer_modele().fit(train[config.FEATURES], train[config.CIBLE])
    pred = candidat.predict(test[config.FEATURES])
    score = mesurer(test[config.CIBLE], pred)

    # 2. Références simples
    majoritaire = train[config.CIBLE].mode()[0]
    refs = {
        "classe_frequente": mesurer(test[config.CIBLE], [majoritaire] * len(test)),
        "habitude": mesurer(test[config.CIBLE], reference_habitude(train, test)),
    }
    print("\nRésultats sur la période de test :")
    afficher("Modèle", score)
    afficher("Référence : classe la plus fréquente", refs["classe_frequente"])
    afficher("Référence : habitude zone+heure", refs["habitude"])
    if score["f1_macro"] <= refs["habitude"]["f1_macro"]:
        print("  ATTENTION : le modèle ne fait pas mieux que l'habitude. Il faut plus de données "
              "ou de meilleures informations (heure précédente, type de route...).")
    print("\n" + classification_report(test[config.CIBLE], pred, labels=config.NIVEAUX, zero_division=0))

    # 3. Comparaison avec le modèle publié
    comparaison = comparer_avec_publie(candidat, test, source)
    if comparaison["statut"] == "comparé":
        print(f"Comparaison avec le modèle publié ({comparaison['version_publiee']}) "
              f"sur {comparaison['nouveau']['lignes']} lignes qu'il n'a jamais vues :")
        afficher("Modèle publié", comparaison["publie"])
        afficher("Nouveau modèle", comparaison["nouveau"])
    else:
        print("Comparaison avec le modèle publié :", comparaison["statut"])

    # 4. Garde-fous
    raisons = garde_fous(df, info, score, refs, comparaison)
    maintenant = datetime.now(timezone.utc)
    version = maintenant.strftime("%Y%m%d-%H%M%S")
    if args.essai:
        decision = "essai (rien publié)"
    elif raisons and not args.forcer:
        decision = "refusé"
    else:
        decision = "publié" + (" (forcé)" if raisons else "")

    rapport = {
        "version": version,
        "date": maintenant.isoformat(timespec="seconds"),
        "decision": decision,
        "raisons_refus": raisons,
        "source": source,
        "donnees": {k: v for k, v in info.items() if k != "source"},
        "decoupage": decoupage,
        "lignes_entrainement": len(train), "lignes_test": len(test),
        "test": score,
        "references": refs,
        "comparaison_modele_publie": comparaison,
        "matrice_confusion": {"ordre": config.NIVEAUX, "valeurs": confusion_matrix(
            test[config.CIBLE], pred, labels=config.NIVEAUX).tolist()},
        "informations_utiles": importances(candidat),
        "parametres": {"n_arbres": config.N_ARBRES, "feuille_min": config.FEUILLE_MIN},
        "versions": {"python": platform.python_version(), "scikit-learn": sklearn.__version__,
                     "pandas": pd.__version__, "numpy": np.__version__},
    }
    config.RAPPORTS.mkdir(exist_ok=True)
    chemin_rapport = config.RAPPORTS / f"{version}.json"
    chemin_rapport.write_text(json.dumps(rapport, ensure_ascii=False, indent=2), encoding="utf-8")

    if decision.startswith("refusé"):
        print("\nMODÈLE NON PUBLIÉ. Raisons :")
        for r in raisons:
            print("  -", r)
        print(f"Le modèle publié reste en place. Rapport : {chemin_rapport}")
        print("(python train.py --forcer pour publier quand même)")
        sys.exit(2)
    if args.essai:
        print(f"\nEssai terminé, rien n'est publié. Rapport : {chemin_rapport}")
        return

    # 5. Modèle final sur toutes les données
    final = fabriquer_modele().fit(df[config.FEATURES], df[config.CIBLE])
    meta = {
        "version": version,
        "entraine_le": rapport["date"],
        "source": source,
        "donnees_test": source != "reel",
        "lignes": len(df),
        "jours": int(df["date"].nunique()) if chrono else None,
        "date_min": str(df["date"].min()) if chrono else None,
        "date_max": str(df["date"].max()) if chrono else None,
        "test": score,
        "references": refs,
        "zones": zones_connues(df),
        "versions": rapport["versions"],
    }
    if config.MODELE.exists():
        shutil.copy2(config.MODELE, config.MODELE_PRECEDENT)
    tmp = config.MODELE.with_suffix(".tmp")
    # compress=3 : fichier environ 3 fois plus petit (il part sur GitHub puis Vercel)
    joblib.dump({"modele": final, "meta": meta}, tmp, compress=3)
    tmp.replace(config.MODELE)

    taille = config.MODELE.stat().st_size / 1e6
    print(f"\nModèle {version} PUBLIÉ : {config.MODELE.name} ({taille:.1f} Mo), "
          f"réentraîné sur les {len(df)} lignes. Rapport : {chemin_rapport.name}")
    if raisons:
        print("Publié malgré :", "; ".join(raisons))
    if meta["donnees_test"]:
        print("Attention : données de test -> l'API répondra \"donnees_test\": true.")
    if config.SIMULATION:
        print("Mode simulation : ce modèle reste dans simulation/, il n'est jamais mis en ligne.")
    else:
        print("Pour le mettre en ligne : git add model_trafic.joblib rapports && "
              "git commit -m \"Modèle " + version + "\" && git push")


if __name__ == "__main__":
    main()
