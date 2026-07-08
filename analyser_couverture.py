"""
Ce fichier analyser_couverture.py sert à répondre à une question très précise dans ton système :

“Est-ce que mes actions (LISTE_CLIENTS, FACTURE, etc.) sont bien couvertes par assez d’exemples et utilisées dans la vraie vie ?”
analyser_couverture.py — Rapport de couverture des exemples par action
2. Ce que fait le code étape par étape
🟦 Étape 1 — Lecture du dataset (semantic_examples.yaml)
exemples_par_action[action] = len(act_data.get("examples", []))

👉 Résultat :

LISTE_CLIENTS → 25 exemples
GENERER_DOC   → 80 exemples
RFM           → 5 exemples
🟦 Étape 2 — Lecture des logs (production)
volume_par_action = Counter(l["action"] for l in logs)

👉 Résultat :

LISTE_CLIENTS → 120 requêtes utilisateurs
GENERER_DOC   → 300 requêtes
RFM           → 8 requêtes
🟦 Étape 3 — Confiance moyenne par action
confiances_par_action[action].append(confidence)

👉 Exemple :

LISTE_CLIENTS → 0.91
RFM           → 0.62 (faible)
🟦 Étape 4 — Calcul d’un “score de priorité”

C’est le cœur du script :

priorite = 0.0

if nb_ex < min_exemples:
    priorite += (min_exemples - nb_ex)

priorite += volume * 0.5

if conf_moy < 0.75:
    priorite += bonus
📌 Interprétation

Une action est PRIORITAIRE si :

elle a peu d’exemples
MAIS beaucoup de requêtes utilisateurs
OU une faible confiance
Exemple concret :
Action	Exemples	Trafic	Confiance	Priorité
RFM	5	8	0.62	🔥 très prioritaire
LISTE_CLIENTS	25	120	0.91	moyen
GENERER_DOC	80	300	0.95	faible priorité
🟦 Étape 5 — Tri et affichage
lignes.sort(key=lambda x: -x[5])

👉 Il classe du plus critique au moins critique

🟦 Étape 6 — Affichage final

Tu obtiens un tableau :

Action              Exemples   Trafic   Conf moy   Priorité
RFM                 5          8        0.62       42.3 ⚠️
LISTE_CLIENTS      25        120       0.91       31.2
...
🚨 3. Ce que le script te dit vraiment

Il te répond :

❗ “Ton dataset est-il bien équilibré ?”

Il détecte :

1. Sous-représentation
actions avec trop peu d’exemples
2. Sur-utilisation en production
actions souvent utilisées mais mal couvertes
3. Faible confiance
actions où ton modèle hésite
=========================================================================
Amélioration #5 : "1505 exemples pour 39 actions" ne dit rien sur la
répartition réelle — certaines actions peuvent avoir 100 exemples,
d'autres 10. Ce script :

  1. Compte les exemples par action dans semantic_examples.yaml.
  2. Si logs_classification.jsonl existe (produit par
     interaction_logger.logger_decision, maintenant appelé depuis
     orchestrateur_general.noeud_classifier — voir amélioration #4),
     croise avec le volume réel de trafic par action et le taux de
     décisions à faible confiance (< SEUIL_COUVERTURE_FAIBLE).
  3. Priorise : peu d'exemples + beaucoup de trafic + confiance moyenne
     basse = candidat prioritaire pour enrichissement.

Usage :
    python analyser_couverture.py
    python analyser_couverture.py --min-exemples 15
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

import yaml

CONFIG_PATH = Path("semantic_examples.yaml")
LOGS_PATH = Path("./logs_classification.jsonl")
SEUIL_COUVERTURE_FAIBLE = 0.75


def _lire_logs() -> list[dict]:
    if not LOGS_PATH.exists():
        return []
    out = []
    for ligne in LOGS_PATH.read_text(encoding="utf-8").splitlines():
        ligne = ligne.strip()
        if not ligne:
            continue
        try:
            out.append(json.loads(ligne))
        except json.JSONDecodeError:
            continue
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-exemples", type=int, default=15,
                         help="Seuil en dessous duquel une action est signalée comme sous-couverte")
    args = parser.parse_args()

    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    exemples_par_action: dict[str, int] = {}
    for _fam, fam_data in config.get("families", {}).items():
        for action, act_data in fam_data.get("actions", {}).items():
            exemples_par_action[action] = len(act_data.get("examples", []))

    logs = _lire_logs()
    volume_par_action = Counter(l["action"] for l in logs if l.get("action"))
    confiances_par_action: dict[str, list[float]] = defaultdict(list)
    for l in logs:
        if l.get("action") and l.get("confidence") is not None:
            confiances_par_action[l["action"]].append(l["confidence"])

    lignes = []
    for action, nb_ex in exemples_par_action.items():
        volume = volume_par_action.get(action, 0)
        confs = confiances_par_action.get(action, [])
        conf_moy = sum(confs) / len(confs) if confs else None
        sous_couvert = nb_ex < args.min_exemples
        # Score de priorité : sous-couverture pondérée par le trafic réel
        # et par une confiance moyenne faible (si connue).
        priorite = 0.0
        if sous_couvert:
            priorite += (args.min_exemples - nb_ex)
        priorite += volume * 0.5
        if conf_moy is not None and conf_moy < SEUIL_COUVERTURE_FAIBLE:
            priorite += (SEUIL_COUVERTURE_FAIBLE - conf_moy) * 20
        lignes.append((action, nb_ex, volume, conf_moy, sous_couvert, priorite))

    lignes.sort(key=lambda x: -x[5])

    print("═" * 88)
    print("📊 COUVERTURE DES EXEMPLES PAR ACTION")
    print("═" * 88)
    print(f"{'Action':<28}{'Exemples':>10}{'Trafic (logs)':>16}{'Conf. moy.':>12}{'Priorité':>12}")
    print("-" * 88)
    for action, nb_ex, volume, conf_moy, sous_couvert, priorite in lignes:
        conf_str = f"{conf_moy:.2f}" if conf_moy is not None else "—"
        marqueur = " ⚠️" if sous_couvert else ""
        print(f"{action:<28}{nb_ex:>10}{volume:>16}{conf_str:>12}{priorite:>12.1f}{marqueur}")

    if not logs:
        print(f"\nℹ️  {LOGS_PATH} introuvable ou vide — le tri par priorité ne reflète "
              f"que la sous-couverture brute (< {args.min_exemples} exemples), pas le "
              f"trafic réel. Lance l'agent en production (ou rejoue des cas via "
              f"extraire_cas_logs.py) pour affiner ce rapport.")

    nb_sous_couvertes = sum(1 for l in lignes if l[4])
    print(f"\n{nb_sous_couvertes} action(s) sous {args.min_exemples} exemples.")
    print("Prochaine étape : enrichir en priorité les actions en haut du tableau,")
    print("via enrichir_exemples.py (corrections validées) ou en ajoutant des")
    print("formulations manuellement dans semantic_examples.yaml, puis relancer")
    print("calibrate_thresholds.py.")


if __name__ == "__main__":
    main()
