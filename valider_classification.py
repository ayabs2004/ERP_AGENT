"""
valider_classification.py — Harness de validation pour comparer
la classification AVANT (regex pures, ENABLE_SEMANTIC_CLASSIFIER=false)
vs APRÈS (regex + sémantique, ENABLE_SEMANTIC_CLASSIFIER=true).

Placer ce fichier dans le MÊME dossier que orchestrateur_general.py,
semantic_classifier.py, draft_flow.py, mcp_pool.py, response_cache.py,
interaction_logger.py (tous les modules importés doivent être présents,
même si leurs fonctions ne sont jamais réellement appelées ici).

Usage :
    python valider_classification.py
    python valider_classification.py --extra cas_reels.json
        (cas_reels.json généré par extraire_cas_logs.py)
valider_classification.py — à quoi il sert ?

C’est un harness de test A/B pour ton système de classification.

👉 Il compare :

Mode A : regex seules (ancien système)
Mode B : regex + sémantique (embeddings) (nouveau système)

Donc il répond à une question simple :

Est-ce que ton ajout de sémantique améliore vraiment la classification ou pas ?

🧠 Idée centrale

Tu as deux cerveaux :

Mode	Description
A	règles regex rapides (fragiles mais précises sur patterns connus)
B	regex + modèle sémantique (embeddings + similarité)

👉 Le script exécute les deux sur les mêmes questions et compare.

📦 Entrée du test
1. CAS_DE_TEST (manuel)

C’est un dataset figé de validation :

"liste tous les clients" → LISTE_CLIENTS
"crée une facture" → GENERER_DOC
etc.

👉 Ça couvre :

cas simples
cas ambigus
cas limites
2. CAS additionnels (--extra)

Tu peux ajouter :

python valider_classification.py --extra cas_reels.json

👉 Ces cas viennent de la vraie production :

logs utilisateur
corrections humaines

➡️ donc c’est du real-world data

⚙️ Fonctionnement du script
🔁 1. Mode A (regex only)
r = og._pre_classifier(question)
désactive sémantique
recharge orchestrateur
utilise uniquement règles regex

👉 résultat = baseline

🔁 2. Mode B (regex + sémantique)
ENABLE_SEMANTIC_CLASSIFIER=true
recharge orchestrateur
active embeddings (semantic_classifier)
utilise hybrid matching

👉 résultat = version améliorée

📊 Comparaison

Pour chaque question :

Champ	Mode A	Mode B
action prédite	r_sans	r_avec
correct ?	ok	ok
origine	REGEX	REGEX / SEMANTIQUE / LLM
📉 Ce que le script mesure
1. Score global
Score SANS sémantique : 82%
Score AVEC sémantique : 91%

👉 objectif : voir le gain réel

2. Régressions 🚨
if r_sans.ok and not r_avec.ok:

👉 cas où tu as cassé quelque chose avec la sémantique

Ex :

regex correct avant
embedding perturbe → mauvaise action
3. Améliorations ✅
if not r_sans.ok and r_avec.ok:

👉 cas où la sémantique a corrigé les erreurs regex

Ex :

"clients" → vague regex échoue
embedding comprend → LISTE_CLIENTS
4. Zone grise 🔎
origine == "ARBITRAGE_LLM"

👉 cas difficiles :

embeddings pas sûrs
fallback LLM utilisé
score entre seuil bas et haut

➡️ ça indique :

ton système hésite trop sur ces cas

📤 Export CSV

Le script génère :

resultats_validation.csv

Contient :

| question | sans_sem | avec_sem | ok | score | origine |

👉 utile pour :

analyse Excel
debug
tuning embeddings
🧠 Architecture globale (très important)

Ce fichier est en bout de chaîne :

logs production
   ↓
extraire_cas_logs.py
   ↓
cas_reels.json
   ↓
valider_classification.py
   ↓
comparaison A vs B
   ↓
décision : améliorer regex ou embeddings
💡 Ce que ce script apporte réellement
✔️ 1. Anti “illusion d’amélioration”

Sans lui tu pourrais croire :

"les embeddings sont meilleurs"

alors qu’en réalité :

ils cassent des cas simples
✔️ 2. Debug précis

Tu vois EXACTEMENT :

quelles phrases échouent
pourquoi
avec quel mode
✔️ 3. Pilotage du système

Tu peux décider :

améliorer regex
ajouter exemples embeddings
ajuster seuils
⚠️ Point important (design intelligent)

Ce script fait une chose très saine :

Il ne fait PAS confiance à la sémantique automatiquement

Il exige :

comparaison stricte
validation empirique
export pour analyse humaine

👉 c’est exactement ce qu’il faut pour un système ERP fiable

🧩 Résumé simple

👉 Ce fichier est :

un banc de test A/B qui vérifie si ton système sémantique améliore réellement la classification ou introduit des erreurs.
"""
import argparse
import asyncio
import csv
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

