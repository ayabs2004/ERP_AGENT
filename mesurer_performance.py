"""
mesurer_performance.py
======================
Rapport de performance global de l'Agent ERP Sage.
Mesure toutes les dimensions en une seule commande.

Usage:
    python mesurer_performance.py
    python mesurer_performance.py --avec-classification   # inclut le test de classification (lent)
    python mesurer_performance.py --avec-cas-reels        # inclut les 968 cas réels (très lent)
"""
import argparse
import asyncio
import json
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent

# ─── Helpers ─────────────────────────────────────────────────────────────────

def sep(titre=""):
    line = "═" * 60
    if titre:
        print(f"\n{line}")
        print(f"  {titre}")
        print(line)
    else:
        print(line)

def ok(msg):  print(f"  ✅  {msg}")
def warn(msg): print(f"  ⚠️   {msg}")
def err(msg):  print(f"  ❌  {msg}")
def info(msg): print(f"  ℹ️   {msg}")


# ─── 1. Tests unitaires (pytest) ─────────────────────────────────────────────

def mesurer_tests_unitaires() -> dict:
    sep("1. TESTS UNITAIRES (Workflows métier)")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_document_workflows.py", "-v", "--tb=no", "-q"],
        capture_output=True, text=True, cwd=ROOT
    )
    output = result.stdout + result.stderr
    # Extraire le résumé
    passed = output.count(" passed")
    failed = output.count(" failed")
    errored = output.count(" error")
    # Chercher la ligne de résumé
    for line in output.splitlines():
        if "passed" in line or "failed" in line or "error" in line:
            summary_line = line.strip()
            break
    else:
        summary_line = output.strip().split("\n")[-1] if output else "N/A"

    total_match = None
    import re
    m = re.search(r"(\d+) passed", output)
    nb_passed = int(m.group(1)) if m else 0
    m = re.search(r"(\d+) failed", output)
    nb_failed = int(m.group(1)) if m else 0
    m = re.search(r"(\d+) error", output)
    nb_errors = int(m.group(1)) if m else 0
    total = nb_passed + nb_failed + nb_errors

    if nb_failed == 0 and nb_errors == 0 and total > 0:
        ok(f"Tous les tests passent : {nb_passed}/{total}")
    elif total == 0:
        warn("Aucun test trouvé (vérifier tests/test_document_workflows.py)")
    else:
        err(f"{nb_failed + nb_errors} test(s) en échec sur {total}")

    return {"passed": nb_passed, "failed": nb_failed, "errors": nb_errors, "total": total}


# ─── 2. Analyse des logs de classification ────────────────────────────────────

