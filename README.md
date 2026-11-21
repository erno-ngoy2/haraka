# trafic-ia : prédire les embouteillages (serveur Haraka)

Voir `CLAUDE.md` pour le contexte complet, l'architecture et l'état d'avancement.

## Démarrage rapide en local (Windows)

```powershell
cd "D:\projet haraka\trafic-ia"
python -m venv venv
venv\Scripts\python -m pip install -r requirements.txt

venv\Scripts\python generer_donnees_test.py     # crée trafic.csv (données artificielles)
venv\Scripts\python train.py                    # entraîne et sauvegarde model_trafic.joblib
venv\Scripts\python prevision.py                # démo en ligne de commande
```

Serveur complet (collecte + prévisions) sur le port 5000, puis test dans un second terminal :

```powershell
venv\Scripts\python app.py
venv\Scripts\python test_api.py
```

Sans clé Firebase, les données vont dans `trafic.db` (SQLite) : parfait pour tester.

## Mise en ligne : GitHub → Vercel, base Firebase Firestore

### 1. Firebase (projet `haraka-d3e2c`)

1. https://console.firebase.google.com → projet **haraka-d3e2c**.
2. **Firestore Database** → Créer une base de données → mode **production**
   → emplacement **europe-west3 (Francfort)** (proche du serveur Vercel `fra1`).
   Le mode production bloque tout accès direct depuis l'extérieur : seul notre serveur
   (clé de compte de service) peut lire et écrire. C'est ce qu'on veut.
3. ⚙️ **Paramètres du projet** → onglet **Comptes de service** → **Générer une nouvelle clé privée**.
   Un fichier JSON est téléchargé (il contient `"type": "service_account"` et `"private_key"`).
   - Pour ton PC : renomme-le `firebase-cle.json` et place-le dans `trafic-ia\`
     (il est dans `.gitignore`, il n'ira jamais sur GitHub).
   - **Ne l'envoie à personne, ne le colle nulle part ailleurs que dans Vercel.**

> Le bloc `firebaseConfig` (apiKey, authDomain...) de la console sert aux apps web/Android,
> pas au serveur Python. Le serveur a besoin de la clé de compte de service ci-dessus.

### 2. Vercel

1. https://vercel.com → **Add New… → Project** → importer le dépôt GitHub **erno-ngoy2/haraka**.
2. Framework : Vercel détecte **Flask** tout seul (fichier `app.py`). Rien à changer.
3. **Environment Variables** (avant de cliquer sur Deploy, ou plus tard dans Settings) :
   - `FIREBASE_CREDENTIALS` = **tout le contenu** du fichier JSON de la clé (ouvre-le avec le
     Bloc-notes, Ctrl+A, Ctrl+C, colle).
   - `TRAFIC_CLE` = un mot de passe long que tu inventes. L'app Android devra l'envoyer dans
     l'en-tête `X-Cle` pour `/collecte` et `/signalement`.
4. **Deploy**. Ensuite, chaque `git push` sur la branche `main` redéploie tout seul.
   Si tu ajoutes ou changes une variable, refais un déploiement (onglet Deployments → Redeploy).

### 3. Vérifier

```powershell
venv\Scripts\python test_api.py https://<ton-projet>.vercel.app
```

`/sante` doit répondre `"stockage": "firestore"` et `"modele_present": true`.
Avec `TRAFIC_CLE`, définis-la d'abord dans le terminal : `$env:TRAFIC_CLE = "..."`.

## Routine avec de vraies données

1. **Observations manuelles** (dès la semaine 1) : remplis `data/observations_manuelles.csv` :

   ```csv
   date,heure,x,y,niveau,pluie,circonstance
   2026-11-24,7,-11.6612,27.4795,embouteillage,0,normal
   ```

   `niveau` : fluide, dense ou embouteillage. `pluie` : 0 ou 1. `circonstance` : normal, accident, travaux, evenement.

2. Chaque jour, sur le PC (avec `firebase-cle.json`) : `venv\Scripts\python aggregation.py`
   lit Firestore et fabrique `trafic.csv`.
3. Chaque semaine : `venv\Scripts\python train.py`, puis
   `git add model_trafic.joblib && git commit -m "Nouveau modèle" && git push` → Vercel redéploie.
4. Quand le modèle vient de **vraies** données : mets `MODELE_DONNEES_TEST = False` dans `config.py`.

## Format des requêtes

`POST /collecte` (en-tête `X-Cle` si `TRAFIC_CLE` est définie), 500 points max par envoi :

```json
{"device": "id-anonyme-aleatoire",
 "points": [{"ts": "2026-11-24T07:05:10Z", "lat": -11.6612, "lon": 27.4795,
             "vitesse_kmh": 18.5, "precision_m": 9, "en_vehicule": 1}]}
```

`POST /signalement` : `{"device": "...", "ts": "...", "lat": ..., "lon": ..., "type": "accident"}`

`POST /previsions`

```json
{"x": -11.66, "y": 27.48, "heure_depart": 7, "date": "2026-11-24",
 "pluie": 1, "circonstance": "normal", "nb_heures": 6}
```

Réponse : `donnees_test`, liste `previsions` (heure, niveau, risque_embouteillage, probabilites)
et `meilleure_heure`.

## Remarques

- La météo vient d'Open-Meteo (archive) : les tout derniers jours peuvent ne pas avoir de mesure, la pluie vaut alors 0.
- Mets à jour `LAT_CENTRE`, `LON_CENTRE` et `FUSEAU` dans `config.py` pour ta ville.
- Offre gratuite Firestore : 20 000 écritures et 50 000 lectures par jour. Un envoi de l'app
  = 1 écriture ; un passage d'`aggregation.py` = 1 lecture par envoi stocké.
