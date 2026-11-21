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

2. **Chaque jour**, sur le PC (avec `firebase-cle.json`) :

   ```powershell
   venv\Scripts\python aggregation.py
   ```

   Il lit Firestore, ajoute la journée à l'historique `data/agregats.csv`, fabrique
   `trafic.csv` puis **supprime les positions brutes de plus de 30 jours** (vie privée).
   ⚠️ `data/agregats.csv` devient alors la seule copie de l'historique : sauvegarde-le
   régulièrement (clé USB, Google Drive...).

3. **Chaque semaine** :

   ```powershell
   venv\Scripts\python train.py
   ```

   - Il apprend sur les jours anciens et teste sur les 20 % de jours les plus récents.
   - Il compare le modèle à deux méthodes simples et au modèle déjà en ligne.
   - Il **refuse de publier** (code 2, le modèle en ligne reste) si : moins de 500 lignes
     ou de 14 jours, pas mieux que « toujours le niveau le plus fréquent », ou plus de
     2 points de F1 macro perdus face au modèle en ligne.
   - Rapport détaillé dans `rapports/AAAAMMJJ-HHMMSS.json`.

   Si c'est publié :

   ```powershell
   git add model_trafic.joblib rapports; git commit -m "Nouveau modèle"; git push
   ```

   Vercel redéploie tout seul. `donnees_test` passe automatiquement à `false` dès que le
   modèle vient de vraies données (`aggregation.py`).

4. Revenir au modèle précédent si besoin : `model_trafic.precedent.joblib` (copie automatique)
   → renomme-le en `model_trafic.joblib`, puis commit et push.

## Tester comme en production (simulation)

`simuler_gps.py` invente des semaines de positions GPS (axes, téléphones, heures de pointe,
accidents) et rejoue **tout** le pipeline dans le dossier `simulation/`, sans toucher aux
vrais fichiers ni à Firebase :

```powershell
$env:HARAKA_SIMULATION = "1"
venv\Scripts\python simuler_gps.py --jours 35 --reset
venv\Scripts\python aggregation.py
venv\Scripts\python train.py
venv\Scripts\python app.py
Remove-Item Env:HARAKA_SIMULATION
```

Essaie aussi `--jours 10 --reset` (le modèle doit être refusé : moins de 14 jours), puis
`--decalage` pour simuler des jours qui arrivent ensuite.

## Jours fériés (`data/feries.csv`)

| colonne | exemple | sens |
|---|---|---|
| `jour` | `12-25` ou `2026-01-05` | `MM-JJ` = chaque année ; `AAAA-MM-JJ` = cette date seulement (reports) |
| `effet` | `embouteillage` / `calme` / `a_observer` | règle appliquée par-dessus le modèle |
| `creneaux` | `11-14;16-22` | heures d'embouteillage (de 11h00 à 14h00 puis de 16h00 à 22h00) |
| `zones` | texte | lieux concernés (information) |

**Chaque année**, ajoute les reports des fériés tombés un week-end (ex. `2027-...`).

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

Réponse :
- `previsions` (heure, niveau, risque_embouteillage, probabilites, ajuste_par_regle) et `meilleure_heure` ;
- `donnees_test` : `true` = modèle de test, **ne pas montrer au public** ;
- `zone_connue` / `distance_zone_m` : `false` = point loin de toute zone vue à l'entraînement
  (le modèle devine, **ne pas montrer**) ;
- `modele` : version, date d'entraînement, période couverte, F1 macro au test ;
- `jour_ferie` : nom, effet et zones concernées si c'est un jour férié.

## Remarques

- La météo vient d'Open-Meteo (archive) : les tout derniers jours peuvent ne pas avoir de mesure, la pluie vaut alors 0.
- Mets à jour `LAT_CENTRE`, `LON_CENTRE` et `FUSEAU` dans `config.py` pour ta ville.
- Offre gratuite Firestore : 20 000 écritures et 50 000 lectures par jour. Un envoi de l'app
  = 1 écriture ; un passage d'`aggregation.py` = 1 lecture par envoi stocké.
