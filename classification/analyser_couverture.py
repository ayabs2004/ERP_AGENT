"""Utility script that analyses the coverage of semantic examples per action.

It reads the semantic examples configuration, optionally reads production
classification logs, computes for each action the number of examples,
the traffic volume, the average confidence, and a priority score that
highlights under‑covered actions with high usage or low confidence.
The results are printed as a formatted table.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

import yaml

CONFIG_PATH = Path("kb.semantic_examples.yaml")
LOGS_PATH = Path("classification.logs_classification.jsonl")
SEUIL_COUVERTURE_FAIBLE = 0.75


def _lire_logs() -> list[dict]:
    """Read classification logs and return a list of log entries.

    Each line of the JSONL file is parsed into a dictionary. Empty lines
    and lines that cannot be decoded as JSON are ignored. If the log file
    does not exist, an empty list is returned.
    """
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
    """Generate a coverage report for each action.

    The function parses the optional ``--min-exemples`` argument, loads the
    semantic examples configuration, computes example counts per action,
    aggregates log statistics (traffic volume and confidence values),
    calculates a priority score, sorts actions by priority, and prints a
    formatted table summarising the findings.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--min-exemples",
        type=int,
        default=15,
        help="Seuil en dessous duquel une action est signalée comme sous-couverte",
    )
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
        print(
            f"\nℹ️  {LOGS_PATH} introuvable ou vide — le tri par priorité ne reflète "
            f"que la sous-couverture brute (< {args.min_exemples} exemples), pas le "
            f"trafic réel. Lance l'agent en production (ou rejoue des cas via "
            f"extraire_cas_logs.py) pour affiner ce rapport."
        )

    nb_sous_couvertes = sum(1 for l in lignes if l[4])
    print(f"\n{nb_sous_couvertes} action(s) sous {args.min_exemples} exemples.")
    print("Prochaine étape : enrichir en priorité les actions en haut du tableau,")
    print("via enrichir_exemples.py (corrections validées) ou en ajoutant des")
    print("formulations manuellement dans semantic_examples.yaml, puis relancer")
    print("calibrate_thresholds.py.")


if __name__ == "__main__":
    main()