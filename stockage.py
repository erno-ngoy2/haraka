"""Où sont rangées les positions GPS et les signalements.

- En ligne (Vercel) : Firebase Firestore. Vercel n'a pas de disque permanent :
  un fichier SQLite y serait effacé à chaque redémarrage.
- En local, sans clé Firebase : SQLite (trafic.db), pratique pour tester.

Firestore est utilisé dès qu'une clé de compte de service est trouvée :
variable d'environnement FIREBASE_CREDENTIALS (le contenu du fichier JSON),
ou fichier firebase-cle.json à côté de ce script (voir config.py).

Économie : un envoi de l'app (jusqu'à 500 points) = UN seul document Firestore,
donc une seule écriture comptée au lieu de 500 (offre gratuite : 20 000 écritures/jour).
"""
import functools
import json
import os
import sqlite3
from contextlib import closing

import pandas as pd

import config

COLONNES_POSITIONS = ["device", "ts", "lat", "lon", "vitesse_kmh", "precision_m", "en_vehicule"]
COLONNES_SIGNALEMENTS = ["device", "ts", "lat", "lon", "type"]


class StockageIndisponible(Exception):
    """Aucune base utilisable (ex. sur Vercel sans clé Firebase)."""


@functools.lru_cache(maxsize=1)
def _cle_firebase():
    brut = os.environ.get("FIREBASE_CREDENTIALS")
    try:
        if brut:
            return json.loads(brut)
        if config.FIREBASE_CLE_FICHIER.exists():
            return json.loads(config.FIREBASE_CLE_FICHIER.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise StockageIndisponible(f"clé Firebase illisible (JSON invalide) : {e}") from e
    return None


def type_stockage():
    """'firestore', 'sqlite', ou None si rien n'est utilisable."""
    if _cle_firebase() is not None:
        return "firestore"
    if os.environ.get("VERCEL"):  # disque non permanent : SQLite perdrait les données
        return None
    return "sqlite"


def _type_ou_erreur():
    t = type_stockage()
    if t is None:
        raise StockageIndisponible(
            "base non configurée : ajoute la variable FIREBASE_CREDENTIALS dans Vercel")
    return t


@functools.lru_cache(maxsize=1)
def _firestore():
    import firebase_admin
    from firebase_admin import credentials, firestore

    if not firebase_admin._apps:
        firebase_admin.initialize_app(credentials.Certificate(_cle_firebase()))
    return firestore.client()


def _sqlite():
    con = sqlite3.connect(config.DB)
    con.executescript("""
        CREATE TABLE IF NOT EXISTS positions(
            device TEXT, ts TEXT, lat REAL, lon REAL,
            vitesse_kmh REAL, precision_m REAL, en_vehicule INTEGER);
        CREATE TABLE IF NOT EXISTS signalements(
            device TEXT, ts TEXT, lat REAL, lon REAL, type TEXT);
        CREATE INDEX IF NOT EXISTS idx_positions_ts ON positions(ts);
    """)
    return con


# --- Écriture (utilisée par collecte.py) -------------------------------------

def ajouter_points(device, points):
    """points : liste de dicts déjà validés (ts, lat, lon, vitesse_kmh, precision_m, en_vehicule)."""
    if not points:
        return
    if _type_ou_erreur() == "firestore":
        from google.cloud.firestore import SERVER_TIMESTAMP
        _firestore().collection(config.COLLECTION_ENVOIS).add(
            {"device": device, "recu_le": SERVER_TIMESTAMP, "points": points})
    else:
        lignes = [tuple([device] + [p[c] for c in COLONNES_POSITIONS[1:]]) for p in points]
        with closing(_sqlite()) as con, con:
            con.executemany("INSERT INTO positions VALUES (?,?,?,?,?,?,?)", lignes)


def ajouter_signalement(s):
    """s : dict déjà validé (device, ts, lat, lon, type)."""
    if _type_ou_erreur() == "firestore":
        from google.cloud.firestore import SERVER_TIMESTAMP
        _firestore().collection(config.COLLECTION_SIGNALEMENTS).add(
            {**s, "recu_le": SERVER_TIMESTAMP})
    else:
        with closing(_sqlite()) as con, con:
            con.execute("INSERT INTO signalements VALUES (?,?,?,?,?)",
                        tuple(s[c] for c in COLONNES_SIGNALEMENTS))


# --- Lecture (utilisée par aggregation.py) -----------------------------------

def lire_positions():
    if _type_ou_erreur() == "firestore":
        lignes = []
        for doc in _firestore().collection(config.COLLECTION_ENVOIS).stream():
            d = doc.to_dict()
            lignes += [{**p, "device": d.get("device", "?")} for p in d.get("points", [])]
        return pd.DataFrame(lignes, columns=COLONNES_POSITIONS)
    if not config.DB.exists():
        return pd.DataFrame(columns=COLONNES_POSITIONS)
    with closing(_sqlite()) as con:
        return pd.read_sql("SELECT * FROM positions", con)


def lire_signalements():
    if _type_ou_erreur() == "firestore":
        lignes = [doc.to_dict() for doc in
                  _firestore().collection(config.COLLECTION_SIGNALEMENTS).stream()]
        return pd.DataFrame(lignes, columns=COLONNES_SIGNALEMENTS)
    if not config.DB.exists():
        return pd.DataFrame(columns=COLONNES_SIGNALEMENTS)
    with closing(_sqlite()) as con:
        return pd.read_sql("SELECT * FROM signalements", con)
