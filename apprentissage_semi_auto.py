"""
apprentissage_semi_auto.py — Apprentissage supervisé semi-automatique
Ce fichier apprentissage_semi_auto.py est le cœur de ton système d’apprentissage supervisé contrôlé.

Mais attention : ce n’est pas un entraînement automatique de type ML classique.
C’est plutôt un mécanisme de mémoire + validation humaine + filtrage anti-bruit.

Je te l’explique clairement étape par étape.

🧠 1. Rôle global du fichier

👉 Il gère une boucle :

prédiction du modèle
        ↓
observation utilisateur
        ↓
confirmation ou correction
        ↓
création de candidats d’apprentissage
        ↓
validation humaine
        ↓
insertion dans le dataset

💡 Donc :

Ce fichier décide ce que le système a le droit d’apprendre ou NON
=======================================================================
Boucle : capture → validation stricte → revue humaine → insertion.

RÈGLE D'OR : score élevé + répétition ne suffisent JAMAIS à valider un
exemple. Seul un signal utilisateur explicite (absence de correction
immédiate = signal faible positif ; correction/reformulation = rejet
immédiat) peut faire d'une prédiction un CANDIDAT. Le passage de
candidat à exemple définitif exige en plus une revue humaine via
`valider_et_inserer_candidats()` — jamais d'insertion automatique
directe dans EXEMPLES_PAR_ACTION.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, asdict
from pathlib import Path

import semantic_classifier as sc

_CANDIDATS_PATH = Path(os.getenv("APPRENTISSAGE_CANDIDATS_PATH", "./candidats_apprentissage.jsonl"))
_VALIDES_PATH   = Path(os.getenv("APPRENTISSAGE_VALIDES_PATH",   "./exemples_valides.jsonl"))
_REJETES_PATH   = Path(os.getenv("APPRENTISSAGE_REJETES_PATH",   "./exemples_rejetes.jsonl"))

# En dessous de ce score, on n'apprend jamais (bruit pur)
SCORE_MIN_CANDIDAT = float(os.getenv("APPRENTISSAGE_SCORE_MIN", "0.80"))
# Marge minimale entre l'action gagnante et la 2e — évite d'apprendre
# dans les zones de confusion entre actions voisines
MARGE_MIN_CANDIDAT = float(os.getenv("APPRENTISSAGE_MARGE_MIN", "0.05"))


@dataclass
class PredictionLoggee:
    question: str
    action: str
    origine: str
    score: float
    score2: float
    timestamp: float
    confirme: bool | None = None


# Mémoire courte : la dernière prédiction en attente d'un signal
# utilisateur (confirmation implicite ou correction au tour suivant)
_predictions_en_attente: dict[str, PredictionLoggee] = {}


def _cle(question: str) -> str:
    return question.strip().lower()


def enregistrer_prediction(question: str, action: str, origine: str,
                            score: float, score2: float = 0.0) -> None:
    """Appelé à chaque classification sémantique réussie (SEMANTIQUE ou
    ARBITRAGE_LLM). Garde une trace en mémoire pour capter le signal
    utilisateur au tour suivant."""
    if origine not in ("SEMANTIQUE", "ARBITRAGE_LLM"):
        return  # on n'apprend que sur ce que le sémantique a proposé
    _predictions_en_attente[_cle(question)] = PredictionLoggee(
        question=question, action=action, origine=origine,
        score=score, score2=score2, timestamp=time.time(),
    )


def enregistrer_signal_correction(question_precedente: str,
                                   action_precedente: str,
                                   nouvelle_demande: str) -> None:
    """L'utilisateur a corrigé/reformulé juste après → la prédiction est
    définitivement écartée de l'apprentissage, journalisée pour audit."""
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
    """Aucune correction détectée au tour suivant → signal positif faible.
    Soumis ensuite aux règles de score/marge avant de devenir un
    candidat écrit sur disque pour revue humaine."""
    pred = _predictions_en_attente.pop(_cle(question), None)
    if pred is None:
        return
    pred.confirme = True
    _evaluer_candidat(pred)


def _evaluer_candidat(pred: PredictionLoggee) -> None:
    if pred.score < SCORE_MIN_CANDIDAT:
        print(f"   ℹ️  [Apprentissage] Candidat écarté (score {pred.score:.2f} < {SCORE_MIN_CANDIDAT})")
        return
    if (pred.score - pred.score2) < MARGE_MIN_CANDIDAT:
        print(f"   ℹ️  [Apprentissage] Candidat écarté (marge {pred.score - pred.score2:.3f} < {MARGE_MIN_CANDIDAT})")
        return
    exemples_existants = sc.EXEMPLES_PAR_ACTION.get(pred.action, [])
    if pred.question.strip().lower() in {e.lower() for e in exemples_existants}:
        return  # déjà présent, rien à faire

    # Diversity check: compute max similarity with existing examples of this action
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
    """Agrège les candidats par (question, action) avec compteur
    d'occurrences, triés par fréquence puis score. À utiliser dans un
    outil/CLI de revue humaine avant validation définitive.
    Ne retourne que les candidats ayant au moins 3 occurrences."""
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
    # Only keep candidates with at least 3 occurrences
    return [c for c in sorted_agg if c.get("occurrences", 1) >= 3]


async def valider_et_inserer_candidats(questions_validees: list[tuple[str, str]]) -> int:
    """
    Insertion DÉFINITIVE, uniquement après revue humaine explicite.
    `questions_validees` : liste de (question, action) approuvées.
    Retourne le nombre d'exemples effectivement insérés.
    """
    nb_inseres = 0
    for question, action in questions_validees:
        ok = await sc.inserer_exemple_valide(action, question)
        if ok:
            nb_inseres += 1
            try:
                with open(_VALIDES_PATH, "a", encoding="utf-8") as f:
                    f.write(json.dumps({
                        "question": question, "action": action,
                        "timestamp": time.time(),
                    }, ensure_ascii=False) + "\n")
            except Exception as e:
                print(f"   ⚠️  [Apprentissage] Échec journal validés : {e}")
    print(f"   💾 [Apprentissage] {nb_inseres} exemple(s) inséré(s) définitivement.")
    return nb_inseres


def purger_candidats_traites() -> None:
    """À appeler après une session de revue pour repartir sur un fichier
    candidats vide (les décisions restent tracées dans exemples_valides
    et exemples_rejetes)."""
    if _CANDIDATS_PATH.exists():
        _CANDIDATS_PATH.unlink()
        print("   🧹 [Apprentissage] Fichier candidats purgé.")