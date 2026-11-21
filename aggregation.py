"""Transforme les points GPS bruts (et tes observations manuelles) en trafic.csv.

Étapes (à lancer une fois par jour, sur ton PC) :
  1. Lit les positions brutes (Firestore en ligne, ou SQLite en local : voir stockage.py).
  2. Les résume par zone (~100 m) + date + heure : vitesse médiane, nombre de points,
     nombre d'appareils, pluie, circonstance. Aucun identifiant d'appareil n'est gardé.
  3. Ajoute ce résumé à l'HISTORIQUE DURABLE data/agregats.csv :
       - les FENETRE_RECALCUL_JOURS derniers jours sont recalculés (envois en retard),
       - les jours plus anciens déjà présents ne bougent plus.
  4. Fabrique trafic.csv à partir de TOUT l'historique : le niveau (fluide / dense /
     embouteillage) est CALCULÉ avec  vitesse médiane / vitesse normale de la zone.
  5. Supprime les positions brutes de plus de RETENTION_BRUTES_JOURS jours (vie privée),
     seulement une fois l'historique bien enregistré.

Usage :
    python aggregation.py               (normal)
    python aggregation.py --sans-purge  (ne supprime rien dans la base)
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import requests

import config
import prevision
import stockage

COLONNES = ["date", "x", "y", "heure", "jour_semaine", "ferie", "type_ferie",
            "pluie", "circonstance", "niveau"]
COLONNES_AGREGATS = ["date", "zone", "heure", "x", "y", "vitesse", "n_points",
                     "n_appareils", "pluie", "circonstance"]


def cle_zone(lat, lon):
    d = config.DECIMALES_ZONE
    return lat.round(d).astype(str) + "_" + lon.round(d).astype(str)


def aujourdhui_local():
    return datetime.now(ZoneInfo(config.FUSEAU)).date()


def ecrire_csv_sur(df, chemin):
    """Écrit d'abord un fichier temporaire puis le renomme : jamais de fichier à moitié écrit."""
    tmp = chemin.with_suffix(chemin.suffix + ".tmp")
    df.to_csv(tmp, index=False)
    os.replace(tmp, chemin)


def ajouter_calendrier(df):
    """Ajoute jour_semaine, ferie et type_ferie à partir de la colonne 'date'.
    Les jours fériés (et leurs reports) viennent de data/feries.csv, lus comme pour
    les prévisions (prevision.ligne_ferie) : mêmes règles partout."""
    dates = pd.to_datetime(df["date"].astype(str))
    effets = {}
    for d in dates.dt.date.unique():
        l = prevision.ligne_ferie(d)
        effets[d] = None if l is None else l.effet
    effet = dates.dt.date.map(effets)
    df["jour_semaine"] = dates.dt.day_of_week
    df["ferie"] = effet.notna().astype(int).values
    df["type_ferie"] = effet.fillna("aucun").values
    return df


def meteo_pluie(lat, lon, debut, fin):
    """Pluie heure par heure (Open-Meteo, gratuit, sans clé). None si indisponible."""
    try:
        r = requests.get(
            "https://archive-api.open-meteo.com/v1/archive",
            params={"latitude": lat, "longitude": lon, "start_date": debut,
                    "end_date": fin, "hourly": "precipitation",
                    "timezone": config.FUSEAU},
            timeout=30)
        r.raise_for_status()
        h = r.json()["hourly"]
    except Exception as e:  # réseau, quota, format inattendu...
        print(f"ATTENTION : météo indisponible ({e}). 'pluie' mis à 0 pour ce passage.")
        return None
    m = pd.DataFrame({"t": pd.to_datetime(h["time"]), "mm": h["precipitation"]})
    manquants = int(m.mm.isna().sum())
    if manquants:
        print(f"Note : {manquants} heures sans mesure de pluie (jours très récents) -> pluie = 0 "
              "pour l'instant, corrigé aux prochains passages.")
    m["date"] = m.t.dt.strftime("%Y-%m-%d")
    m["heure"] = m.t.dt.hour
    m["pluie"] = (m.mm.fillna(0) >= config.SEUIL_PLUIE_MM).astype(int)
    return m[["date", "heure", "pluie"]]


# --- 1 et 2 : résumer les positions brutes -----------------------------------

