"""
Simple nodes for the orchestrator.
Extracted from orchestrateur_general.py lines 3769-3814.
"""


async def noeud_hors_sujet(state, _invoke_llm, CAPACITES_SYSTEME):
    """
    Handles out-of-scope messages by responding with a brief reminder of the ERP role.
    """
    state["reponse_finale"] = await _invoke_llm(
        f'Assistant ERP Sage 100.\nMessage hors-sujet : "{state["demande_brute"]}"\n'
        f'Réponds en 1-2 phrases naturelles en rappelant ton rôle ERP.',
        use_smart=False,
    )
    return state


async def noeud_aide(state, _invoke_llm, CAPACITES_SYSTEME):
    """
    Handles help requests by listing the system's capabilities.
    """
    state["reponse_finale"] = await _invoke_llm(
        f'Assistant ERP Sage 100.\nCapacités demandées : "{state["demande_brute"]}"\n'
        f'Réponds clairement :\n{CAPACITES_SYSTEME}\nInvite à formuler une demande.',
        use_smart=False,
    )
    return state


async def noeud_clarification(state, _invoke_llm):
    """
    Handles ambiguous requests by asking for clarification.
    """
    options = state.get("_clarif_options")
    if options:
        state["reponse_finale"] = await _invoke_llm(
            f'Assistant ERP Sage 100.\nDemande : "{state["demande_brute"]}"\n'
            f'Deux interprétations possibles ont été détectées avec une confiance '
            f'proche : "{options[0]}" ou "{options[1]}". '
            f'Pose UNE question courte demandant à l\'utilisateur de choisir entre '
            f'ces deux interprétations, reformulées en langage naturel (pas de noms '
            f'de code techniques).',
            use_smart=False,
        )
    else:
        state["reponse_finale"] = await _invoke_llm(
            f'Assistant ERP Sage 100.\nDemande ambiguë : "{state["demande_brute"]}"\n'
            f'RÈGLES STRICTES :\n'
            f'1. Pose UNE seule question, en une seule phrase.\n'
            f'2. Maximum 2 exemples de réponse, très courts (un mot ou un code).\n'
            f'3. Si la demande contient déjà un opérateur numérique explicite '
            f'(<, >, "inférieur à", "supérieur à"), NE PAS demander de clarifier '
            f'ce point : il n\'est pas ambigu.\n'
            f'4. Pas de listes à puces, pas de reformulations multiples du même point.',
            use_smart=False,
        )
    return state
