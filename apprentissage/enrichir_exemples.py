"""Human‑in‑the‑loop script that validates automatically detected classification errors
and integrates the accepted corrections as new training examples into
`semantic_examples.yaml`. It updates the embeddings cache and recomputes the
centroids so that the semantic classifier immediately benefits from the new
data.

The workflow is:
1. Load pending corrections from ``corrections_a_verifier.jsonl``.
2. Display each correction and ask the user to accept, reject or keep it pending.
3. Rewrite the file with the still‑pending entries.
4. Insert each accepted correction into the semantic examples via
   ``sc.inserer_exemple_valide`` which updates the YAML, the embeddings cache
   and the centroids.
"""

import asyncio
import json
from pathlib import Path

import classification.semantic_classifier as sc

_CORRECTIONS_PATH = Path("./corrections_a_verifier.jsonl")


async def _main_async():
    """Load pending corrections, interactively validate them, and update the
    training data.

    The function performs the following steps:
    * Reads ``corrections_a_verifier.jsonl`` and parses each non‑empty line as JSON.
    * Warm‑up the semantic classifier to ensure embeddings are loaded.
    * For each entry, prints the question, predicted action and suggested correction,
      then prompts the user to validate (accept, reject, or keep pending).
    * Writes back the entries that were kept pending.
    * Inserts each accepted correction into the semantic examples using
      ``sc.inserer_exemple_valide`` and counts successful insertions.
    """
    if not _CORRECTIONS_PATH.exists():
        print("Aucune correction en attente.")
        return
    lignes = [
        l
        for l in _CORRECTIONS_PATH.read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]
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
        rep = input(
            "Valider ? [o/N/s(kip, garder en attente)] : "
        ).strip().lower()
        if rep == "o":
            validees.append(entry)
        elif rep == "s":
            reste.append(ligne)

    _CORRECTIONS_PATH.write_text("\n".join(reste), encoding="utf-8")
    if not validees:
        print("Aucune correction validée.")
        return

    nb_inseres = 0
    for entry in validees:
        action = entry["correction_supposee"]
        if action not in sc.EXEMPLES_PAR_ACTION:
            print(
                f"⚠️  Action '{action}' introuvable dans semantic_examples.yaml, ignorée."
            )
            continue
        ok = await sc.inserer_exemple_valide(action, entry["question"])
        if ok:
            nb_inseres += 1

    print(
        f"\n✅ {nb_inseres} exemple(s) ajouté(s) à semantic_examples.yaml. "
        f"Centroïdes recalculés, cache mis à jour."
    )


def main():
    """Run the asynchronous main routine."""
    asyncio.run(_main_async())


if __name__ == "__main__":
    main()