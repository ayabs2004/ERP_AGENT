"""Module to extract reliable and suspect cases from classification logs and user corrections.

It reads `logs_classification.jsonl` and `corrections_a_verifier.jsonl`, normalizes questions,
deduplicates entries keeping the most recent decision per question, separates cases into
reliable (non‑contested) and suspect (contested) datasets, adds a verification flag for
low‑confidence reliable cases, and writes the results to JSON files.
"""

import argparse
import json
import re
from collections import Counter
from pathlib import Path


def _lire_jsonl(path: Path) -> list[dict]:
    """Read a JSON Lines file and return a list of dictionaries.

    If the file does not exist, an empty list is returned. Lines that cannot be parsed
    as JSON are ignored.
    """
    if not path.exists():
        return []
    entries = []
    for ligne in path.read_text(encoding="utf-8").splitlines():
        ligne = ligne.strip()
        if not ligne:
            continue
        try:
            entries.append(json.loads(ligne))
        except json.JSONDecodeError:
            continue
    return entries


def _normaliser(question: str) -> str:
    """Normalize a question string.

    The function strips leading/trailing whitespace, converts the text to lowercase,
    and collapses consecutive whitespace characters into a single space.
    """
    return re.sub(r"\s+", " ", question.strip().lower())


def main():
    """Process logs and corrections to generate reliable and suspect case datasets.

    The function parses command‑line arguments, loads the logs and corrections,
    deduplicates entries, separates contested from non‑contested cases, flags low‑confidence
    reliable cases, and writes the resulting datasets to JSON files.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--logs", default="./logs_classification.jsonl",
                        help="Chemin vers logs_classification.jsonl")
    parser.add_argument("--corrections", default="./corrections_a_verifier.jsonl",
                        help="Chemin vers corrections_a_verifier.jsonl")
    parser.add_argument("--out", default="cas_reels.json",
                        help="Fichier JSON de sortie pour valider_classification.py")
    parser.add_argument("--out-suspects", default="cas_suspects.json",
                        help="Fichier JSON séparé pour les cas contestés (à valider à la main)")
    parser.add_argument("--min-confiance-fiable", type=float, default=0.8,
                        help="En dessous de ce seuil, un cas non contesté est quand même flaggé à vérifier")
    parser.add_argument("--min-confiance-inclure", type=float, default=None,
                        help="Exclut purement les logs sous ce seuil (optionnel, agressif)")
    args = parser.parse_args()

    logs_path = Path(args.logs)
    corrections_path = Path(args.corrections)

    print(f"📥 Lecture de {logs_path}...")
    logs = _lire_jsonl(logs_path)
    print(f"   {len(logs)} décisions loggées.")

    print(f"📥 Lecture de {corrections_path}...")
    corrections = _lire_jsonl(corrections_path)
    print(f"   {len(corrections)} correction(s) détectée(s) (non encore validées via enrichir_exemples.py).")

    questions_contestees = {_normaliser(c["question"]): c for c in corrections}

    if args.min_confiance_inclure is not None:
        avant = len(logs)
        logs = [l for l in logs if l.get("confidence", 1.0) >= args.min_confiance_inclure]
        print(f"   Filtre confidence >= {args.min_confiance_inclure} : {avant} → {len(logs)}")

    par_question: dict[str, dict] = {}
    for l in logs:
        q = l.get("question")
        if not q or not l.get("action"):
            continue
        cle = _normaliser(q)
        prec = par_question.get(cle)
        if prec is None or l.get("ts", 0) >= prec.get("ts", 0):
            par_question[cle] = l

    print(f"   Après déduplication (question normalisée, décision la plus récente conservée) : "
          f"{len(par_question)} cas uniques.")

    cas_fiables, cas_suspects = [], []
    for cle, l in par_question.items():
        confidence = l.get("confidence")
        if cle in questions_contestees:
            corr = questions_contestees[cle]
            cas_suspects.append({
                "question": l["question"],
                "action_loggee": l["action"],
                "origine_loggee": l.get("origine", ""),
                "confidence_loggee": confidence,
                "correction_supposee": corr.get("correction_supposee"),
                "message_correction": corr.get("message_correction"),
                "note": "Contesté par l'utilisateur (detecter_correction). "
                        "Vérifier manuellement avant d'assigner action_attendue.",
            })
        else:
            a_verifier = confidence is not None and confidence < args.min_confiance_fiable
            cas_fiables.append({
                "question": l["question"],
                "action_attendue": l["action"],
                "_origine_loggee": l.get("origine", ""),
                "_confiance_loggee": confidence,
                "_a_verifier": a_verifier,
            })

    origines = Counter(c["_origine_loggee"] for c in cas_fiables)
    print("\n📊 Répartition par origine (cas non contestés) :")
    for origine, nb in origines.most_common():
        print(f"   {origine or '(vide)':<18} : {nb}")

    nb_a_verifier = sum(1 for c in cas_fiables if c["_a_verifier"])

    out_path = Path(args.out)
    out_path.write_text(json.dumps(cas_fiables, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n✅ {len(cas_fiables)} cas fiables exportés dans {out_path}")
    if nb_a_verifier:
        print(f"⚠️  {nb_a_verifier} d'entre eux ont confidence < {args.min_confiance_fiable} "
              f"(_a_verifier=true) → à relire en priorité.")

    if cas_suspects:
        out_suspects_path = Path(args.out_suspects)
        out_suspects_path.write