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
COLONNES_OBSERVATIONS = ["device", "ts", "lat", "lon", "niveau", "pluie", "circonstance"]
# Les autres champs (route, météo, commentaire, précision GPS...) sont rangés tels quels
# dans Firestore, et dans la colonne "details" (texte JSON) en SQLite.


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
    """'firestore', 'sqlite', ou None si rien n'est utilisable.

    HARAKA_STOCKAGE=sqlite force la base locale même si firebase-cle.json existe
    (pour les essais et la simulation, sans toucher aux vraies données)."""
    if config.SIMULATION or os.environ.get("HARAKA_STOCKAGE", "").lower() == "sqlite":
        return "sqlite"
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
            device TEXT, ts TEXT, lat REAL, lon REAL, type TEXT, details TEXT);
        CREATE TABLE IF NOT EXISTS observations(
            device TEXT, ts TEXT, lat REAL, lon REAL, niveau TEXT, pluie INTEGER,
            circonstance TEXT, details TEXT);
        CREATE INDEX IF NOT EXISTS idx_positions_ts ON positions(ts);
    """)
    # Anciennes bases (avant la colonne "details") : on l'ajoute
    colonnes = [c[1] for c in con.execute("PRAGMA table_info(signalements)")]
    if "details" not in colonnes:
        con.execute("ALTER TABLE signalements ADD COLUMN details TEXT")
    return con


def _ajouter(collection, table, colonnes, doc):
    """Firestore : tout le document. SQLite : colonnes principales + le reste en JSON."""
    if _type_ou_erreur() == "firestore":
        from google.cloud.firestore import SERVER_TIMESTAMP
        _firestore().collection(collection).add({**doc, "recu_le": SERVER_TIMESTAMP})
    else:
        details = {k: v for k, v in doc.items() if k not in colonnes}
        valeurs = tuple(doc.get(c) for c in colonnes) + (json.dumps(details, ensure_ascii=False),)
        trous = ",".join("?" * (len(colonnes) + 1))
        with closing(_sqlite()) as con, con:
            con.execute(f"INSERT INTO {table} ({','.join(colonnes)},details) VALUES ({trous})", valeurs)


def _lire(collection, table, colonnes):
    if _type_ou_erreur() == "firestore":
        lignes = [doc.to_dict() for doc in _firestore().collection(collection).stream()]
        df = pd.DataFrame(lignes)
        for c in colonnes:
            if c not in df.columns:
                df[c] = None
        return df
    if not config.DB.exists():
        return pd.DataFrame(columns=colonnes)
    with closing(_sqlite()) as con:
        df = pd.read_sql(f"SELECT * FROM {table}", con)
    if df.empty or "details" not in df.columns:
        return df.drop(columns=["details"], errors="ignore")
    # Lignes anciennes (avant la colonne "details") : valeur vide -> {}
    extra = pd.json_normalize([json.loads(d) if isinstance(d, str) and d else {}
                               for d in df["details"]], max_level=0)
    return pd.concat([df.drop(columns=["details"]).reset_index(drop=True),
                      extra.drop(columns=[c for c in extra.columns if c in df.columns])], axis=1)


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
    """s : dict déjà validé (device, ts, lat, lon, type + champs en plus)."""
    _ajouter(config.COLLECTION_SIGNALEMENTS, "signalements", COLONNES_SIGNALEMENTS, s)


def ajouter_observation(o):
    """o : dict déjà validé (device, ts, lat, lon, niveau, pluie, circonstance + champs en plus)."""
    _ajouter(config.COLLECTION_OBSERVATIONS, "observations", COLONNES_OBSERVATIONS, o)


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
    """Tous les signalements (colonnes principales + champs en plus : categorie, route...)."""
    return _lire(config.COLLECTION_SIGNALEMENTS, "signalements", COLONNES_SIGNALEMENTS)


def lire_observations():
    """Enregistrements manuels faits dans l'app (niveau observé sur place)."""
    return _lire(config.COLLECTION_OBSERVATIONS, "observations", COLONNES_OBSERVATIONS)


# --- Vie privée : suppression des données brutes anciennes -------------------

def purger_avant(limite):
    """Supprime positions et signalements d'INCIDENTS reçus avant `limite` (datetime UTC).

    À n'appeler qu'APRÈS avoir sauvegardé les agrégats (aggregation.py le fait).
    Ne supprime PAS :
      - les signalements de LIEUX (bar, hôtel...) : c'est un inventaire, pas un moment ;
      - les observations manuelles : ce sont des exemples d'entraînement saisis exprès.
    Firestore : se base sur 'recu_le' (date de réception par le serveur).
    SQLite : se base sur 'ts' (date de la mesure).
    Retourne (nb_envois_ou_positions, nb_signalements) supprimés.
    """
    if _type_ou_erreur() == "firestore":
        from google.cloud.firestore_v1.base_query import FieldFilter
        db = _firestore()
        totaux = []
        for nom in (config.COLLECTION_ENVOIS, config.COLLECTION_SIGNALEMENTS):
            n = 0
            dernier = None
            while True:
                q = (db.collection(nom).where(filter=FieldFilter("recu_le", "<", limite))
                     .order_by("recu_le").limit(400))
                if dernier is not None:
                    q = q.start_after(dernier)
                docs = list(q.stream())
                if not docs:
                    break
                dernier = docs[-1]
                a_supprimer = [d for d in docs if (d.to_dict() or {}).get("categorie") != "lieu"]
                if a_supprimer:
                    lot = db.batch()
                    for d in a_supprimer:
                        lot.delete(d.reference)
                    lot.commit()
                n += len(a_supprimer)
            totaux.append(n)
        return tuple(totaux)
    if not config.DB.exists():
        return (0, 0)
    seuil = limite.strftime("%Y-%m-%dT%H:%M:%S")
    with closing(_sqlite()) as con, con:
        n1 = con.execute("DELETE FROM positions WHERE ts < ?", (seuil,)).rowcount
        n2 = con.execute(
            "DELETE FROM signalements WHERE ts < ? "
            "AND COALESCE(json_extract(details, '$.categorie'), '') != 'lieu'", (seuil,)).rowcount
    return (n1, n2)