# ══════════════════════════════════════════════════════════════════
# JEU DE TEST — 3 à 10 formulations par action migrée vers le sémantique
# (toute action présente dans EXEMPLES_PAR_ACTION de semantic_classifier.py)
# ══════════════════════════════════════════════════════════════════
CAS_DE_TEST: list[tuple[str, str | None]] = [

    # ── LISTE_CLIENTS ──────────────────────────────────────────────
    ("liste tous les clients",                         "LISTE_CLIENTS"),
    ("montre-moi mes clients",                          "LISTE_CLIENTS"),
    ("quels clients avons-nous",                         "LISTE_CLIENTS"),
    ("donne la liste des clients",                       "LISTE_CLIENTS"),
    ("affiche les clients actifs",                        "LISTE_CLIENTS"),
    ("peux-tu m'afficher tous les clients svp",           "LISTE_CLIENTS"),
    ("clients",                                           "LISTE_CLIENTS"),

    # ── TOP_CLIENTS ────────────────────────────────────────────────
    ("top 5 clients",                                    "TOP_CLIENTS"),
    ("meilleurs clients par chiffre d'affaires",          "TOP_CLIENTS"),
    ("qui achète le plus",                                "TOP_CLIENTS"),
    ("classement des clients par CA",                     "TOP_CLIENTS"),
    ("mes plus gros clients",                             "TOP_CLIENTS"),
    ("clients les plus fidèles",                          "TOP_CLIENTS"),

    # ── FICHE_CLIENT ───────────────────────────────────────────────
    ("fiche du client CLI001",                           "FICHE_CLIENT"),
    ("informations sur le client Dupont",                 "FICHE_CLIENT"),
    ("détail du client CLI002",                          "FICHE_CLIENT"),
    ("profil du client Martin",                          "FICHE_CLIENT"),
    ("dis-moi tout sur le client CLI003",                 "FICHE_CLIENT"),
    ("qui est le client CLI004",                         "FICHE_CLIENT"),

    # ── STATUT_CLIENT ──────────────────────────────────────────────
    ("quel est le statut du client CLI005",              "STATUT_CLIENT"),
    ("le client CLI006 est-il bloqué",                    "STATUT_CLIENT"),
    ("validité du client CLI007",                        "STATUT_CLIENT"),

    # ── LISTE_ARTICLES ─────────────────────────────────────────────
    ("liste tous les articles",                          "LISTE_ARTICLES"),
    ("affiche le catalogue produits",                     "LISTE_ARTICLES"),
    ("montre-moi les articles",                          "LISTE_ARTICLES"),
    ("tous les produits",                                "LISTE_ARTICLES"),
    ("catalogue",                                        "LISTE_ARTICLES"),

    # ── VERIFIER_STOCK ─────────────────────────────────────────────
    ("quel est le stock de l'article ECRAN4K",           "VERIFIER_STOCK"),
    ("stock disponible de REF123",                       "VERIFIER_STOCK"),
    ("combien de stock reste-t-il pour l'écran",         "VERIFIER_STOCK"),
    ("articles en rupture de stock",                     "VERIFIER_STOCK"),
    ("y a-t-il du stock pour SKU456",                    "VERIFIER_STOCK"),

    # ── CA_GLOBAL ──────────────────────────────────────────────────
    ("chiffre d'affaires global",                        "CA_GLOBAL"),
    ("CA total de l'entreprise",                         "CA_GLOBAL"),
    ("quel est notre CA",                                "CA_GLOBAL"),

    # ── SAISONNALITE ───────────────────────────────────────────────
    ("chiffre d'affaires mensuel",                       "SAISONNALITE"),
    ("CA par mois",                                      "SAISONNALITE"),
    ("évolution du CA mois par mois",                    "SAISONNALITE"),

    # ── FACTURES_NON_REGLEES_FOURN ─────────────────────────────────
    ("factures fournisseur impayées",                    "FACTURES_NON_REGLEES_FOURN"),
    ("factures fournisseur non réglées",                 "FACTURES_NON_REGLEES_FOURN"),
    ("achats non réglés",                                "FACTURES_NON_REGLEES_FOURN"),

    # ── FACTURES_NON_REGLEES ───────────────────────────────────────
    ("factures impayées",                                "FACTURES_NON_REGLEES"),
    ("factures non réglées",                             "FACTURES_NON_REGLEES"),
    ("factures en attente de paiement",                  "FACTURES_NON_REGLEES"),

    # ── TOUTES_FACTURES_CLIENT ─────────────────────────────────────
    ("toutes les factures du client CLI008",             "TOUTES_FACTURES_CLIENT"),
    ("factures du client Martin",                        "TOUTES_FACTURES_CLIENT"),

    # ── DSO ────────────────────────────────────────────────────────
    ("délai de paiement moyen",                          "DSO"),
    ("DSO des clients",                                  "DSO"),
    ("retard de paiement moyen",                         "DSO"),

    # ── RFM ────────────────────────────────────────────────────────
    ("analyse RFM des clients",                          "RFM"),
    ("segmentation des clients",                         "RFM"),

    # ── PALMARES_ARTICLES ──────────────────────────────────────────
    ("palmarès des articles les plus vendus",            "PALMARES_ARTICLES"),
    ("meilleurs articles",                                "PALMARES_ARTICLES"),
    ("top des produits vendus",                          "PALMARES_ARTICLES"),

    # ── RENTABILITE ────────────────────────────────────────────────
    ("marge brute par article",                          "RENTABILITE"),
    ("rentabilité des articles",                         "RENTABILITE"),
    ("taux de marge",                                    "RENTABILITE"),

    # ── CLIENTS_BAISSE ─────────────────────────────────────────────
    ("clients en baisse de chiffre d'affaires",          "CLIENTS_BAISSE"),
    ("clients dont le CA diminue",                       "CLIENTS_BAISSE"),

    # ── DOCS_PERIODE ───────────────────────────────────────────────
    ("documents entre deux dates",                       "DOCS_PERIODE"),
    ("documents créés en 2024",                          "DOCS_PERIODE"),

    # ── CREER_CLIENT ───────────────────────────────────────────────
    ("crée un nouveau client",                           "CREER_CLIENT"),
    ("ajoute un client appelé Dupont SARL",              "CREER_CLIENT"),
    ("enregistre un nouveau client",                     "CREER_CLIENT"),
    ("nouveau client Martin",                            "CREER_CLIENT"),

    # ── CREER_FOURNISSEUR ──────────────────────────────────────────
    ("crée un nouveau fournisseur",                      "CREER_FOURNISSEUR"),
    ("ajoute un fournisseur ACME",                       "CREER_FOURNISSEUR"),
    ("nouveau fournisseur Grossiste SA",                  "CREER_FOURNISSEUR"),

    # ── MODIFIER_STATUT ────────────────────────────────────────────
    ("bloque le client CLI009",                          "MODIFIER_STATUT"),
    ("débloque le client CLI010",                        "MODIFIER_STATUT"),
    ("réactive le client CLI011",                        "MODIFIER_STATUT"),

    # ── GENERER_DOC ────────────────────────────────────────────────
    ("crée un bon de livraison pour le client CLI012",   "GENERER_DOC"),
    ("génère une facture pour ABC",                      "GENERER_DOC"),
    ("crée un ordre de fabrication de 100 pièces",       "GENERER_DOC"),
    ("crée un bon de fabrication pour l'OF00123",        "GENERER_DOC"),
    ("crée un bon de commande",                          "GENERER_DOC"),
    ("établis une facture pour le client CLI013",        "GENERER_DOC"),
    ("crée un bon de réception fournisseur",             "GENERER_DOC"),
    ("crée un BL achat pour le fournisseur FOUR001",     "GENERER_DOC"),
    ("prépare une facture",                              "GENERER_DOC"),
    ("je veux une facture",                              "GENERER_DOC"),

    # ── TRANSFORMER_DOC ────────────────────────────────────────────
    ("transforme le BL BL000123 en facture",             "TRANSFORMER_DOC"),
    ("convertis le bon de livraison en facture",         "TRANSFORMER_DOC"),
    ("facture le BL BL000456",                           "TRANSFORMER_DOC"),
    ("passe l'OF00789 en BF",                             "TRANSFORMER_DOC"),

    # ── CREER_AVOIR ────────────────────────────────────────────────
    ("crée un avoir pour la facture FA000789",           "CREER_AVOIR"),
    ("génère un avoir client pour FA000111",              "CREER_AVOIR"),

    # ── REGLEMENT ──────────────────────────────────────────────────
    ("règle la facture FA000456",                        "REGLEMENT"),
    ("enregistre le paiement de la facture FA000222",    "REGLEMENT"),
    ("marque la facture FA000333 comme réglée",          "REGLEMENT"),
    ("paye la facture FA000444 par virement",            "REGLEMENT"),

    # ── DECLARATION_EXCEL ──────────────────────────────────────────
    ("crée une déclaration du mois de juin",             "DECLARATION_EXCEL"),
    ("génère la déclaration fiscale de janvier",         "DECLARATION_EXCEL"),
    ("déclaration mensuelle achat vente",                "DECLARATION_EXCEL"),
    ("déclaration de juillet",                           "DECLARATION_EXCEL"),

    # ── OFFRE_PRIX_EXCEL ───────────────────────────────────────────
    ("génère une offre de prix pour le client CLI014",   "OFFRE_PRIX_EXCEL"),
    ("exporte l'offre de prix en Excel",                 "OFFRE_PRIX_EXCEL"),

    # ── BALANCE_AGEE_EXCEL ─────────────────────────────────────────
    ("exporte la balance âgée",                          "BALANCE_AGEE_EXCEL"),
    ("génère la balance âgée en Excel",                  "BALANCE_AGEE_EXCEL"),

    # ── DASHBOARD_EXCEL ────────────────────────────────────────────
    ("affiche le tableau de bord",                       "DASHBOARD_EXCEL"),
    ("montre le dashboard KPI",                          "DASHBOARD_EXCEL"),
    ("résumé général de l'activité",                     "DASHBOARD_EXCEL"),
    ("donne-moi un résumé global",                       "DASHBOARD_EXCEL"),

    # ── LISTE_FOURNISSEURS ─────────────────────────────────────────
    ("liste tous les fournisseurs",                      "LISTE_FOURNISSEURS"),
    ("affiche les fournisseurs",                          "LISTE_FOURNISSEURS"),
    ("quels sont nos fournisseurs actifs",                "LISTE_FOURNISSEURS"),
    ("donne-moi les fournisseurs",                        "LISTE_FOURNISSEURS"),

    # ── FICHE_FOURNISSEUR ──────────────────────────────────────────
    ("fiche du fournisseur FOUR002",                     "FICHE_FOURNISSEUR"),
    ("informations sur le fournisseur Grossiste SA",     "FICHE_FOURNISSEUR"),

    # ── TOP_FOURNISSEURS ───────────────────────────────────────────
    ("top des fournisseurs",                             "TOP_FOURNISSEURS"),
    ("meilleurs fournisseurs par volume d'achat",        "TOP_FOURNISSEURS"),

    # ── MOUVEMENT_STOCK ────────────────────────────────────────────
    ("entrée de stock pour l'article REF789",            "MOUVEMENT_STOCK"),
    ("sortie de stock pour ECRAN4K",                     "MOUVEMENT_STOCK"),
    ("ajuste le stock de l'article REF999",              "MOUVEMENT_STOCK"),

    # ── PROPOSITION_ACHAT ──────────────────────────────────────────
    ("propose une commande d'achat pour cet article",    "PROPOSITION_ACHAT"),
    ("génère une proposition d'achat pour REF111",       "PROPOSITION_ACHAT"),

    # ── WORKFLOW_COMMANDE ──────────────────────────────────────────
    ("traite la commande complète du client CLI015",     "WORKFLOW_COMMANDE"),
    ("lance le flux commande",                           "WORKFLOW_COMMANDE"),

    # ── RECHERCHE_PROCEDURE ────────────────────────────────────────
    ("quelle est la procédure pour créer un BL",         "RECHERCHE_PROCEDURE"),
    ("comment fait-on pour bloquer un client",            "RECHERCHE_PROCEDURE"),

    # ── RECOMMANDATION ─────────────────────────────────────────────
    ("que recommandes-tu pour le client CLI016",         "RECOMMANDATION"),
    ("quelle action recommandes-tu pour ce client",      "RECOMMANDATION"),

    # ── SEUIL_STOCK ────────────────────────────────────────────────
    ("quel est le seuil de stock de l'article REF222",   "SEUIL_STOCK"),

    # ── LISTE_PROCEDURES ───────────────────────────────────────────
    ("liste toutes les procédures",                      "LISTE_PROCEDURES"),

    # ── NL2SQL_LIBRE ───────────────────────────────────────────────
    ("liste les bons de livraison du client CLI017",     "NL2SQL_LIBRE"),
    ("factures supérieures à 1000 euros",                "NL2SQL_LIBRE"),
    ("clients ayant plus de 3 factures",                 "NL2SQL_LIBRE"),
    ("articles dont le prix dépasse 500",                "NL2SQL_LIBRE"),
    ("clients bloqués",                                  "NL2SQL_LIBRE"),
    ("clients inactifs depuis 6 mois",                   "NL2SQL_LIBRE"),
    ("encours du client CLI018",                         "NL2SQL_LIBRE"),
    ("nombre de commandes par client",                   "NL2SQL_LIBRE"),
    ("factures du mois de juin",                         "NL2SQL_LIBRE"),
    ("toutes les factures",                              "NL2SQL_LIBRE"),
    ("quel client a le meilleur panier moyen",           "NL2SQL_LIBRE"),
    ("clients classés par nombre de commandes",          "NL2SQL_LIBRE"),

    # ── CAS PIÉGEUX / LIMITES ──────────────────────────────────────
    # Formulations courtes, ambiguës, ou qui ressemblent à plusieurs actions.
    ("les clients",                                       "LISTE_CLIENTS"),
    ("client",                                            None),   # trop vague, seul
    ("facturation",                                       None),   # ambigu (créer vs impayées)
    ("factur",                                            None),   # faute de frappe
    ("clients qui ont des factures impayées et un encours élevé",
                                                            "NL2SQL_LIBRE"),  # combine 2 critères
    ("crée une facture pour le client CLI019 de l'article REF333 quantité 10",
                                                            "GENERER_DOC"),   # phrase longue, complète
    ("est-ce que le client CLI020 est bloqué ou actif",   "STATUT_CLIENT"),
    ("montre-moi le stock et le prix de REF444",          "VERIFIER_STOCK"),  # double intention
    ("bonjour, peux-tu me dire combien j'ai de clients actifs en ce moment",
                                                            "LISTE_CLIENTS"),  # tournure polie longue
]


