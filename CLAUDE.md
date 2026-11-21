# CLAUDE.md : contexte du projet trafic-ia

Ce fichier est lu par Claude Code au début de chaque session. Il explique **ce qu'on construit, où on en est, et comment on travaille**. Mets-le à jour à la fin de chaque session de travail (section « État d'avancement »).

## Le projet en une phrase

Une IA simple qui **prédit les embouteillages** en RDC à partir de : la localisation (x = latitude, y = longitude), l'heure, le jour de la semaine, les jours fériés, la pluie et les circonstances (normal, accident, travaux, événement). Elle donne le niveau de trafic (`fluide`, `dense`, `embouteillage`) **heure par heure**, avec le risque d'embouteillage et la meilleure heure pour partir.

## Qui travaille dessus

- Développeur Android (Android Studio, Kotlin, Flutter, React Native) et Python, ingénieur en génie électrique, en RDC.
- PC Windows 11 (commandes à donner en version Windows : `venv\Scripts\activate`).
- Il apprend le machine learning : **explique chaque choix simplement, en français**, et propose le plus simple qui marche avant le plus sophistiqué.
- Budget serré, forfaits internet chers : privilégier l'hébergement gratuit ou peu cher et les envois de données groupés.

## Architecture

```
                         ┌──────────── Vercel (app.py, Flask) ────────────┐
App Android ──GPS/signalements──▶ POST /collecte, /signalement ──▶ Firebase Firestore
App Android ◀──prévisions JSON─── POST /previsions ◀── prevision.py ◀── model_trafic.joblib (dans GitHub)
                         └────────────────────────────────────────────────┘
Sur le PC (1x/jour)  : aggregation.py lit Firestore + Open-Meteo + data/*.csv ──▶ trafic.csv
Sur le PC (1x/sem.)  : train.py ──▶ model_trafic.joblib ──▶ git push ──▶ Vercel redéploie tout seul
```

Hébergement : **Vercel** (offre gratuite Hobby), déployé automatiquement depuis GitHub
(`https://github.com/erno-ngoy2/haraka`, ce dossier seulement, pas le code Kotlin).
Base : **Firebase Firestore** (Vercel n'a pas de disque permanent, SQLite y serait effacé).
En local sans clé Firebase, `stockage.py` utilise SQLite (`trafic.db`) pour les tests.

## Fichiers (tout est à plat à la racine, lancer les scripts depuis la racine)

| Fichier | Rôle |
|---|---|
| `config.py` | **Tous les réglages** : chemins, fuseau, seuils, features, modèle, Firestore. Modifier ici en priorité. |
| `app.py` | **Point d'entrée unique** (Vercel et local, port 5000) : branche `collecte.py` et `api.py`, plus `GET /` et `GET /sante`. |
| `stockage.py` | Lecture/écriture des positions et signalements : Firestore si clé trouvée (`FIREBASE_CREDENTIALS` ou `firebase-cle.json`), sinon SQLite. 1 envoi de l'app = 1 document Firestore (économie d'écritures). |
| `collecte.py` | `POST /collecte` (points GPS par lots de 500 max) et `POST /signalement`. Validation, clé via variable d'environnement `TRAFIC_CLE` (en-tête `X-Cle`). |
| `aggregation.py` | Points GPS → lignes zone+heure avec le niveau calculé, + météo, fériés, circonstances, + observations manuelles. Écrit `trafic.csv`. Tourne sur le PC. |
| `train.py` | Entraîne un RandomForest (pipeline scikit-learn), évalue, sauvegarde `model_trafic.joblib` compressé (~6 Mo). |
| `prevision.py` | Charge le modèle, calcule les prévisions heure par heure, applique les règles de jours fériés par-dessus. |
| `api.py` | `POST /previsions` pour l'app mobile (indique `donnees_test`). |
| `generer_donnees_test.py` | Données **artificielles** pour tester le code. |
| `test_api.py` | Teste le serveur : `python test_api.py` (local) ou `python test_api.py https://...vercel.app`. |
| `vercel.json`, `.python-version`, `requirements.txt` | Déploiement Vercel (région `fra1`, Python 3.13, versions figées). |
| `data/feries.csv` | Jours fériés (mois-jour) avec effet : `embouteillage`, `calme` ou `a_observer`, et heures éventuelles. |
| `data/observations_manuelles.csv` | Observations faites à la main (colonnes : date, heure, x, y, niveau, pluie, circonstance). |

## Décisions importantes (ne pas les remettre en cause sans en parler)

