"""Construit data/rues_lubumbashi.json à partir d'une liste brute de noms (un par ligne).

Sources de la liste (relevée le 2026-11-21) :
  - https://rues-rd-congo.openalfa.com/lubumbashi/liste-rues/  (lettres a à z, 0-9)
  - https://fr-academic.com/dic.nsf/frwiki/2022768
Les rues déjà parmi les 10 routes de départ (data/routes.json) sont retirées.
Les variantes d'un même nom (casse, accents, « de la », fautes connues) sont fusionnées.
Numéros : à partir de 1001 (les routes créées sur le terrain gardent 11, 12, ...).

Usage : python outils/construire_catalogue_rues.py liste_brute.txt
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
from routes import cle_nom, type_de_voie  # noqa: E402

# Variantes / fautes de frappe repérées dans les sources -> nom retenu
VARIANTES = {
    "Avenue Laboritoire": "Avenue Laboratoire",
    "Avenue Du Laboratoir": "Avenue Laboratoire",
    "Avenue des Laboratoires": "Avenue Laboratoire",
    "Route du Golfe": "Route du Golf",
    "Avenue Monseigneur de Hemptine": "Avenue Monseigneur De Hemptinne",
    "Avenue Makwaruzo": "Avenue Makwarunzu",
    "Avenue Mandunga": "Avenue Mandungu",
    "Avenue Sinai": "Avenue du Sinaï",
    "Avenue Tanganika": "Avenue Tanganyika",
    "Rue Tanganyka": "Avenue Tanganyika",
    "Avenue Psychologie": "Avenue de Psychologie",
    "Avenue Plateaux": "Avenue de Plateau",
    "Avenue Potagée": "Avenue du Potager",
    "Avenue Zambèze": "Avenue du Zambèze",
    "Avenue 17 Mai": "Avenue du 17 Mai",
    "Avenue Vua": "Avenue de Vua",
    "Avenue Lwalaba": "Avenue Lualaba",
    "Avenue Lwena": "Avenue Luena",
    "Avenue du Roche": "Avenue du Rocher",
    "Avenue des Roches": "Avenue du Rocher",
}
# Noms qui désignent une des 10 routes de départ (id dans data/routes.json)
DEJA_ROUTE_DE_DEPART = {
    "Avenue Lumumba": 1, "Boulevard Kamanyola": 2, "Avenue LD Kabila": 3,
    "Avenue Jason Sendwe": 4, "Avenue Mama Yemo": 5, "Avenue Kasavubu": 6,
    "Avenue de la Révolution": 7, "Avenue du 30 Juin": 8, "Avenue de la Libération": 9,
    "Avenue des Usines": 10,
}


def main(chemin):
    depart = json.loads(config.ROUTES_JSON.read_text(encoding="utf-8"))["routes"]
    cles_depart = {cle_nom(r["nom"]) for r in depart}
    vus, rues = set(), []
    for ligne in Path(chemin).read_text(encoding="utf-8").splitlines():
        nom = " ".join(ligne.split())
        if not nom:
            continue
        nom = nom[0].upper() + nom[1:]
        nom = VARIANTES.get(nom, nom)
        if nom in DEJA_ROUTE_DE_DEPART or cle_nom(nom) in cles_depart:
            continue
        cle = (type_de_voie(nom), cle_nom(nom))
        if cle in vus:
            continue
        vus.add(cle)
        rues.append(nom)
    rues.sort(key=lambda n: (cle_nom(n), n))
    sortie = {
        "ville": "Lubumbashi",
        "sources": ["https://rues-rd-congo.openalfa.com/lubumbashi/liste-rues/",
                    "https://fr-academic.com/dic.nsf/frwiki/2022768"],
        "releve_le": "2026-11-21",
        "note": "Noms seulement : pas de coordonnées. La forme de chaque rue se dessine avec "
                "les points relevés sur le terrain depuis l'app.",
        "rues": [{"id": config.PREMIER_ID_CATALOGUE + i, "nom": n, "type": type_de_voie(n)}
                 for i, n in enumerate(rues)],
    }
    config.RUES_JSON.write_text(json.dumps(sortie, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(rues)} rues écrites dans {config.RUES_JSON.name} "
          f"(ids {config.PREMIER_ID_CATALOGUE} à {config.PREMIER_ID_CATALOGUE + len(rues) - 1})")


if __name__ == "__main__":
    main(sys.argv[1])
