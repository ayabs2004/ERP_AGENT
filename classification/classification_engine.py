"""
classification_engine.py — Moteur de décision centralisé
Ce que ce fichier apporte à ton architecture

Avant :
❌ logique mélangée dans orchestrateur
❌ décisions incohérentes
❌ LLM dominait tout

Maintenant :

🧱 Architecture propre :
semantic_classifier
        ↓
DecisionSemantique
        ↓
classification_engine
        ↓
arbitrage LLM vs sémantique
        ↓
orchestrateur (exécution)
🧠 5. Ce fichier est quoi exactement ?

👉 Ce n’est PAS un modèle ML

👉 Ce n’est PAS un entraînement

C’est :

⚙️ un moteur de décision hybride

Il combine :

règles métier
seuils statistiques
logique de sécurité
arbitrage LLM
🔥 6. Idée clé (très importante)

Ton système suit ce principe :

🧠 “Le LLM propose, la sémantique valide, et le système arbitre selon le risque métier”
==========================================================
Objectif (amélioration #1 du plan de refonte) : sortir la logique de
DÉCISION (quel signal gagne, quand demander confirmation) de
orchestrateur_general.py pour qu'elle soit lisible et testable
indépendamment. L'extraction d'entités (client/article/quantité/pièce)
reste dans orchestrateur_general.py : elle est trop couplée aux règles
métier ERP existantes pour être déplacée sans risque de régression dans
le cadre de cette itération.

Deux responsabilités :
1. evaluer_semantique()      → verdict du classifieur sémantique
                                (ACCEPTE / ZONE_GRISE / REJETE / AUCUNE_FAMILLE)
2. arbitrer_llm_semantique()  → politique de résolution des désaccords
                                LLM vs sémantique (amélioration #3 :
                                le LLM ne gagne plus automatiquement)
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import classification.semantic_classifier as sc

# Score sémantique à partir duquel, en cas de désaccord avec le LLM sur
# une action d'ÉCRITURE (crée/modifie/règle un document en base), on
# refuse de trancher silencieusement et on demande confirmation à
# l'utilisateur plutôt que de suivre le LLM par défaut.
SEUIL_CONFIRMATION_ECRITURE = float(os.getenv("SEUIL_CONFIRMATION_ECRITURE", "0.95"))


def _actions_ecriture() -> set[str]:
    """Import tardif pour éviter le cycle d'import avec
    orchestrateur_general.py (qui importe ce module)."""
    from api.shared import ACTIONS_ECRITURE
    return ACTIONS_ECRITURE


@dataclass
class DecisionSemantique:
    statut: str                 # "ACCEPTE" | "ZONE_GRISE" | "REJETE" | "AUCUNE_FAMILLE"
    action: str | None
    score: float
    score2: float
    marge: float
    seuil_haut: float
    seuil_bas: float
    famille: str = ""


async def evaluer_semantique(question: str) -> DecisionSemantique:
    """Exécute le classifieur sémantique hiérarchique (famille → action)
    et applique les seuils (spécifiques à l'action, avec repli sur les
    valeurs par défaut) pour rendre un verdict unique.

    Remplace la logique auparavant dupliquée inline dans
    orchestrateur_general.noeud_classifier (ÉTAPE 0b)."""
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
        statut=statut, action=sem_action, score=sem_score, score2=sem_score2,
        marge=marge, seuil_haut=seuil_haut, seuil_bas=seuil_bas, famille=famille,
    )


@dataclass
class ArbitrageResult:
    action_finale: str
    origine: str                          # "SEMANTIQUE" | "LLM" | "ARBITRAGE_LLM"
    besoin_confirmation: bool = False
    action_alternative: str | None = None  # action que le sémantique proposait, si divergence


def arbitrer_llm_semantique(llm_action: str, decision_sem: DecisionSemantique) -> ArbitrageResult:
    """Politique d'arbitrage pondérée (amélioration #3).

    Avant : en cas de désaccord, le LLM gagnait toujours, y compris
    quand le score sémantique était très élevé — risqué pour les
    actions qui écrivent en base (créer client, régler une facture...).

    Maintenant :
    - Accord LLM/sémantique               → action retenue directement.
    - Désaccord, action de LECTURE        → le LLM tranche (coût d'erreur
                                             faible, se corrige facilement
                                             au tour suivant).
    - Désaccord, action d'ÉCRITURE ET
      score sémantique >= SEUIL_CONFIRMATION_ECRITURE
                                           → on NE tranche PAS : on
                                             remonte une demande de
                                             confirmation à l'utilisateur
                                             plutôt que de risquer une
                                             écriture incorrecte.
    """
    if decision_sem.action is None:
        return ArbitrageResult(llm_action, "LLM")

    if llm_action == decision_sem.action:
        return ArbitrageResult(llm_action, "ARBITRAGE_LLM")

    if (decision_sem.action in _actions_ecriture()
            and decision_sem.score >= SEUIL_CONFIRMATION_ECRITURE):
        return ArbitrageResult(
            action_finale=llm_action,
            origine="LLM",
            besoin_confirmation=True,
            action_alternative=decision_sem.action,
        )

    return ArbitrageResult(llm_action, "LLM", action_alternative=decision_sem.action)
