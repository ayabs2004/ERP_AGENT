"""Module for semi‑automatic supervised learning management.

Handles the lifecycle of model predictions, captures user signals,
evaluates candidates based on score and margin thresholds, aggregates
candidates for human review, and inserts validated examples into the
dataset.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, asdict
from pathlib import Path

import classification.semantic_classifier as sc

_CANDIDATS_PATH = Path(os.getenv("APPRENTISSAGE_CANDIDATS_PATH", "./candidats_apprentissage.jsonl"))
_VALIDES_PATH = Path(os.getenv("APPRENTISSAGE_VALIDES_PATH", "./exemples_valides.jsonl"))
_REJETES_PATH = Path(os.getenv("APPRENTISSAGE_REJETES_PATH", "./exemples_rejetes.jsonl"))

SCORE_MIN_CANDIDAT = float(os.getenv("APPRENTISSAGE_SCORE_MIN", "0.80"))
MARGE_MIN_CANDIDAT = float(os.getenv("APPRENTISSAGE_MARGE_MIN", "0.05"))


@dataclass
class PredictionLoggee:
    """Data structure storing a single model prediction awaiting user feedback."""
    question: str
    action: str
    origine: str
    score: float
    score2: float
    timestamp: float
    confirme: bool | None = None


_predictions_en_attente: dict[str, PredictionLoggee] = {}


def _cle(question: str) -> str:
    """Normalize a question string for dictionary keys."""
    return question.strip().lower()


def enregistrer_prediction(question: str, action: str, origine: str,
                          score: float, score2: float = 0.0) -> None:
    """Record a successful semantic classification for later user‑signal processing."""
    if origine not in ("SEMANTIQUE", "ARBITRAGE_LLM"):
        return
    _predictions_en_attente[_cle(question)] = PredictionLoggee(
        question=question,
        action=action,
        origine=origine,
        score=score,
        score2=score2,
        timestamp=time.time(),
    )


def enregistrer_signal_correction(question_precedente: str,
                                   action_precedente: str,
                                   nouvelle_demande: str) -> None:
    """Handle a user correction, discarding the associated prediction and logging the rejection."""
    pred = _predictions_en_attente.pop(_cle(question_precedente), None)
    if pred is None:
        return
    pred.confirme = False
    try:
        with open(_REJETES_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(pred), ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"   ⚠️  [Apprentissage] Échec persistance rejet : {e}")
    print(f"   🚫 [Apprentissage] Rejeté (correction utilisateur) : '{pred.question}' → {pred.action}")


def enregistrer_signal_confirmation(question: str) -> None:
    """Handle an implicit positive signal when no correction is observed on the next turn."""
    pred = _predictions_en_attente.pop(_cle(question), None)
    if pred is None:
        return
    pred.confirme = True
    _evaluer_candidat(pred)


def _evaluer_candidat(pred: PredictionLoggee) -> None:
    """Evaluate a candidate prediction against score, margin and diversity criteria."""
    if pred.score < SCORE_MIN_CANDIDAT:
        print(f"   ℹ️  [Apprentissage] Candidat écarté (score {pred.score:.2f} < {SCORE_MIN_CANDIDAT})")
        return
    if (pred.score - pred.score2) < MARGE_MIN_CANDIDAT:
        print(f"   ℹ️  [Apprentissage] Candidat écarté (marge {pred.score - pred.score2:.3f} < {MARGE_MIN_CANDIDAT})")
        return
    exemples_existants = sc.EXEMPLES_PAR_ACTION.get(pred.action, [])
    if pred.question.strip().lower() in {e.lower() for e in exemples_existants}:
        return
    sim_max = sc.calculer_similarite_maximale(pred.question, pred.action)
    if sim_max >= 0.95:
        print(f"   ℹ️  [Apprentissage] Candidat écarté (trop similaire aux exemples existants : {sim_max:.3f} >= 0.95)")
        return
    print(f"   ✅ [Apprentissage] Candidat retenu pour revue humaine : '{pred.question}' → {pred.action}")
    try:
        with open(_CANDIDATS_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(pred), ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"   ⚠️  [Apprentissage] Échec persistance candidat : {e}")


def lister_candidats_pour_revue() -> list[dict]:
    """Aggregate candidates by (question, action) with occurrence counts and highest score, returning those with at least three occurrences."""
    if not _CANDIDATS_PATH.exists():
        return []
    agg: dict[tuple[str, str], dict] = {}
    with open(_CANDIDATS_PATH, "r", encoding="utf-8") as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            key = (d["question"].strip().lower(), d["action"])
            if key not in agg:
                agg[key] = {**d, "occurrences": 1}
            else:
                agg[key]["occurrences"] += 1
                agg[key]["score"] = max(agg[key]["score"], d["score"])
    sorted_agg = sorted(agg.values(), key=lambda x: (-x["occurrences"], -x["score"]))
    return [c for c in sorted_agg if c.get("occurrences", 1) >= 3]


async def valider_et_inserer_candidats(questions_validees: list[tuple[str, str]]) -> int:
    """Insert definitively validated examples after explicit human review.

    Args:
        questions_validees: List of (question, action) tuples approved by a reviewer.

    Returns:
        Number of examples successfully inserted.
    """
    nb_inseres = 0
    for question, action in questions_validees:
        ok = await sc.inserer_exemple_valide(action, question)
        if ok:
            nb_inseres += 1
            try:
                with open(_VALIDES_PATH, "a", encoding="utf-8") as f:
                    f.write(json.dumps({
                        "question": question,
                        "action": action,
                        "timestamp": time.time(),
                    }, ensure_ascii=False) + "\n")
            except Exception as e:
                print(f"   ⚠️  [Apprentissage] Échec journal validés : {e}")
    print(f"   💾 [Apprentissage] {nb_inseres} exemple(s) inséré(s) définitivement.")
    return nb_inseres


def purger_candidats_traites() -> None:
    """Clear the candidates file after a review session, keeping only audit logs."""
    if _CANDIDATS_PATH.exists():
        _CANDIDATS_PATH.unlink()
        print("   🧹 [Apprentissage] Fichier candidats purgé.")