def resumer_brutes():
    """Positions brutes -> une ligne par zone + date + heure (sans la pluie)."""
    print(f"Lecture des positions brutes ({stockage.type_stockage()})...")
    pts = stockage.lire_positions()
    sig = stockage.lire_signalements()
    vide = pd.DataFrame(columns=[c for c in COLONNES_AGREGATS if c != "pluie"])
    print(f"  {len(pts)} positions, {len(sig)} signalements")
    if pts.empty:
        return vide

    # Points en véhicule et assez précis seulement
    pts = pts[(pts.en_vehicule.astype(int) == 1)
              & (pts.precision_m.astype(float) <= config.PRECISION_GPS_MAX_M)].copy()
    if pts.empty:
        return vide

    # Horodatage en heure locale
    pts["ts"] = pd.to_datetime(pts["ts"], utc=True, format="ISO8601").dt.tz_convert(config.FUSEAU)
    pts["date"] = pts.ts.dt.strftime("%Y-%m-%d")
    pts["heure"] = pts.ts.dt.hour
    pts["zone"] = cle_zone(pts.lat.astype(float), pts.lon.astype(float))

    g = pts.groupby(["zone", "date", "heure"], as_index=False).agg(
        x=("lat", "mean"), y=("lon", "mean"),
        vitesse=("vitesse_kmh", "median"),
        n_points=("vitesse_kmh", "size"),
        n_appareils=("device", "nunique"))

    # Circonstances : un signalement vaut pour DUREE_SIGNALEMENT_H heures
    g["circonstance"] = "normal"
    if not sig.empty:
        sig["ts"] = pd.to_datetime(sig["ts"], utc=True, format="ISO8601").dt.tz_convert(config.FUSEAU)
        sig["zone"] = cle_zone(sig.lat.astype(float), sig.lon.astype(float))
        morceaux = []
        for k in range(config.DUREE_SIGNALEMENT_H):
            s = sig.copy()
            s["ts"] = s["ts"] + pd.Timedelta(hours=k)
            s["date"] = s.ts.dt.strftime("%Y-%m-%d")
            s["heure"] = s.ts.dt.hour
            morceaux.append(s)
        sig = pd.concat(morceaux).drop_duplicates(["zone", "date", "heure"])
        cle = sig.set_index(["zone", "date", "heure"])["type"].to_dict()
        g["circonstance"] = [cle.get((z, d, h), "normal")
                             for z, d, h in zip(g.zone, g.date, g.heure)]
    return g


# --- 3 : historique durable --------------------------------------------------

def charger_agregats():
    if not config.AGREGATS_CSV.exists():
        return pd.DataFrame(columns=COLONNES_AGREGATS)
    return pd.read_csv(config.AGREGATS_CSV, dtype={"date": str, "zone": str})


def fusionner(anciens, nouveaux, aujourdhui):
    """Récents : recalculés. Anciens déjà présents : figés. Anciens absents : ajoutés."""
    limite = (aujourdhui - timedelta(days=config.FENETRE_RECALCUL_JOURS)).isoformat()
    deja = set(anciens["date"])
    a_ecrire = nouveaux[(nouveaux["date"] >= limite) | (~nouveaux["date"].isin(deja))].copy()
    gardes = anciens[~anciens["date"].isin(set(a_ecrire["date"]))]
    return a_ecrire, gardes


def ajouter_pluie(df):
    if df.empty:
        df["pluie"] = pd.Series(dtype=int)
        return df
    m = meteo_pluie(df.x.astype(float).mean(), df.y.astype(float).mean(),
                    df.date.min(), df.date.max())
    if m is None:
        df["pluie"] = 0
        return df
    df = df.merge(m, on=["date", "heure"], how="left")
    df["pluie"] = df["pluie"].fillna(0).astype(int)
    return df


# --- 4 : trafic.csv ------------------------------------------------------------

def construire_depuis_agregats(ag):
    """Historique -> lignes d'entraînement avec le niveau calculé."""
    vide = pd.DataFrame(columns=COLONNES)
    if ag.empty:
        return vide
    g = ag[(ag.n_points >= config.MIN_POINTS) & (ag.n_appareils >= config.MIN_APPAREILS)].copy()
    # Zones avec assez d'historique pour connaître leur "vitesse normale"
    g = g[g.groupby("zone")["zone"].transform("size") >= config.MIN_LIGNES_PAR_ZONE].copy()
    if g.empty:
        return vide

    # Niveau = vitesse mesurée / vitesse normale de la zone (85e percentile de son historique)
    libre = g.groupby("zone")["vitesse"].transform(lambda s: s.quantile(0.85))
    g["ratio"] = g["vitesse"] / libre.where(libre > 0)
    g = g[g["ratio"].notna()].copy()
    g["niveau"] = pd.cut(
        g["ratio"], [-float("inf"), config.SEUIL_EMBOUTEILLAGE, config.SEUIL_DENSE, float("inf")],
        labels=["embouteillage", "dense", "fluide"]).astype(str)
    return ajouter_calendrier(g)[COLONNES]


