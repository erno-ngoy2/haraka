"""Configuration commune à tous les scripts. Modifie ici, pas ailleurs."""
from pathlib import Path

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"

DB = BASE / "trafic.db"                      # base SQLite locale (tests sans Firebase)
FERIES_CSV = DATA / "feries.csv"             # jours fériés + effet sur le trafic
OBSERVATIONS_CSV = DATA / "observations_manuelles.csv"
TRAFIC_CSV = BASE / "trafic.csv"             # jeu d'entraînement (produit par aggregation.py)
MODELE = BASE / "model_trafic.joblib"        # modèle entraîné

# Fuseau horaire de ta ville : "Africa/Lubumbashi" (UTC+2) ou "Africa/Kinshasa" (UTC+1)
FUSEAU = "Africa/Lubumbashi"

# Centre de ta ville (EXEMPLE : à remplacer). Sert au générateur de données de test
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

# --- Modèle ------------------------------------------------------------------
# 100 arbres, 5 exemples minimum par feuille, fichier compressé : environ 6 Mo
# (contre 96 Mo avec 200 arbres / feuille de 2) pour moins d'un point de précision perdu.
# Le fichier doit rester petit : il est envoyé sur GitHub et chargé par Vercel.
N_ARBRES = 100
FEUILLE_MIN = 5

# True tant que le modèle publié est entraîné sur generer_donnees_test.py.
# L'API l'indique dans chaque réponse ("donnees_test"). Passe à False seulement
# après un train.py sur de vraies données (aggregation.py).
MODELE_DONNEES_TEST = True
