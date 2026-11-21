"""Teste le serveur (en local ou en ligne).

Usage :
    python test_api.py                                  (local : lance d'abord python app.py)
    python test_api.py https://ton-projet.vercel.app    (serveur en ligne)
Si TRAFIC_CLE est définie, elle est envoyée dans l'en-tête X-Cle.

Attention : écrit un point et un signalement "test-appareil" dans la base.
"""
import os
import sys
from datetime import datetime, timezone

import requests

import config

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5000").rstrip("/")
ENTETES = {"X-Cle": os.environ["TRAFIC_CLE"]} if os.environ.get("TRAFIC_CLE") else {}
maintenant = datetime.now(timezone.utc).isoformat()

print("Serveur testé :", BASE)
try:
    r = requests.get(f"{BASE}/sante", timeout=30)
    print("sante       ->", r.status_code, r.json())

    r = requests.post(f"{BASE}/collecte", headers=ENTETES, timeout=30, json={
        "device": "test-appareil",
        "points": [{"ts": maintenant, "lat": config.LAT_CENTRE, "lon": config.LON_CENTRE,
                    "vitesse_kmh": 25, "precision_m": 8}]})
    print("collecte    ->", r.status_code, r.json())

    r = requests.post(f"{BASE}/signalement", headers=ENTETES, timeout=30, json={
        "device": "test-appareil", "ts": maintenant, "lat": config.LAT_CENTRE,
        "lon": config.LON_CENTRE, "type": "accident"})
    print("signalement ->", r.status_code, r.json())

    r = requests.post(f"{BASE}/previsions", timeout=30, json={
        "x": config.LAT_CENTRE, "y": config.LON_CENTRE, "heure_depart": 6, "pluie": 1})
    data = r.json()
    print("previsions  ->", r.status_code, "| donnees_test :", data.get("donnees_test"))
    for p in data.get("previsions", []):
        print(f"   {p['heure']:>2} h  {p['niveau']:<14} risque {p['risque_embouteillage']}")
    if "meilleure_heure" in data:
        print("   meilleure heure :", data["meilleure_heure"]["heure"], "h")
    else:
        print("   ", data)
except requests.ConnectionError:
    print("Impossible de joindre le serveur. En local : lance python app.py d'abord.")