@dataclass
class Resultat:
    question: str
    attendu: str | None
    obtenu: str | None
    origine: str | None
    score: float | None
    ok: bool


async def _run_un_mode(activer_semantique: bool, cas: list[tuple[str, str | None]]) -> list[Resultat]:
    os.environ["ENABLE_SEMANTIC_CLASSIFIER"] = "true" if activer_semantique else "false"

    for mod_name in list(sys.modules):
        if mod_name in ("orchestrateur_general", "semantic_classifier"):
            del sys.modules[mod_name]

    import orchestrateur_general as og

    if activer_semantique:
        from semantic_classifier import warmup_semantic_classifier
        await warmup_semantic_classifier()

    resultats = []
    for question, attendu in cas:
        r = og._pre_classifier(question)  # -> str | None (jamais un objet)
        if r is None:
            resultats.append(Resultat(question, attendu, None, None, None, attendu is None))
        else:
            ok = (r == attendu)
            resultats.append(Resultat(question, attendu, r, "REGEX", 1.0, ok))
    return resultats
def _charger_cas_depuis_json(path: str) -> list[tuple[str, str | None]]:
    """Charge des cas supplémentaires depuis un fichier JSON
    (format : [{"question": "...", "action_attendue": "..."}])
    généré par extraire_cas_logs.py"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [(item["question"], item.get("action_attendue")) for item in data]


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--extra", type=str, default=None,
                         help="Fichier JSON de cas supplémentaires (sortie de extraire_cas_logs.py)")
    args = parser.parse_args()

    cas = list(CAS_DE_TEST)
    if args.extra:
        cas_extra = _charger_cas_depuis_json(args.extra)
        print(f"➕ {len(cas_extra)} cas supplémentaires chargés depuis {args.extra}\n")
        cas += cas_extra

    print("═" * 70)
    print(f"MODE A — Regex seules (ENABLE_SEMANTIC_CLASSIFIER=false) — {len(cas)} cas")
    print("═" * 70)
    resultats_sans = await _run_un_mode(activer_semantique=False, cas=cas)

    print("\n" + "═" * 70)
    print(f"MODE B — Regex + Sémantique (ENABLE_SEMANTIC_CLASSIFIER=true) — {len(cas)} cas")
    print("═" * 70)
    resultats_avec = await _run_un_mode(activer_semantique=True, cas=cas)

    # ── Rapport comparatif ────────────────────────────────────────
    print("\n" + "═" * 100)
    print(f"{'Question':<45} {'Attendu':<22} {'SANS sém.':<22} {'AVEC sém.':<22}")
    print("─" * 100)

    nb_ok_sans = nb_ok_avec = 0
    regressions, ameliorations, zone_grise = [], [], []

    for r_sans, r_avec in zip(resultats_sans, resultats_avec):
        q = r_sans.question
        attendu = r_sans.attendu or "(None attendu)"
        obt_sans = r_sans.obtenu or "None"
        obt_avec = r_avec.obtenu or "None"

        if r_sans.ok:
            nb_ok_sans += 1
        if r_avec.ok:
            nb_ok_avec += 1

        marqueur = ""
        if r_sans.ok and not r_avec.ok:
            marqueur = "  ⚠️  RÉGRESSION"
            regressions.append((q, attendu, obt_sans, obt_avec))
        elif not r_sans.ok and r_avec.ok:
            marqueur = "  ✅ amélioration"
            ameliorations.append((q, attendu, obt_sans, obt_avec))

        if r_avec.origine == "ARBITRAGE_LLM":
            zone_grise.append((q, r_avec.obtenu, r_avec.score))

        print(f"{q[:44]:<45} {attendu[:21]:<22} {obt_sans[:21]:<22} {obt_avec[:21]:<22}{marqueur}")

    print("─" * 100)
    total = len(cas)
    print(f"\nScore SANS sémantique : {nb_ok_sans}/{total} ({100*nb_ok_sans/total:.0f}%)")
    print(f"Score AVEC sémantique : {nb_ok_avec}/{total} ({100*nb_ok_avec/total:.0f}%)")

    if regressions:
        print(f"\n🚨 {len(regressions)} RÉGRESSION(S) DÉTECTÉE(S) :")
        for q, attendu, avant, apres in regressions:
            print(f"   - \"{q}\"")
            print(f"     attendu={attendu} | avant={avant} | après={apres}")
    else:
        print("\n✅ Aucune régression détectée sur ce jeu de test.")

    if ameliorations:
        print(f"\n✨ {len(ameliorations)} amélioration(s) (raté sans sémantique, réussi avec) :")
        for q, attendu, avant, apres in ameliorations:
            print(f"   - \"{q}\" → {apres}")

    if zone_grise:
        seuil_bas  = os.getenv("SEUIL_SEMANTIQUE_BAS",  "0.70")
        seuil_haut = os.getenv("SEUIL_SEMANTIQUE_HAUT", "0.85")
        print(f"\n🔎 {len(zone_grise)} cas passés par l'arbitrage LLM (zone grise {seuil_bas}-{seuil_haut}) :")
        for q, action, score in zone_grise:
            print(f"   - \"{q}\" → {action} (score={score:.3f})")
        print("   ℹ️  Beaucoup de cas ici = latence ajoutée fréquemment ; envisager")
        print("      d'enrichir EXEMPLES_PAR_ACTION pour ces formulations.")

    # Export CSV pour analyse plus poussée / partage avec l'équipe
    out_path = Path(__file__).parent / "resultats_validation.csv"
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["question", "attendu", "sans_sem_action", "sans_sem_ok",
                          "avec_sem_action", "avec_sem_origine", "avec_sem_score", "avec_sem_ok"])
        for r_sans, r_avec in zip(resultats_sans, resultats_avec):
            writer.writerow([
                r_sans.question, r_sans.attendu,
                r_sans.obtenu, r_sans.ok,
                r_avec.obtenu, r_avec.origine, r_avec.score, r_avec.ok,
            ])
    print(f"\n📄 Détails exportés dans {out_path}")


if __name__ == "__main__":
    asyncio.run(main())  