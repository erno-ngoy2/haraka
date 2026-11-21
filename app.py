"""Serveur Haraka : collecte + prévisions dans une seule application Flask.

Vercel trouve tout seul ce fichier et la variable `app`.
En local : python app.py   (écoute sur le port 5000, ou sur $env:PORT)

Routes :
    GET  /              description du service
    GET  /sante         état (modèle présent, type de base)
    POST /collecte      points GPS               (collecte.py)
    POST /signalement   accident/travaux/evenement (collecte.py)
    POST /previsions    prévisions heure par heure (api.py)
"""
from flask import Flask, jsonify

import prevision
import stockage
from api import bp as bp_previsions
from collecte import bp as bp_collecte

app = Flask(__name__)
app.json.ensure_ascii = False  # accents lisibles dans les réponses JSON
app.register_blueprint(bp_collecte)
app.register_blueprint(bp_previsions)


@app.get("/")
def accueil():
    return jsonify({
        "service": "Haraka trafic-ia",
        "routes": ["GET /sante", "POST /collecte", "POST /signalement", "POST /previsions"],
    })


@app.get("/sante")
def sante():
    try:
        base = stockage.type_stockage() or "non configurée"
    except stockage.StockageIndisponible as e:
        base = f"erreur : {e}"
    try:
        modele = prevision.infos_modele()
    except FileNotFoundError:
        modele = None
    return jsonify({
        "ok": True,
        "modele_present": modele is not None,
        "donnees_test": True if modele is None else modele["donnees_test"],
        "modele": modele,
        "stockage": base,
    })


if __name__ == "__main__":
    import os
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
