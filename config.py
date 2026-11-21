"""Configuration commune à tous les scripts. Modifie ici, pas ailleurs."""
import os
from pathlib import Path

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"

DB = BASE / "trafic.db"                      # base SQLite locale (tests sans Firebase)
FERIES_CSV = DATA / "feries.csv"             # jours fériés + effet sur le trafic
OBSERVATIONS_CSV = DATA / "observations_manuelles.csv"
TRAFIC_CSV = BASE / "trafic.csv"             # jeu d'entraînement (produit par aggregation.py)
MODELE = BASE / "model_trafic.joblib"        # modèle entraîné

# Ville de départ : LUBUMBASHI (décidé le 2026-11-21).
# Fuseau horaire : "Africa/Lubumbashi" (UTC+2). Pour Kinshasa ce serait "Africa/Kinshasa" (UTC+1).
FUSEAU = "Africa/Lubumbashi"

# Centre-ville de Lubumbashi. Sert au générateur de données de test, à la simulation
# et à la météo (position de référence).
LAT_CENTRE = -11.66
LON_CENTRE = 27.48

# --- Découpage en zones -----------------------------------------------------
# Nombre de décimales des coordonnées : 3 -> cases d'environ 100 m, 2 -> environ 1 km
DECIMALES_ZONE = 3

# --- Fiabilité des lignes zone + heure --------------------------------------
PRECISION_GPS_MAX_M = 30     # on ignore les points GPS moins précis que ça
MIN_POINTS = 10              # points GPS minimum par zone + heure
MIN_APPAREILS = 2            # appareils différents minimum par zone + heure
MIN_LIGNES_PAR_ZONE = 10     # lignes minimum pour estimer la "vitesse normale" d'une zone

# --- Définition des niveaux (ratio vitesse mesurée / vitesse normale) ---------
SEUIL_EMBOUTEILLAGE = 0.40   # en dessous : embouteillage
SEUIL_DENSE = 0.70           # entre les deux : dense ; au-dessus : fluide

# Durée pendant laquelle un signalement (accident, travaux...) reste valable
DUREE_SIGNALEMENT_H = 2

# Pluie : précipitation horaire (mm) à partir de laquelle on compte "pluie"
SEUIL_PLUIE_MM = 0.2

NIVEAUX = ["fluide", "dense", "embouteillage"]
CIRCONSTANCES = ["normal", "accident", "travaux", "evenement"]

FEATURES = ["x", "y", "heure", "jour_semaine", "ferie", "type_ferie", "pluie", "circonstance"]
CATEGORIES = ["circonstance", "type_ferie"]
CIBLE = "niveau"

# --- Stockage des positions et signalements ----------------------------------
# En ligne (Vercel) : Firebase Firestore, car Vercel n'a pas de disque permanent.
# En local : SQLite (trafic.db) tant qu'aucune clé Firebase n'est trouvée.
# La clé est lue dans la variable d'environnement FIREBASE_CREDENTIALS (contenu JSON),
# sinon dans ce fichier. NE JAMAIS LE COMMITTER (il est dans .gitignore).
FIREBASE_CLE_FICHIER = BASE / "firebase-cle.json"
COLLECTION_ENVOIS = "envois"                 # 1 document = 1 envoi de l'app (jusqu'à 500 points)
COLLECTION_SIGNALEMENTS = "signalements"
MAX_POINTS_PAR_ENVOI = 500

# --- Historique et vie privée ------------------------------------------------
# agregats.csv = l'historique DURABLE (une ligne par zone + date + heure, sans
# identifiant d'appareil). C'est lui qui sert à l'entraînement, pas les positions brutes.
# Il n'est PAS sur GitHub : fais-en une copie de sauvegarde régulière (clé USB, Drive...).
AGREGATS_CSV = DATA / "agregats.csv"
TRAFIC_INFO = BASE / "trafic_info.json"      # d'où vient trafic.csv (réel ou test)
# Les positions brutes plus vieilles que ça sont supprimées après agrégation.
RETENTION_BRUTES_JOURS = 30
# Les jours récents sont recalculés à chaque passage (des envois arrivent en retard
# quand le téléphone n'a pas de connexion). Au-delà, un jour déjà agrégé est figé.
FENETRE_RECALCUL_JOURS = 7

# --- Modèle ------------------------------------------------------------------
# 100 arbres, 5 exemples minimum par feuille, fichier compressé : environ 6 Mo
# (contre 96 Mo avec 200 arbres / feuille de 2) pour moins d'un point de précision perdu.
# Le fichier doit rester petit : il est envoyé sur GitHub et chargé par Vercel.
N_ARBRES = 100
FEUILLE_MIN = 5
MODELE_PRECEDENT = BASE / "model_trafic.precedent.joblib"  # pour revenir en arrière
RAPPORTS = BASE / "rapports"                 # un rapport JSON par entraînement

# --- Garde-fous de l'entraînement (données réelles seulement) ----------------
MIN_LIGNES_ENTRAINEMENT = 500   # lignes zone + heure fiables
MIN_JOURS_ENTRAINEMENT = 14     # jours différents (pour voir au moins 2 semaines)
PART_TEST = 0.2                 # 20 % des jours les plus récents servent au test
MIN_LIGNES_COMPARAISON = 50     # pour comparer l'ancien et le nouveau modèle
TOLERANCE_REGRESSION = 0.02     # le nouveau peut perdre au plus 2 points de F1 macro

# --- Prévisions --------------------------------------------------------------
# Si le point demandé est à plus de cette distance de toute zone vue à l'entraînement,
# la prévision est marquée "zone_connue": false (le modèle devine, ne pas l'afficher).
DISTANCE_MAX_ZONE_M = 300

# --- Mode simulation (HARAKA_SIMULATION=1) ------------------------------------
# Rejoue tout le pipeline de production (simuler_gps.py -> aggregation.py -> train.py
# -> app.py) avec des positions GPS inventées, dans le dossier simulation/ :
# base SQLite, historique, trafic.csv, modèle et rapports y sont séparés des vrais.
# Firebase n'est jamais utilisé et le modèle obtenu est marqué "donnees_test".
SIMULATION = os.environ.get("HARAKA_SIMULATION") == "1"
if SIMULATION:
    SORTIE_SIMULATION = BASE / "simulation"
    SORTIE_SIMULATION.mkdir(exist_ok=True)
    DB = SORTIE_SIMULATION / "trafic.db"
    AGREGATS_CSV = SORTIE_SIMULATION / "agregats.csv"
    TRAFIC_CSV = SORTIE_SIMULATION / "trafic.csv"
    TRAFIC_INFO = SORTIE_SIMULATION / "trafic_info.json"
    MODELE = SORTIE_SIMULATION / "model_trafic.joblib"
    MODELE_PRECEDENT = SORTIE_SIMULATION / "model_trafic.precedent.joblib"
    RAPPORTS = SORTIE_SIMULATION / "rapports"
    OBSERVATIONS_CSV = SORTIE_SIMULATION / "observations_manuelles.csv"  # n'existe pas : ignoré
