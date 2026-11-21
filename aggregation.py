"""Transforme les points GPS bruts (et tes observations manuelles) en trafic.csv.

Une ligne de trafic.csv = une zone (~100 m) à une heure d'une date donnée, avec :
  - le niveau de trafic (fluide / dense / embouteillage), CALCULÉ à partir des vitesses,
  - le contexte : heure, jour, férié, pluie, circonstance.

Le niveau vient du rapport  vitesse médiane mesurée / vitesse normale de la zone
(seuils dans config.py). Les lignes pas assez fiables (peu de points, un seul appareil)
sont écartées.

Usage : python aggregation.py        (à lancer une fois par jour, sur ton PC)
Avec firebase-cle.json présent, les données sont lues dans Firestore (celles collectées
par le serveur en ligne) ; sinon dans la base SQLite locale (voir stockage.py).
"""
import sys

import pandas as pd
import requests

import config
import stockage

COLONNES = ["date", "x", "y", "heure", "jour_semaine", "ferie", "type_ferie",
            "pluie", "circonstance", "niveau"]


def cle_zone(lat, lon):
    d = config.DECIMALES_ZONE
    return lat.round(d).astype(str) + "_" + lon.round(d).astype(str)


def charger_effets_feries():
    f = pd.read_csv(config.FERIES_CSV, dtype=str)
    return dict(zip(f.mois_jour, f.effet))


def ajouter_calendrier(df, effets):
    """Ajoute jour_semaine, ferie et type_ferie à partir de la colonne 'date'."""
    dates = pd.to_datetime(df["date"].astype(str))
    mj = dates.dt.strftime("%m-%d")
    df["jour_semaine"] = dates.dt.dayofweek
    df["ferie"] = mj.isin(effets).astype(int)
    df["type_ferie"] = mj.map(effets).fillna("aucun")
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
        print(f"Note : {manquants} heures sans mesure de pluie (jours très récents) -> pluie = 0.")
    m["date"] = m.t.dt.date
    m["heure"] = m.t.dt.hour
    m["pluie"] = (m.mm.fillna(0) >= config.SEUIL_PLUIE_MM).astype(int)
    return m[["date", "heure", "pluie"]]


def depuis_gps(effets):
    vide = pd.DataFrame(columns=COLONNES)
    print(f"Lecture des données ({stockage.type_stockage()})...")
    pts = stockage.lire_positions()
    sig = stockage.lire_signalements()
    if pts.empty:
        return vide
    # Points en véhicule et assez précis seulement
    pts = pts[(pts.en_vehicule.astype(int) == 1)
              & (pts.precision_m.astype(float) <= config.PRECISION_GPS_MAX_M)].copy()
    if pts.empty:
        return vide

    # Horodatage en heure locale
    pts["ts"] = pd.to_datetime(pts["ts"], utc=True, format="ISO8601").dt.tz_convert(config.FUSEAU)
    pts["date"] = pts.ts.dt.date
    pts["heure"] = pts.ts.dt.hour
    pts["zone"] = cle_zone(pts.lat, pts.lon)

    # Une ligne par zone + date + heure
    g = pts.groupby(["zone", "date", "heure"], as_index=False).agg(
        x=("lat", "mean"), y=("lon", "mean"),
        vitesse=("vitesse_kmh", "median"),
        n_points=("vitesse_kmh", "size"),
        n_appareils=("device", "nunique"))

    # Lignes fiables seulement
    g = g[(g.n_points >= config.MIN_POINTS) & (g.n_appareils >= config.MIN_APPAREILS)].copy()
    # Zones avec assez d'historique pour connaître leur "vitesse normale"
    g = g[g.groupby("zone")["zone"].transform("size") >= config.MIN_LIGNES_PAR_ZONE].copy()
    if g.empty:
        return vide

    # Niveau = vitesse mesurée / vitesse normale de la zone (85e percentile)
    libre = g.groupby("zone")["vitesse"].transform(lambda s: s.quantile(0.85))
    g["ratio"] = g["vitesse"] / libre.where(libre > 0)
    g["niveau"] = pd.cut(
        g["ratio"], [-float("inf"), config.SEUIL_EMBOUTEILLAGE, config.SEUIL_DENSE, float("inf")],
        labels=["embouteillage", "dense", "fluide"]).astype(str)
    g = g[g["ratio"].notna()].copy()

    # Calendrier
    g = ajouter_calendrier(g, effets)

    # Pluie
    m = meteo_pluie(g.x.mean(), g.y.mean(), str(g.date.min()), str(g.date.max()))
    if m is not None:
        g = g.merge(m, on=["date", "heure"], how="left")
        g["pluie"] = g["pluie"].fillna(0).astype(int)
    else:
        g["pluie"] = 0

    # Circonstances : un signalement vaut pour DUREE_SIGNALEMENT_H heures
    g["circonstance"] = "normal"
    if not sig.empty:
        sig["ts"] = pd.to_datetime(sig["ts"], utc=True, format="ISO8601").dt.tz_convert(config.FUSEAU)
        sig["zone"] = cle_zone(sig.lat, sig.lon)
        morceaux = []
        for k in range(config.DUREE_SIGNALEMENT_H):
            s = sig.copy()
            s["ts"] = s["ts"] + pd.Timedelta(hours=k)
            s["date"] = s.ts.dt.date
            s["heure"] = s.ts.dt.hour
            morceaux.append(s)
        sig = pd.concat(morceaux).drop_duplicates(["zone", "date", "heure"])
        cle = sig.set_index(["zone", "date", "heure"])["type"].to_dict()
        g["circonstance"] = [cle.get((z, d, h), "normal")
                             for z, d, h in zip(g.zone, g.date, g.heure)]

    return g[COLONNES]


def depuis_observations(effets):
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
    o["date"] = pd.to_datetime(o["date"]).dt.date
    o["pluie"] = o["pluie"].fillna(0).astype(int)
    o["circonstance"] = o["circonstance"].fillna("normal")
    o.loc[~o["circonstance"].isin(config.CIRCONSTANCES), "circonstance"] = "normal"
    o = ajouter_calendrier(o, effets)
    return o[COLONNES]


def main():
    effets = charger_effets_feries()
    gps = depuis_gps(effets)
    obs = depuis_observations(effets)
    print(f"Lignes issues du GPS : {len(gps)} | issues des observations manuelles : {len(obs)}")

    morceaux = [d for d in (gps, obs) if not d.empty]
    if not morceaux:
        sys.exit("Aucune ligne exploitable pour l'instant : continue la collecte "
                 "(il faut des zones avec assez de points et d'appareils).")

    df = pd.concat(morceaux, ignore_index=True)[COLONNES]
    df.to_csv(config.TRAFIC_CSV, index=False)
    print(f"{len(df)} lignes écrites dans {config.TRAFIC_CSV.name}")
    print(df["niveau"].value_counts())


if __name__ == "__main__":
    main()
