"""Simule des semaines de collecte GPS pour tester le pipeline comme en production.

Écrit des positions et des signalements INVENTÉS dans simulation/trafic.db, exactement
au format envoyé par l'app : quelques axes de la ville, des téléphones anonymes, des
heures de pointe, des week-ends plus calmes, quelques accidents.

Usage (PowerShell, depuis trafic-ia) :
    $env:HARAKA_SIMULATION = "1"
    venv\\Scripts\\python simuler_gps.py --jours 35 --reset   # 35 jours jusqu'à hier
    venv\\Scripts\\python aggregation.py
    venv\\Scripts\\python train.py
    venv\\Scripts\\python app.py                               # sert le modèle simulé
    Remove-Item Env:HARAKA_SIMULATION                         # revenir au mode normal

Options :
    --jours N       nombre de jours simulés (défaut 35)
    --decalage K    s'arrêter K jours avant hier (pour simuler ensuite « les jours suivants »)
    --reset         vider la base de simulation avant
"""
import argparse
import sys
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import numpy as np

import config
import stockage

if not config.SIMULATION:
    sys.exit("Sécurité : lance d'abord  $env:HARAKA_SIMULATION = \"1\"  (sinon les fausses "
             "positions iraient dans la vraie base).")

AXES = 6                 # axes (routes) simulés
CASES_PAR_AXE = 5        # zones de ~100 m par axe
APPAREILS = 12           # téléphones
HEURES = range(5, 23)    # heures de circulation


def facteur_vitesse(jour, heure, rng):
    """Vitesse mesurée / vitesse normale selon le moment (plus petit = plus bouché)."""
    ouvre = jour.weekday() < 5
    if ouvre and (7 <= heure <= 9 or 16 <= heure <= 19):
        return rng.uniform(0.2, 0.55)      # pointe
    if ouvre and 10 <= heure <= 15:
        return rng.uniform(0.5, 0.85)      # journée chargée
    if not ouvre and 10 <= heure <= 18:
        return rng.uniform(0.6, 0.95)      # week-end
    return rng.uniform(0.8, 1.0)           # tôt / tard


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--jours", type=int, default=35)
    p.add_argument("--decalage", type=int, default=0)
    p.add_argument("--reset", action="store_true")
    args = p.parse_args()

    if args.reset:
        for f in (config.DB, config.AGREGATS_CSV, config.TRAFIC_CSV, config.TRAFIC_INFO,
                  config.MODELE, config.MODELE_PRECEDENT):
            f.unlink(missing_ok=True)
        if config.RAPPORTS.exists():
            for r in config.RAPPORTS.glob("*.json"):
                r.unlink()
        print("Simulation remise à zéro.")

    fuseau = ZoneInfo(config.FUSEAU)
    hier = datetime.now(fuseau).date() - timedelta(days=1 + args.decalage)
    debut = hier - timedelta(days=args.jours - 1)
    rng = np.random.default_rng(int(debut.strftime("%Y%m%d")))

    # Zones : des cases de 0,001° le long de quelques axes partant du centre
    pas = 10 ** -config.DECIMALES_ZONE
    lat0 = round(config.LAT_CENTRE, config.DECIMALES_ZONE)
    lon0 = round(config.LON_CENTRE, config.DECIMALES_ZONE)
    directions = [(1, 0), (0, 1), (-1, 0), (0, -1), (1, 1), (-1, 1)][:AXES]
    zones = [(lat0 + dl * (k + 2) * pas * 3, lon0 + dn * (k + 2) * pas * 3)
             for dl, dn in directions for k in range(CASES_PAR_AXE)]
    v_normale = rng.uniform(30, 60, len(zones))                      # km/h, route libre
    centrale = np.array([1.0 if i % CASES_PAR_AXE < 2 else 0.0 for i in range(len(zones))])
    appareils = [f"simu-{i:02d}" for i in range(APPAREILS)]

    total = 0
    n_accidents = 0
    jour = debut
    while jour <= hier:
        points = {a: [] for a in appareils}
        # 0 à 2 accidents par jour, chacun bloque une zone pendant 2 heures
        accidents = {(int(rng.integers(len(zones))), int(rng.choice(list(HEURES))))
                     for _ in range(rng.poisson(0.7))}
        for zi, h in accidents:
            ts = datetime.combine(jour, time(h, int(rng.integers(0, 20))), fuseau)
            stockage.ajouter_signalement({
                "device": str(rng.choice(appareils)),
                "ts": ts.astimezone(ZoneInfo("UTC")).isoformat().replace("+00:00", "Z"),
                "lat": zones[zi][0], "lon": zones[zi][1], "type": "accident"})
            n_accidents += 1
        for h in HEURES:
            for zi, (lat, lon) in enumerate(zones):
                f = facteur_vitesse(jour, h, rng) * (1 - 0.25 * centrale[zi])
                if (zi, h) in accidents or (zi, h - 1) in accidents:
                    f = min(f, rng.uniform(0.1, 0.3))
                passants = rng.choice(appareils, size=int(rng.integers(2, 5)), replace=False)
                for _ in range(rng.poisson(16)):
                    minute, seconde = int(rng.integers(60)), int(rng.integers(60))
                    ts = datetime.combine(jour, time(h, minute, seconde), fuseau)
                    points[str(rng.choice(passants))].append({
                        "ts": ts.astimezone(ZoneInfo("UTC")).isoformat().replace("+00:00", "Z"),
                        "lat": lat + rng.uniform(-0.4, 0.4) * pas,
                        "lon": lon + rng.uniform(-0.4, 0.4) * pas,
                        "vitesse_kmh": float(np.clip(v_normale[zi] * f * rng.lognormal(0, 0.15), 0, 150)),
                        "precision_m": float(rng.uniform(4, 15)),
                        "en_vehicule": int(rng.random() > 0.03),
                    })
        for a, pts in points.items():
            stockage.ajouter_points(a, pts)
            total += len(pts)
        jour += timedelta(days=1)

    print(f"Simulé : {total} positions et {n_accidents} accidents, {len(zones)} zones, "
          f"{APPAREILS} appareils, du {debut} au {hier}.")
    print(f"Base : {config.DB}")


if __name__ == "__main__":
    main()