1. **Le niveau de trafic est calculé, pas saisi** : `ratio = vitesse médiane de la zone-heure / vitesse normale de la zone` (85e percentile). `< 0.40` embouteillage, `< 0.70` dense, sinon fluide. Seuils dans `config.py`.
2. **Zones** = cases d'environ 100 m (`DECIMALES_ZONE = 3`). Commencer avec **10 à 20 axes importants**, pas toute la ville.
3. **Fiabilité** : une ligne zone-heure n'est gardée qu'avec au moins 10 points GPS venant d'au moins 2 appareils.
4. **Jours fériés** : seulement ~10 par an, le modèle ne peut pas les apprendre tout de suite. On applique donc des **règles** (`feries.csv` + `ajuster_ferie` dans `prevision.py`) par-dessus le modèle. Connu : 1er janvier = embouteillages de 17h à 20h ; 4 janvier = journée presque vide ; 25 décembre = embouteillages ; les autres sont `a_observer` (à remplir avec des observations).
5. **Découpage de test chronologique** quand `trafic.csv` a une colonne `date` (entraîner sur le passé, tester sur le plus récent), pour éviter la triche.
6. **Vie privée** : consentement dans l'app, identifiant anonyme aléatoire, ignorer les ~200 premiers mètres de chaque trajet, supprimer les positions brutes après agrégation (30 jours max). Ne jamais enregistrer le numéro de téléphone ni un compte.
7. **Pas de prédictions publiées** tant qu'il n'y a pas de vraies données (objectif : 4 à 6 semaines, au moins 10 contributeurs réguliers). Exception décidée le 2026-11-21 : le modèle **de test** est en ligne pour développer l'app, mais chaque réponse porte `"donnees_test": true` (`config.MODELE_DONNEES_TEST`). L'app ne doit pas montrer ces prévisions au public tant que c'est `true`.
8. **Versions figées** dans `requirements.txt` : le modèle doit être rechargé avec les mêmes versions de scikit-learn/numpy. Changer une version = relancer `train.py` et committer le nouveau modèle.
9. **Secrets** : la clé Firebase (`firebase-cle.json`) et `TRAFIC_CLE` ne vont jamais dans git ; sur Vercel ce sont des variables d'environnement.

## Commandes (Windows)

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt

python generer_donnees_test.py   # données de test
python train.py                  # entraîne
python prevision.py              # démo en ligne de commande
python app.py                    # terminal 1 : serveur complet, port 5000
python test_api.py               # terminal 2 : teste toutes les routes
python test_api.py https://<projet>.vercel.app   # teste le serveur en ligne
python aggregation.py            # avec de vraies données : fabrique trafic.csv
```

Pour tester depuis l'app : émulateur Android = `http://10.0.2.2:5000` ; vrai téléphone = IP du PC sur le même Wi-Fi (autoriser Python dans le pare-feu) ; en ligne = l'URL Vercel (HTTPS).

## État d'avancement

- [x] Pipeline complet côté serveur (collecte, agrégation, entraînement, prévision, API)
- [x] Règles pour les jours fériés
- [x] Testé de bout en bout avec des données artificielles
- [ ] **App Android : service de collecte GPS** (Foreground Service, FusedLocationProvider, mode conduite, envoi par lots avec WorkManager)
- [ ] App Android : bouton « Signaler » (accident / travaux / événement)
- [ ] App Android : écran de prévisions (appelle `POST /previsions`)
- [ ] Définir les 10 à 20 zones/axes de départ et la ville exacte (**mettre à jour `LAT_CENTRE`, `LON_CENTRE`, `FUSEAU` dans `config.py`**)
- [ ] Première collecte réelle + observations manuelles (voir README)
- [x] Serveur prêt pour Vercel + Firestore (app.py unique, stockage.py, modèle compressé ~6 Mo), testé en local
- [ ] Créer la base Firestore, mettre `FIREBASE_CREDENTIALS` et `TRAFIC_CLE` dans Vercel, importer le dépôt GitHub (voir README)
- [ ] Vie privée : suppression des positions brutes après 30 jours. **À faire seulement quand `aggregation.py` gardera l'historique** de `trafic.csv` (aujourd'hui il recalcule tout depuis les positions brutes : les supprimer effacerait l'historique d'entraînement).
- [ ] Remplir les `a_observer` de `feries.csv` après chaque férié observé

## Prochaines améliorations possibles (plus tard)

- Ajouter le niveau de trafic de l'heure précédente comme information (souvent la plus utile).
- Ajouter le type de route, les jours de paie (fin de mois), la rentrée scolaire, les veilles de fête, les grands événements comme `circonstance`.
- Passer à `HistGradientBoostingClassifier` si le RandomForest ne suffit plus.
- Remplacer les zones en cases par des tronçons de route (map-matching).

## Manière de travailler

- Répondre en **français**, expliquer simplement, donner le code **complet** des fichiers modifiés.
- Garder les réglages dans `config.py` ; ne pas disperser des constantes dans les scripts.
- Après toute modification du pipeline, relancer : `generer_donnees_test.py` → `train.py` → `prevision.py` pour vérifier que rien ne casse.
- Les colonnes de `trafic.csv` doivent rester cohérentes entre `aggregation.py`, `generer_donnees_test.py`, `train.py` (`config.FEATURES`) et `prevision.py`. Si on ajoute une information, la modifier **aux 4 endroits**.
- Ne jamais committer `trafic.db`, `trafic.csv`, ni aucune clé (voir `.gitignore`). `model_trafic.joblib` **est** committé : Vercel en a besoin.
