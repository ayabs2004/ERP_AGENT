"""
enrichir_exemples.py — revue humaine des corrections détectées et
intégration validée dans semantic_examples.yaml.
🧠 Rôle global

👉 Ce script sert à :interface humaine pour dire : cette erreur → devient un vrai exemple d’apprentissage”

transformer des erreurs détectées automatiquement → en nouveaux exemples d’apprentissage validés par un humain
RÉÉCRITURE (amélioration #5) : la version précédente patchait le texte
source de semantic_classifier.py à la recherche de `"ACTION": [` — un
format qui n'existe plus depuis que semantic_examples.yaml est devenu
la source de vérité unique (voir l'en-tête de semantic_classifier.py).
Ce script était donc silencieusement cassé (aucune action ne matchait
jamais `marqueur`). Il utilise maintenant sc.inserer_exemple_valide(),
qui écrit dans le YAML, met à jour le cache d'embeddings et recalcule
les centroïdes concernés.
🔄 Ce que fait le script (étape par étape)
1. Charge les corrections en attente
lignes = corrections_a_verifier.jsonl

Chaque ligne ressemble à :

{
  "question": "crée moi un BL",
  "action_predite": "CREER_CLIENT",
  "correction_supposee": "GENERER_DOC"
}
2. Affiche les cas à un humain

Il fait :

Question            : crée moi un BL
Action prédite      : CREER_CLIENT
Correction supposée : GENERER_DOC

Puis demande :

Valider ? [o/N/s]
3. Décision humaine
Si tu tapes :
Input	Résultat
o	accepté
n	rejeté définitivement
s	gardé pour plus tard
4. Filtrage des corrections

Après la boucle :

validees → à apprendre
reste → encore en attente

Et il réécrit le fichier :

_CORRECTIONS_PATH.write_text(...)

👉 donc :

les “n” disparaissent
les “s” restent
5. Ajout dans le système d’apprentissage

Pour chaque correction validée :

sc.inserer_exemple_valide(action, question)

👉 ça fait 3 choses importantes :

✔ 1. ajoute dans semantic_examples.yaml

→ nouvelle donnée d’entraînement

✔ 2. met à jour embeddings

→ recalcul vectoriel

✔ 3. met à jour centroïdes

→ impact direct sur classification

🧠 Donc ce fichier fait quoi ?
👉 résumé simple :

Il transforme les erreurs détectées en nouveaux exemples d’entraînement validés humainement.

🔁 Dans ton pipeline global

Voici où il se situe :

1. Utilisateur parle
2. Classifieur se trompe
3. detecter_correction() → logs erreur
4. corrections_a_verifier.jsonl se remplit

5. TU LANCES :
   python enrichir_exemples.py

6. Tu valides manuellement
7. Le système apprend
Usage : python enrichir_exemples.py
"""
import asyncio
import json
from pathlib import Path

import semantic_classifier as sc

_CORRECTIONS_PATH = Path("./corrections_a_verifier.jsonl")


async def _main_async():
    if not _CORRECTIONS_PATH.exists():
        print("Aucune correction en attente.")
        return
    lignes = [l for l in _CORRECTIONS_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]
    if not lignes:
        print("Aucune correction en attente.")
        return

    print("⏳ Chargement du classifieur sémantique (embeddings de référence)...")
    await sc.warmup_semantic_classifier()

    validees, reste = [], []

    for ligne in lignes:
        entry = json.loads(ligne)
        print(f"\nQuestion            : {entry['question']}")
        print(f"Action prédite       : {entry['action_predite']}")
        print(f"Correction supposée  : {entry['correction_supposee']}")
        rep = input("Valider ? [o/N/s(kip, garder en attente)] : ").strip().lower()
        if rep == "o":
            validees.append(entry)
        elif rep == "s":
            reste.append(ligne)
        # sinon ('n' ou autre) : rejetée, retirée définitivement de la file

    _CORRECTIONS_PATH.write_text("\n".join(reste), encoding="utf-8")
    if not validees:
        print("Aucune correction validée.")
        return

    nb_inseres = 0
    for entry in validees:
        action = entry["correction_supposee"]
        if action not in sc.EXEMPLES_PAR_ACTION:
            print(f"⚠️  Action '{action}' introuvable dans semantic_examples.yaml, ignorée.")
            continue
        ok = await sc.inserer_exemple_valide(action, entry["question"])
        if ok:
            nb_inseres += 1

    print(f"\n✅ {nb_inseres} exemple(s) ajouté(s) à semantic_examples.yaml. "
          f"Centroïdes recalculés, cache mis à jour.")


def main():
    asyncio.run(_main_async())


if __name__ == "__main__":
    main()
