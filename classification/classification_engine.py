"""Central decision engine that combines semantic classification and LLM suggestions.
It applies business thresholds, confidence margins, and arbitration rules to
determine the final action to be executed by the orchestrator."""

from __future__ import annotations

import os
from dataclasses import dataclass

import classification.semantic_classifier as sc

SEUIL_CONFIRMATION_ECRITURE = float(os.getenv("SEUIL_CONFIRMATION_ECRITURE", "0.95"))


def _actions_ecriture() -> set[str]:
    """Return the set of actions that involve writing to the database,
    imported lazily to avoid circular imports."""
    from api.shared import ACTIONS_ECRITURE
    return ACTIONS_ECRITURE


@dataclass
class DecisionSemantique:
    """Container for the result of the semantic classifier, including status,
    chosen action, scores, margins, thresholds, and family."""
    statut: str
    action: str | None
    score: float
    score2: float
    marge: float
    seuil_haut: float
    seuil_bas: float
    famille: str = ""


async def evaluer_semantique(question: str) -> DecisionSemantique:
    """Run the hierarchical semantic classifier on a question and compute a
    DecisionSemantique based on configured thresholds."""
    sem_action, sem_score, sem_score2 = await sc.classifier_semantique(question)
    if sem_action is None:
        return DecisionSemantique("AUCUNE_FAMILLE", None, 0.0, 0.0, 0.0, 0.0, 0.0)

    act_cfg = sc.get_action_config(sem_action)
    seuil_haut = act_cfg.get("threshold", sc.DEFAULTS.get("threshold", 0.90))
    marge_min = act_cfg.get("margin", sc.DEFAULTS.get("margin", 0.08))
    seuil_bas = seuil_haut - marge_min
    marge = sem_score - sem_score2
    famille = sc.ACTION_TO_FAMILY.get(sem_action, "")

    if sem_score >= seuil_haut or (sem_score >= seuil_bas and marge >= marge_min):
        statut = "ACCEPTE"
    elif sem_score >= seuil_bas:
        statut = "ZONE_GRISE"
    else:
        statut = "REJETE"

    return DecisionSemantique(
        statut=statut,
        action=sem_action,
        score=sem_score,
        score2=sem_score2,
        marge=marge,
        seuil_haut=seuil_haut,
        seuil_bas=seuil_bas,
        famille=famille,
    )


@dataclass
class ArbitrageResult:
    """Result of arbitrating between LLM suggestion and semantic decision,
    indicating the final action, its origin, and whether user confirmation is required."""
    action_finale: str
    origine: str
    besoin_confirmation: bool = False
    action_alternative: str | None = None


def arbitrer_llm_semantique(llm_action: str, decision_sem: DecisionSemantique) -> ArbitrageResult:
    """Arbitrate between the LLM proposed action and the semantic decision according
    to business rules and confidence thresholds."""
    if decision_sem.action is None:
        return ArbitrageResult(llm_action, "LLM")

    if llm_action == decision_sem.action:
        return ArbitrageResult(llm_action, "ARBITRAGE_LLM")

    if (
        decision_sem.action in _actions_ecriture()
        and decision_sem.score >= SEUIL_CONFIRMATION_ECRITURE
    ):
        return ArbitrageResult(
            action_finale=llm_action,
            origine="LLM",
            besoin_confirmation=True,
            action_alternative=decision_sem.action,
        )

    return ArbitrageResult(llm_action, "LLM", action_alternative=decision_sem.action)