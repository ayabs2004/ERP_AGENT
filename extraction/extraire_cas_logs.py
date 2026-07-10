"""Ce fichier extraire_cas_logs.py est un outil de construction de dataset de test intelligent à partir de la vraie utilisation de ton système.

Je te l’explique clairement comme un pipeline.

🧠 Rôle global

👉 Il sert à :

transformer les logs réels de ton agent + les corrections utilisateurs en un dataset de validation propre pour tester ton classifieur

📌 D’où viennent les données ?

Il lit 2 fichiers :

1. Logs de classification
logs_classification.jsonl

Exemple :

{
  "question": "liste tous les clients",
  "action": "LISTE_CLIENTS",
  "confidence": 0.92
}

👉 ça = décisions de ton système en production

2. Corrections utilisateur
corrections_a_verifier.jsonl

Exemple :

{
  "question": "crée moi un BL",
  "action_predite": "CREER_CLIENT",
  "correction_supposee": "GENERER_DOC"
}

👉 ça = erreurs détectées via feedback utilisateur

🔁 Ce que fait le script (pipeline complet)
1. Chargement des fichiers
logs = logs_classification.jsonl
corrections = corrections_a_verifier.jsonl
2. Normalisation des questions
_normaliser(question)

👉 transforme :

majuscules → minuscules
espaces multiples supprimés

➡️ objectif : éviter doublons invisibles

3. Détection des questions contestées
questions_contestees = corrections

👉 si une question a été corrigée par un humain :

elle est marquée comme suspecte
4. Déduplication des logs
par_question[question] = dernière décision

👉 tu gardes :

une seule version par question
la plus récente
5. Séparation en 2 datasets
🟢 A) CAS FIABLES

Si la question n’a pas été contestée :

cas_fiables.append({
    "question": ...,
    "action_attendue": log_action
})

👉 c’est ton dataset “probablement correct”

⚠️ MAIS :

Tu ajoutes un flag :

"_a_verifier": confidence < seuil

👉 donc même si pas contesté :

si confiance faible → suspect léger
🔴 B) CAS SUSPECTS

Si la question a été corrigée :

cas_suspects.append(...)

Contient :

action prédite
correction humaine
message utilisateur

👉 c’est du gold data humain

📊 6. Statistiques utiles
Counter(origines)

👉 tu vois :

REGEX
SEMANTIQUE
LLM
etc.
💾 7. Export des fichiers
CAS FIABLES :
cas_reels.json
CAS SUSPECTS :
cas_suspects.json
🧠 Résumé simple
👉 ce script fait :

il transforme les logs de production + feedback humain en dataset structuré pour évaluer et améliorer ton classifieur

🔁 Dans ton pipeline global
UTILISATEUR
   ↓
orchestrateur (classification)
   ↓
logs_classification.jsonl
   ↓
corrections_a_verifier.jsonl
   ↓
extraire_cas_logs.py
   ↓
cas_reels.json
   ↓
tests / validation / calibration
extraire_cas_logs.py — Construit un jeu de cas de test réels à partir
de logs_classification.jsonl (produit par interaction_logger.logger_decision)
et corrections_a_verifier.jsonl (produit par interaction_logger.detecter_correction).

Adapté au format EXACT de interaction_logger.py :

  logs_classification.jsonl (une ligne par décision de classification) :
    {"ts": 1234.5, "question": "...", "action": "LISTE_CLIENTS",
     "origine": "REGEX_METIER", "confidence": 1.0}

  corrections_a_verifier.jsonl (une ligne par correction détectée) :
    {"ts": 1234.5, "question": "...", "action_predite": "...",
     "correction_supposee": "...", "message_correction": "non, ..."}

PRINCIPE :
  - Une question présente dans corrections_a_verifier.jsonl a été
    contestée par l'utilisateur (heuristique "non" + mot-clé) → son
    action loggée est SUSPECTE. On exclut ces cas de la vérité terrain
    automatique et on les exporte à part, avec la correction supposée
    comme SUGGESTION à valider manuellement (même logique que
    enrichir_exemples.py : jamais d'auto-validation).
  - Une question absente des corrections est supposée avoir été bien
    classifiée (personne ne l'a contestée) → utilisée comme vérité
    terrain PROBABLE pour le harness, avec un flag "_a_verifier" si sa
    confiance loggée était basse (< --min-confiance-fiable).

Ce fichier reste un POINT DE DÉPART. Relis le JSON produit avant de
l'utiliser comme --extra dans valider_classification.py.

USAGE :
    python extraire_cas_logs.py
        (utilise ./logs_classification.jsonl et ./corrections_a_verifier.jsonl
         par défaut, comme interaction_logger.py)

    python extraire_cas_logs.py --logs autre_chemin.jsonl --corrections autre.jsonl \
        --out cas_reels.json --min-confiance-fiable 0.8
"""
import argparse
import json
import re
from collections import Counter
from pathlib import Path


def _lire_jsonl(path: Path) -> list[dict]:
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
    return re.sub(r"\s+", " ", question.strip().lower())


def main():
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

    logs_path        = Path(args.logs)
    corrections_path = Path(args.corrections)

    print(f"📥 Lecture de {logs_path}...")
    logs = _lire_jsonl(logs_path)
    print(f"   {len(logs)} décisions loggées.")

    print(f"📥 Lecture de {corrections_path}...")
    corrections = _lire_jsonl(corrections_path)
    print(f"   {len(corrections)} correction(s) détectée(s) (non encore validées via enrichir_exemples.py).")

    # Questions contestées → à exclure de la vérité terrain automatique
    questions_contestees = {_normaliser(c["question"]): c for c in corrections}

    if args.min_confiance_inclure is not None:
        avant = len(logs)
        logs = [l for l in logs if l.get("confidence", 1.0) >= args.min_confiance_inclure]
        print(f"   Filtre confidence >= {args.min_confiance_inclure} : {avant} → {len(logs)}")

    # Dédup : on garde la décision la PLUS RÉCENTE par question normalisée
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

    # ── Stats utiles pour prioriser la relecture ────────────────────
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
        out_suspects_path.write_text(json.dumps(cas_suspects, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n🚨 {len(cas_suspects)} cas contestés (exclus de la vérité terrain) "
              f"exportés dans {out_suspects_path}")
        print("   Ces cas sont aussi visibles dans corrections_a_verifier.jsonl et")
        print("   doivent passer par enrichir_exemples.py pour validation humaine —")
        print("   ne les intègre pas directement comme action_attendue sans relecture.")

    print(f"\n📝 Prochaine étape :")
    print(f"   1. Ouvre {args.out} et corrige les 'action_attendue' erronées si tu en repères")
    print(f"      (priorité aux lignes _a_verifier=true).")
    print(f"   2. python valider_classification.py --extra {args.out}")


if __name__ == "__main__":
    main()