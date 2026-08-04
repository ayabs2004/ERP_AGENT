"""
reviser_candidats.py — revue humaine des candidats d'apprentissage
semi-automatique (candidats_apprentissage.jsonl), équivalent de
enrichir_exemples.py mais pour asa.lister_candidats_pour_revue().

Aucune insertion automatique : chaque candidat est affiché (question,
action prédite, score, nombre d'occurrences non contestées) et tu
réponds o/N/s. Les validés sont insérés dans semantic_examples.yaml via
apprentissage_semi_auto.valider_et_inserer_candidats() ; les rejetés et
les skippés restent tracés séparément.

Usage :
    python reviser_candidats.py
    python reviser_candidats.py --min-occurrences 1   (pour voir aussi
        les candidats encore rares, avant qu'ils atteignent le seuil de 3
        utilisé par run_learning_cycle.py)
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import apprentissage_semi_auto as asa
import classification.semantic_classifier as sc

_CANDIDATS_PATH = asa._CANDIDATS_PATH


def _lister_tous(min_occurrences: int) -> list[dict]:
    """Comme asa.lister_candidats_pour_revue() mais avec un seuil
    d'occurrences configurable (celui de la lib est fixé à 3)."""
    if not _CANDIDATS_PATH.exists():
        return []
    agg: dict[tuple[str, str], dict] = {}
    with open(_CANDIDATS_PATH, "r", encoding="utf-8") as f:
        for ligne in f:
            ligne = ligne.strip()
            if not ligne:
                continue
            try:
                d = json.loads(ligne)
            except Exception:
                continue
            key = (d["question"].strip().lower(), d["action"])
            if key not in agg:
                agg[key] = {**d, "occurrences": 1}
            else:
                agg[key]["occurrences"] += 1
                agg[key]["score"] = max(agg[key]["score"], d["score"])
    sorted_agg = sorted(agg.values(), key=lambda x: (-x["occurrences"], -x["score"]))
    return [c for c in sorted_agg if c.get("occurrences", 1) >= min_occurrences]


async def _main_async(min_occurrences: int):
    candidats = _lister_tous(min_occurrences)
    if not candidats:
        print(f"Aucun candidat avec ≥ {min_occurrences} occurrence(s) pour l'instant.")
        return

    print("⏳ Chargement du classifieur sémantique (embeddings de référence)...")
    await sc.warmup_semantic_classifier()

    valides: list[tuple[str, str]] = []
    rejetes: list[tuple[str, str]] = []
    skippes: set[tuple[str, str]] = set()

    print(f"\n{len(candidats)} candidat(s) à revoir.\n")
    for c in candidats:
        cle = (c["question"].strip().lower(), c["action"])
        print(f"\nQuestion     : {c['question']}")
        print(f"Action       : {c['action']}")
        print(f"Score        : {c['score']:.2f}")
        print(f"Occurrences  : {c['occurrences']} (jamais corrigée par l'utilisateur)")
        rep = input("Valider ? [o/N/s(kip, garder en attente)] : ").strip().lower()
        if rep == "o":
            valides.append(cle)
        elif rep == "s":
            skippes.add(cle)
        else:
            rejetes.append(cle)

    # Réécrit le fichier brut en ne gardant que les candidats "skip"
    # (les validés et rejetés sont retirés définitivement de la file).
    lignes_restantes = []
    with open(_CANDIDATS_PATH, "r", encoding="utf-8") as f:
        for ligne in f:
            ligne_s = ligne.strip()
            if not ligne_s:
                continue
            d = json.loads(ligne_s)
            cle = (d["question"].strip().lower(), d["action"])
            if cle in skippes:
                lignes_restantes.append(ligne_s)
    _CANDIDATS_PATH.write_text(
        "\n".join(lignes_restantes) + ("\n" if lignes_restantes else ""),
        encoding="utf-8",
    )

    if valides:
        nb = await asa.valider_et_inserer_candidats(valides)
        print(f"\n✅ {nb} exemple(s) inséré(s) dans semantic_examples.yaml.")
    else:
        print("\nAucun candidat validé.")

    if rejetes:
        print(f"🚫 {len(rejetes)} candidat(s) rejeté(s) (retirés de la file, non insérés).")
    if skippes:
        print(f"⏭️  {len(skippes)} candidat(s) laissé(s) en attente (skip).")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-occurrences", type=int, default=3,
                         help="Nombre minimal d'occurrences non contestées pour proposer le candidat (défaut 3, comme run_learning_cycle.py)")
    args = parser.parse_args()
    asyncio.run(_main_async(args.min_occurrences))


if __name__ == "__main__":
    main()