def mesurer_logs_classification() -> dict:
    sep("2. ANALYSE DES LOGS DE CLASSIFICATION")
    log_path = ROOT / "logs_classification.jsonl"
    if not log_path.exists():
        warn("logs_classification.jsonl introuvable")
        return {}

    logs = [json.loads(l) for l in log_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    total = len(logs)
    if total == 0:
        warn("Aucun log trouvé")
        return {}

    # Répartition par origine
    origines = Counter(l.get("origine", "INCONNUE") for l in logs)
    questions_uniques = len({l["question"].strip().lower() for l in logs})

    # Actions les plus fréquentes
    actions = Counter(l.get("action") for l in logs if l.get("action"))
    top5_actions = actions.most_common(5)

    # Confiance moyenne
    confidences = [l.get("confidence", 0) for l in logs if "confidence" in l]
    conf_moy = sum(confidences) / len(confidences) if confidences else 0

    # Période couverte
    timestamps = [l.get("ts", 0) for l in logs if l.get("ts")]
    if timestamps:
        debut = datetime.fromtimestamp(min(timestamps)).strftime("%d/%m/%Y")
        fin = datetime.fromtimestamp(max(timestamps)).strftime("%d/%m/%Y")
        periode = f"{debut} → {fin}"
    else:
        periode = "inconnue"

    info(f"Période analysée      : {periode}")
    info(f"Total requêtes loggées: {total}")
    info(f"Questions uniques     : {questions_uniques}")
    info(f"Confiance moyenne     : {conf_moy:.2%}")
    print()
    info("Répartition par moteur de classification :")
    for orig, nb in sorted(origines.items(), key=lambda x: -x[1]):
        pct = 100 * nb / total
        barre = "█" * int(pct / 3)
        label = "⚠️  à améliorer" if orig == "INCONNUE" else ""
        print(f"    {orig:15s}: {nb:5d} ({pct:5.1f}%) {barre} {label}")

    print()
    info("Top 5 actions demandées :")
    for action, nb in top5_actions:
        print(f"    {str(action):30s}: {nb} fois")

    # Taux de couverture (non-INCONNUE)
    nb_inconnus = origines.get("INCONNUE", 0)
    taux_couverture = 1 - nb_inconnus / total

    if taux_couverture >= 0.80:
        ok(f"Taux de couverture : {taux_couverture:.1%}")
    elif taux_couverture >= 0.60:
        warn(f"Taux de couverture : {taux_couverture:.1%} (à améliorer)")
    else:
        err(f"Taux de couverture : {taux_couverture:.1%} (critique)")

    return {
        "total_logs": total,
        "questions_uniques": questions_uniques,
        "conf_moy": conf_moy,
        "origines": dict(origines),
        "taux_couverture": taux_couverture,
    }


# ─── 3. Satisfaction utilisateur (corrections) ───────────────────────────────

def mesurer_satisfaction() -> dict:
    sep("3. SATISFACTION UTILISATEUR")
    corr_path = ROOT / "corrections_a_verifier.jsonl"
    logs_path = ROOT / "logs_classification.jsonl"

    nb_logs = 0
    if logs_path.exists():
        nb_logs = sum(1 for l in logs_path.read_text(encoding="utf-8").splitlines() if l.strip())

    nb_corrections = 0
    if corr_path.exists():
        nb_corrections = sum(1 for l in corr_path.read_text(encoding="utf-8").splitlines() if l.strip())

    if nb_logs > 0:
        taux_insatisfaction = nb_corrections / nb_logs
        taux_satisfaction = 1 - taux_insatisfaction
        if taux_satisfaction >= 0.95:
            ok(f"Satisfaction estimée  : {taux_satisfaction:.1%} ({nb_corrections} corrections / {nb_logs} requêtes)")
        elif taux_satisfaction >= 0.80:
            warn(f"Satisfaction estimée  : {taux_satisfaction:.1%} ({nb_corrections} corrections / {nb_logs} requêtes)")
        else:
            err(f"Satisfaction estimée  : {taux_satisfaction:.1%} ({nb_corrections} corrections / {nb_logs} requêtes)")
    else:
        info("Aucun log disponible pour calculer la satisfaction")
        taux_satisfaction = None

    # Candidats semi-auto
    cand_path = ROOT / "candidats_apprentissage.jsonl"
    if cand_path.exists():
        candidats = [json.loads(l) for l in cand_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        confirmes = sum(1 for c in candidats if c.get("confirme") is True)
        rejetes = sum(1 for c in candidats if c.get("confirme") is False)
        info(f"Candidats apprentissage: {len(candidats)} total, {confirmes} confirmés, {rejetes} rejetés")

    return {"nb_corrections": nb_corrections, "nb_logs": nb_logs, "satisfaction": taux_satisfaction}


# ─── 4. Santé du moteur sémantique ───────────────────────────────────────────

def mesurer_sante_semantique() -> dict:
    sep("4. SANTÉ DU MOTEUR SÉMANTIQUE")
    try:
        import yaml
        yaml_path = ROOT / "classification" / "semantic_examples.yaml"
        if not yaml_path.exists():
            warn("semantic_examples.yaml introuvable")
            return {}
        with open(yaml_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

        total_examples = 0
        nb_actions = 0
        actions_pauvres = []  # < 5 exemples
        for family, fam_data in config.get("families", {}).items():
            for action, act_data in fam_data.get("actions", {}).items():
                nb = len(act_data.get("examples", []))
                total_examples += nb
                nb_actions += 1
                if nb < 5:
                    actions_pauvres.append((action, nb))

        cache_path = ROOT / "classification" / "semantic_embeddings_cache.json"
        cache_ok = cache_path.exists()

        info(f"Actions couvertes     : {nb_actions}")
        info(f"Total exemples YAML   : {total_examples}")
        info(f"Moyenne par action    : {total_examples/nb_actions:.1f}")
        if cache_ok:
            size_mb = cache_path.stat().st_size / 1024 / 1024
            ok(f"Cache sémantique      : présent ({size_mb:.1f} MB)")
        else:
            warn("Cache sémantique      : absent (sera recalculé au prochain démarrage)")

        if actions_pauvres:
            warn(f"{len(actions_pauvres)} action(s) avec < 5 exemples (à enrichir) :")
            for act, nb in sorted(actions_pauvres, key=lambda x: x[1]):
                print(f"    {act:30s}: {nb} exemple(s)")
        else:
            ok("Toutes les actions ont ≥ 5 exemples")

        return {"total_examples": total_examples, "nb_actions": nb_actions, "actions_pauvres": len(actions_pauvres)}

    except ImportError:
        warn("Module yaml non disponible")
        return {}


# ─── 5. Classification (optionnel, lent) ─────────────────────────────────────

async def mesurer_classification(avec_cas_reels=False) -> dict:
    sep("5. SCORE DE CLASSIFICATION (NLU)")
    print("  ⏳ Lancement de valider_classification.py...")

    extra = ["--extra", "cas_reels.json"] if avec_cas_reels and (ROOT / "cas_reels.json").exists() else []
    result = subprocess.run(
        [sys.executable, "-m", "classification.valider_classification"] + extra,
        capture_output=True, text=True, cwd=ROOT, encoding="utf-8", errors="replace"
    )
    output = result.stdout + result.stderr

    import re
    score_regex = re.search(r"Score SANS sémantique\s*:\s*(\d+)/(\d+)\s*\((\d+)%\)", output)
    score_sem = re.search(r"Score AVEC sémantique\s*:\s*(\d+)/(\d+)\s*\((\d+)%\)", output)
    regression = "régression" in output.lower() and "aucune régression" not in output.lower()

    r = {"regex": None, "semantique": None, "regression": regression}
    if score_regex:
        pct = int(score_regex.group(3))
        label = f"{score_regex.group(1)}/{score_regex.group(2)} ({pct}%)"
        r["regex"] = pct
        if pct >= 80: ok(f"Regex seules       : {label}")
        elif pct >= 60: warn(f"Regex seules       : {label}")
        else: err(f"Regex seules       : {label}")

    if score_sem:
        pct = int(score_sem.group(3))
        label = f"{score_sem.group(1)}/{score_sem.group(2)} ({pct}%)"
        r["semantique"] = pct
        if pct >= 80: ok(f"Regex + Sémantique : {label}")
        elif pct >= 60: warn(f"Regex + Sémantique : {label}")
        else: err(f"Regex + Sémantique : {label}")

    if regression:
        err("Régressions détectées !")
    else:
        ok("Aucune régression")

    return r


# ─── 6. Résumé final ─────────────────────────────────────────────────────────

def afficher_resume(tests, logs, satisfaction, semantique, classification):
    sep("RÉSUMÉ GLOBAL — Agent ERP Sage")
    now = datetime.now().strftime("%d/%m/%Y %H:%M")
    print(f"  Généré le : {now}\n")

    def score_emoji(val, seuil_ok=80, seuil_warn=60):
        if val is None: return "⬜ N/A"
        if val >= seuil_ok: return f"🟢 {val}%"
        if val >= seuil_warn: return f"🟡 {val}%"
        return f"🔴 {val}%"

    # Tests unitaires
    if tests["total"] > 0:
        pct_tests = int(100 * tests["passed"] / tests["total"])
        print(f"  🔧 Tests workflow      : {score_emoji(pct_tests)}  ({tests['passed']}/{tests['total']})")
    else:
        print(f"  🔧 Tests workflow      : ⬜ Non trouvés")

    # Couverture logs
    if logs:
        pct_couv = int(100 * logs.get("taux_couverture", 0))
        print(f"  📊 Couverture logs     : {score_emoji(pct_couv)}  ({logs.get('questions_uniques', 0)} questions uniques)")

    # Satisfaction
    sat = satisfaction.get("satisfaction")
    if sat is not None:
        print(f"  😊 Satisfaction users  : {score_emoji(int(100*sat))}  ({satisfaction['nb_corrections']} corrections)")
    else:
        print(f"  😊 Satisfaction users  : ⬜ Aucune correction enregistrée (bon signe !)")

    # Sémantique
    if semantique:
        pct_sem = int(100 * semantique.get("total_examples", 0) / max(semantique.get("nb_actions", 1), 1))
        actions_pauvres = semantique.get("actions_pauvres", 0)
        etat = "🟢" if actions_pauvres == 0 else ("🟡" if actions_pauvres < 5 else "🔴")
        print(f"  🧠 Moteur sémantique   : {etat} {semantique.get('total_examples', 0)} exemples, {actions_pauvres} action(s) à enrichir")

    # Classification
    if classification:
        r = classification.get("regex")
        s = classification.get("semantique")
        reg = "✅" if not classification.get("regression") else "❌"
        print(f"  🎯 Classification NLU  : Regex={score_emoji(r)}  Sémantique={score_emoji(s)}  {reg} Régressions")

    sep()
    print("  💡 Conseils :")
    if tests.get("failed", 0) > 0:
        print("     → Corriger les tests unitaires en échec")
    if logs and logs.get("taux_couverture", 1) < 0.80:
        print("     → Continuer l'apprentissage : python apprentissage/apprendre_inconnus.py")
    if classification and classification.get("semantique", 100) < 85:
        print("     → Ajouter des exemples dans semantic_examples.yaml")
    if semantique and semantique.get("actions_pauvres", 0) > 0:
        print("     → Enrichir les actions avec peu d'exemples")
    print()


# ─── Main ─────────────────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(description="Rapport de performance Agent ERP Sage")
    parser.add_argument("--avec-classification", action="store_true", help="Inclure le test de classification (lent ~2 min)")
    parser.add_argument("--avec-cas-reels", action="store_true", help="Inclure les cas réels extraits des logs")
    args = parser.parse_args()

    print()
    print("╔══════════════════════════════════════════════════════════╗")
    print("║      RAPPORT DE PERFORMANCE — Agent ERP Sage            ║")
    print("╚══════════════════════════════════════════════════════════╝")

    tests = mesurer_tests_unitaires()
    logs = mesurer_logs_classification()
    satisfaction = mesurer_satisfaction()
    semantique = mesurer_sante_semantique()

    classification = {}
    if args.avec_classification:
        classification = await mesurer_classification(avec_cas_reels=args.avec_cas_reels)
    else:
        sep("5. SCORE DE CLASSIFICATION (NLU)")
        info("Ignoré (ajouter --avec-classification pour l'inclure)")

    afficher_resume(tests, logs, satisfaction, semantique, classification)


if __name__ == "__main__":
    asyncio.run(main())
