"""
Planner node for the orchestrator.
Extracted from orchestrateur_general.py lines 3745-3766.
"""

import asyncio
import json
import logging

logger = logging.getLogger(__name__)


async def noeud_planner(state, ACTIONS_LECTURE, ACTIONS_EXPORT, ACTIONS_KB, ACTIONS_NL2SQL, 
                         _invoke_llm, PLANNER_TIMEOUT):
    """
    Constructs an execution plan for the given action.
    For simple actions (read, export, KB, NL2SQL), uses a direct plan.
    For complex actions, uses LLM to generate a plan.
    """
    logger.info("🧩 [Planner] Construction du plan...")
    if state["action"] in (ACTIONS_LECTURE | ACTIONS_EXPORT | ACTIONS_KB | ACTIONS_NL2SQL):
        state["plan_execution"] = [{"step": 1, "action": state["action"], "reason": "direct"}]
        return state
    prompt = (
        f'Tu es un planner ERP Sage 100.\nDemande : "{state["demande_brute"]}"\n'
        f'Réponds UNIQUEMENT avec un JSON : [{{"step":1,"action":"...","reason":"..."}}]'
    )
    try:
        r = await asyncio.wait_for(_invoke_llm(prompt, use_smart=True), timeout=PLANNER_TIMEOUT)
        plan = json.loads(r.replace("```json", "").replace("```", "").strip())
        state["plan_execution"] = (
            plan if isinstance(plan, list) and plan
            else [{"step": 1, "action": state["action"], "reason": "fallback"}]
        )
    except (json.JSONDecodeError, ValueError, asyncio.TimeoutError):
        state["plan_execution"] = [{"step": 1, "action": state["action"], "reason": "fallback"}]
    return state