def depuis_observations():
    """Observations faites à la main (data/observations_manuelles.csv)."""
    vide = pd.DataFrame(columns=COLONNES)
    if not config.OBSERVATIONS_CSV.exists():
        return vide
    o = pd.read_csv(config.OBSERVATIONS_CSV)
    if o.empty:
        return vide
    o = o[o["niveau"].isin(config.NIVEAUX)].copy()
    if o.empty:
        return vide
    o["date"] = pd.to_datetime(o["date"]).dt.strftime("%Y-%m-%d")
    o["pluie"] = o["pluie"].fillna(0).astype(int)
    o["circonstance"] = o["circonstance"].fillna("normal")
    o.loc[~o["circonstance"].isin(config.CIRCONSTANCES), "circonstance"] = "normal"
    o = ajouter_calendrier(o)
    return o[COLONNES]


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--sans-purge", action="store_true",
                   help="ne supprime pas les positions brutes anciennes")
    args = p.parse_args()
    if config.RETENTION_BRUTES_JOURS <= config.FENETRE_RECALCUL_JOURS:
        sys.exit("config : RETENTION_BRUTES_JOURS doit être plus grand que FENETRE_RECALCUL_JOURS.")

    aujourdhui = aujourdhui_local()

    # 1-3 : historique
    nouveaux = resumer_brutes()
    anciens = charger_agregats()
    a_ecrire, gardes = fusionner(anciens, nouveaux, aujourdhui)
    a_ecrire = ajouter_pluie(a_ecrire)
    historique = (pd.concat([gardes, a_ecrire[COLONNES_AGREGATS]], ignore_index=True)
                  .sort_values(["date", "heure", "zone"]).reset_index(drop=True))
    if not historique.empty:
        ecrire_csv_sur(historique, config.AGREGATS_CSV)
    jours_maj = sorted(set(a_ecrire["date"]))
    print(f"Historique : {historique['date'].nunique()} jours, {len(historique)} lignes "
          f"({len(jours_maj)} jour(s) ajouté(s) ou recalculé(s)"
          + (f" : {jours_maj[0]} -> {jours_maj[-1]})" if jours_maj else ")"))

    # 4 : trafic.csv
    gps = construire_depuis_agregats(historique)
    obs = depuis_observations()
    print(f"Lignes fiables issues du GPS : {len(gps)} | observations manuelles : {len(obs)}")
    morceaux = [d for d in (gps, obs) if not d.empty]
    if morceaux:
        df = pd.concat(morceaux, ignore_index=True)[COLONNES]
        ecrire_csv_sur(df, config.TRAFIC_CSV)
        config.TRAFIC_INFO.write_text(json.dumps({
            "source": "simulation" if config.SIMULATION else "reel",
            "genere_le": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "lignes": len(df), "lignes_gps": len(gps), "lignes_observations": len(obs),
            "jours": int(df["date"].nunique()),
            "date_min": str(df["date"].min()), "date_max": str(df["date"].max()),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{len(df)} lignes écrites dans {config.TRAFIC_CSV.name} "
              f"({df['date'].nunique()} jours, {df['date'].min()} -> {df['date'].max()})")
        print(df["niveau"].value_counts().to_string())
    else:
        print("Pas encore de ligne exploitable : continue la collecte "
              "(il faut des zones avec assez de points et d'appareils).")

    # 5 : vie privée (seulement si l'historique couvre bien les données brutes)
    if args.sans_purge:
        print("Purge désactivée (--sans-purge).")
    elif nouveaux.empty or config.AGREGATS_CSV.exists():
        limite = datetime.now(timezone.utc) - timedelta(days=config.RETENTION_BRUTES_JOURS)
        n_pos, n_sig = stockage.purger_avant(limite)
        print(f"Vie privée : données brutes d'avant le {limite:%Y-%m-%d} supprimées "
              f"({n_pos} envoi(s)/position(s), {n_sig} signalement(s)).")

    if not morceaux:
        sys.exit(1)


if __name__ == "__main__":
    main()
