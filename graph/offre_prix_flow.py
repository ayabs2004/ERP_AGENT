"""
offre_prix_flow.py — Flux Offre de Prix Multi-Articles → PDF
=============================================================
Gère le cycle complet de création d'une offre de prix :

  1. Extraction des articles depuis la demande utilisateur
  2. Pour chaque article :
     a. Calcul du prix de revient = Σ(AR_PrixAch × NO_Qte) depuis F_NOMENCLAT
     b. Prix suggéré = prix_revient × 1.30 (marge 30%)
     c. Infos produit récupérées depuis la KB vectorielle (Qdrant)
  3. Dialogue multi-tour :
     - Présentation prix de revient + prix suggéré par article
     - User confirme ou saisit un prix différent
     - Demande de remise (oui/non + %)
  4. Génération PDF avec tableau : Produit | Infos | Prix Unitaire | Remise | Prix Net
     (date en haut à droite, pas de client)

Statuts du draft offre :
  "ATTENTE_PRIX_{i}"   → en attente de validation du prix de l'article i
  "ATTENTE_REMISE"     → en attente de la réponse remise globale
  "PRET"               → toutes infos collectées, prêt pour génération PDF
  ""                   → flux terminé ou annulé
"""

from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# ─────────────────────────────────────────────────────────────────────
# Import DB helpers (réutilise les fonctions existantes)
# ─────────────────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent.parent))

from api.mcp_actions_sage import _get_conn, _resolve_article, _get_nomenclature


# ─────────────────────────────────────────────────────────────────────
# MARGE PAR DÉFAUT
# ─────────────────────────────────────────────────────────────────────
MARGE_DEFAUT = 0.30  # 30 %


# ─────────────────────────────────────────────────────────────────────
# CALCUL PRIX DE REVIENT
# ─────────────────────────────────────────────────────────────────────

def calculer_prix_revient(ref_article: str) -> dict:
    """
    Calcule le prix de revient d'un article à partir de sa nomenclature.

    Retourne :
        {
            "ref": str,
            "designation": str,
            "composants": [ {"ref", "designation", "qte", "prix_achat", "total"}, ... ],
            "prix_revient": float,
            "prix_suggere": float,   # prix_revient * (1 + MARGE_DEFAUT)
            "erreur": str | None,    # non-None si article introuvable ou pas de nomenclature
        }
    """
    conn = _get_conn()
    try:
        article = _resolve_article(conn, ref_article)
        if not article:
            return {
                "ref": ref_article,
                "designation": ref_article,
                "composants": [],
                "prix_revient": 0.0,
                "prix_suggere": 0.0,
                "erreur": f"Article '{ref_article}' introuvable dans la base.",
            }

        ref_reelle = article["AR_Ref"]
        designation = article.get("AR_Design") or ref_reelle
        composants_raw = _get_nomenclature(conn, ref_reelle)

        if not composants_raw:
            # Pas de nomenclature → utilise AR_PrixAch directement
            prix_ach = float(article.get("AR_PrixAch") or 0.0)
            return {
                "ref": ref_reelle,
                "designation": designation,
                "composants": [],
                "prix_revient": prix_ach,
                "prix_suggere": round(prix_ach * (1 + MARGE_DEFAUT), 3),
                "erreur": None,
            }

        composants = []
        prix_revient_total = 0.0
        for comp in composants_raw:
            prix_ach = comp["prix_achat"] or comp.get("prix_vente", 0.0) or 0.0
            total_ligne = comp["qte_necessaire"] * prix_ach
            prix_revient_total += total_ligne
            composants.append({
                "ref": comp["ref_composant"],
                "designation": comp["designation"],
                "qte": comp["qte_necessaire"],
                "prix_achat": round(prix_ach, 3),
                "total": round(total_ligne, 3),
            })

        return {
            "ref": ref_reelle,
            "designation": designation,
            "composants": composants,
            "prix_revient": round(prix_revient_total, 3),
            "prix_suggere": round(prix_revient_total * (1 + MARGE_DEFAUT), 3),
            "erreur": None,
        }
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────
# RECHERCHE KB
# ─────────────────────────────────────────────────────────────────────

async def rechercher_infos_kb(ref_article: str, designation: str) -> str:
    """
    Interroge la base vectorielle Qdrant pour récupérer les informations
    relatives à l'article (fabrication, utilisation, caractéristiques…).

    Retourne le texte condensé par un LLM sous forme d'un paragraphe propre,
    ou une chaîne vide si rien n'est trouvé.
    """
    try:
        from kb.indexer_kb import rechercher
        resultats = rechercher(
            f"article {ref_article} {designation} fabrication utilisation caractéristiques",
            top_k=3,
            score_min=0.0,
        )
        if not resultats:
            # Deuxième essai moins spécifique
            resultats = rechercher(f"{ref_article} {designation}", top_k=2, score_min=0.0)

        if not resultats:
            return ""

        extraits = []
        for r in resultats[:2]:
            texte = (r.get("texte") or "").strip()
            if texte:
                extraits.append(texte[:400])  # limite chaque extrait à 400 chars

        texte_brut = " | ".join(extraits)
        if not texte_brut:
            return ""
            
        from api.orchestrateur_general import _invoke_llm
        prompt = (
            f"Tu es un assistant expert en produits.\n"
            f"Voici des informations brutes extraites de fiches techniques concernant l'article '{designation}' ({ref_article}).\n"
            f"Infos : {texte_brut}\n\n"
            f"Rédige un unique paragraphe très court (2-3 phrases maximum) et propre (texte continu) "
            f"qui résume les caractéristiques, la matière ou la fabrication. "
            f"Ne mets aucun titre, aucune puce Markdown, ni de retour à la ligne. "
            f"Réponds UNIQUEMENT avec le résumé texte."
        )
        resume = await _invoke_llm(prompt, use_smart=False)
        return resume.strip()
    except Exception as e:
        print(f"   ⚠️  [KB Offre] {e}")
        return ""


# ─────────────────────────────────────────────────────────────────────
# EXTRACTION DES ARTICLES DEPUIS LA DEMANDE
# ─────────────────────────────────────────────────────────────────────

def extraire_articles_depuis_demande(demande: str) -> list[str]:
    """
    Extrait les références articles depuis une demande type :
      "crée une offre de prix pour ECRAN4K et LAPTOP"
      "génère une offre de prix pour ECRAN4K, LAPTOP, IMPRIMANTE"

    Retourne une liste de références (uppercased, sans doublons).
    """
    # Référence article = 2-20 caractères alphanumérique+tiret, souvent tout caps
    # On cherche après les mots-clés "pour", "de", ",", "et"
    refs: list[str] = []

    # Pattern : suite de tokens séparés par "et", "," après "pour"
    # On cherche des mots de 2+ chars alphanumeriques qui ressemblent à des refs
    _EXCLUS = {
        "OFFRE", "PRIX", "UNE", "UN", "DE", "POUR", "ET", "ARTICLE",
        "ARTICLES", "PRODUIT", "PRODUITS", "CRÉE", "CREE", "CREER",
        "GÉNÈRE", "GENERE", "GENERER", "FAIRE", "FAIS",
    }

    # Cherche les tokens après "pour" / "de" dans la phrase
    texte_upper = demande.upper()

    # Retire les mots vides connus
    tokens = re.findall(r"\b([A-Z][A-Z0-9\-]{1,19})\b", texte_upper)

    for tok in tokens:
        if tok not in _EXCLUS and len(tok) >= 2:
            refs.append(tok)

    # Dédoublonnage en conservant l'ordre
    seen: set[str] = set()
    result: list[str] = []
    for r in refs:
        if r not in seen:
            seen.add(r)
            result.append(r)
    return result


# ─────────────────────────────────────────────────────────────────────
# INITIALISATION DU DRAFT OFFRE
# ─────────────────────────────────────────────────────────────────────

async def initialiser_draft_offre(refs_articles: list[str]) -> dict:
    """
    Construit le draft initial de l'offre de prix.

    Structure du draft :
    {
        "type_doc": "OFFRE_PRIX",
        "date_str": "10/07/2026",
        "articles": [
            {
                "ref": "ECRAN4K",
                "designation": "...",
                "infos_kb": "...",
                "composants": [...],
                "prix_revient": 134.0,
                "prix_suggere": 174.2,
                "prix_final": None,     # à remplir par l'user
            },
            ...
        ],
        "remise_pct": None,         # % de remise global (0.0 si aucune)
        "statut_offre": "ATTENTE_PRIX_0",
        "index_courant": 0,
    }
    """
    articles = []
    for ref in refs_articles:
        calc = calculer_prix_revient(ref)
        kb_info = ""
        if not calc.get("erreur"):
            kb_info = await rechercher_infos_kb(ref, calc["designation"])
        articles.append({
            "ref": calc["ref"],
            "designation": calc["designation"],
            "infos_kb": kb_info,
            "composants": calc["composants"],
            "prix_revient": calc["prix_revient"],
            "prix_suggere": calc["prix_suggere"],
            "prix_final": None,
            "erreur": calc.get("erreur"),
        })

    return {
        "type_doc": "OFFRE_PRIX",
        "date_str": datetime.now().strftime("%d/%m/%Y"),
        "articles": articles,
        "remise_pct": None,
        "statut_offre": "ATTENTE_PRIX_0",
        "index_courant": 0,
    }


# ─────────────────────────────────────────────────────────────────────
# FORMATAGE DU MESSAGE DE SUGGESTION PRIX
# ─────────────────────────────────────────────────────────────────────

def formater_suggestion_prix(article: dict, index: int, total: int) -> str:
    """
    Génère le message multi-ligne présentant le prix de revient
    et le prix suggéré pour un article donné.
    """
    ref = article["ref"]
    designation = article["designation"]
    composants = article.get("composants", [])
    prix_revient = article["prix_revient"]
    prix_suggere = article["prix_suggere"]
    infos_kb = article.get("infos_kb", "")
    erreur = article.get("erreur")

    lignes = [
        f"📦 **Article {index + 1}/{total} — {designation} ({ref})**",
        "─" * 55,
    ]

    if erreur:
        lignes.append(f"⚠️  {erreur}")
        lignes.append(f"💡 Prix suggéré (basé sur prix d'achat ou 0) : **{prix_suggere:.3f} TND**")
        lignes.append("")
        lignes.append("✏️  Saisissez le prix unitaire souhaité (ou **CONFIRMER** pour accepter) :")
        return "\n".join(lignes)

    if infos_kb:
        lignes.append(f"📋 **Infos produit** : {infos_kb[:300]}")
        lignes.append("")

    if composants:
        lignes.append("🔧 **Composition (nomenclature) :**")
        lignes.append(
            f"{'Composant':<30} {'Qté':>5} {'Prix Ach':>10} {'Total':>10}"
        )
        lignes.append("─" * 60)
        for comp in composants:
            lignes.append(
                f"{comp['designation'][:28]:<30} {comp['qte']:>5g} "
                f"{comp['prix_achat']:>10.3f} {comp['total']:>10.3f}"
            )
        lignes.append("─" * 60)
        marge_val = prix_revient * MARGE_DEFAUT
        lignes.append(f"{'Prix de revient':<30} {'':>5} {'':>10} {prix_revient:>10.3f} TND")
        lignes.append(f"{'+ Marge 30%':<30} {'':>5} {'':>10} {marge_val:>10.3f} TND")
        lignes.append(f"{'➜ Prix suggéré':<30} {'':>5} {'':>10} {prix_suggere:>10.3f} TND")
    else:
        lignes.append(f"   Prix d'achat direct   : {prix_revient:.3f} TND")
        lignes.append(f"   + Marge 30%           : {prix_revient * MARGE_DEFAUT:.3f} TND")
        lignes.append(f"   ➜ Prix suggéré        : {prix_suggere:.3f} TND")

    lignes.append("")
    lignes.append(f"💡 Prix suggéré : **{prix_suggere:.3f} TND**")
    lignes.append("✏️  Tapez **CONFIRMER** pour accepter ce prix, ou saisissez un montant différent :")
    return "\n".join(lignes)


# ─────────────────────────────────────────────────────────────────────
# TRAITEMENT DE LA RÉPONSE PRIX
# ─────────────────────────────────────────────────────────────────────

def traiter_reponse_prix(draft: dict, texte_user: str) -> tuple[dict, str]:
    """
    Parse la réponse de l'utilisateur concernant le prix d'un article.

    - Si "confirmer"/"oui"/vide → utilise prix_suggere
    - Sinon tente de parser un montant numérique

    Retourne (draft_mis_à_jour, message_suivant)
    """
    i = draft.get("index_courant", 0)
    articles = draft.get("articles", [])

    if i >= len(articles):
        return draft, ""

    article = articles[i]
    texte = texte_user.strip().lower().rstrip("!.")

    # Mots acceptant le prix suggéré
    _MOTS_OK = {"confirmer", "confirme", "ok", "oui", "yes", "accepter", "accepte", "valider", "valide"}
    if texte in _MOTS_OK or texte == "":
        articles[i]["prix_final"] = article["prix_suggere"]
    else:
        # Essaie de parser un nombre
        m = re.search(r"(\d+(?:[.,]\d+)?)", texte_user)
        if m:
            try:
                prix_saisi = float(m.group(1).replace(",", "."))
                articles[i]["prix_final"] = round(prix_saisi, 3)
            except ValueError:
                articles[i]["prix_final"] = article["prix_suggere"]
        else:
            articles[i]["prix_final"] = article["prix_suggere"]

    prix_retenu = articles[i]["prix_final"]
    draft["articles"] = articles

    # Passe à l'article suivant ou à la question remise
    i_suivant = i + 1
    if i_suivant < len(articles):
        draft["index_courant"] = i_suivant
        draft["statut_offre"] = f"ATTENTE_PRIX_{i_suivant}"
        msg_suivant = (
            f"✅ Prix retenu pour **{article['ref']}** : {prix_retenu:.3f} TND\n\n"
            + formater_suggestion_prix(articles[i_suivant], i_suivant, len(articles))
        )
    else:
        draft["index_courant"] = i_suivant
        draft["statut_offre"] = "ATTENTE_REMISE"
        msg_suivant = (
            f"✅ Prix retenu pour **{article['ref']}** : {prix_retenu:.3f} TND\n\n"
            + ("─" * 55) + "\n"
            + "💬 Y a-t-il une **remise** sur cette offre ?\n"
            + "   → Tapez le pourcentage (ex: **10** ou **10%**) ou **non** si pas de remise."
        )

    draft["statut_offre"] = draft.get("statut_offre", "ATTENTE_REMISE")
    return draft, msg_suivant


# ─────────────────────────────────────────────────────────────────────
# TRAITEMENT DE LA RÉPONSE REMISE
# ─────────────────────────────────────────────────────────────────────

def traiter_reponse_remise(draft: dict, texte_user: str) -> tuple[dict, str]:
    """
    Parse la réponse de l'utilisateur concernant la remise.

    - "non" / "0" / "pas de remise" → remise_pct = 0.0
    - "10" / "10%" / "15,5" → remise_pct = 10.0

    Retourne (draft_mis_à_jour, message_confirmation)
    """
    texte = texte_user.strip().lower()

    _MOTS_NON = {"non", "no", "pas", "aucune", "aucun", "sans"}
    
    # Check exact match first
    if texte in _MOTS_NON or texte == "0" or texte == "0%":
        draft["remise_pct"] = 0.0
    else:
        # Check if they explicitly said something like "pas de remise"
        if "pas de remise" in texte or "sans remise" in texte:
            draft["remise_pct"] = 0.0
        else:
            m = re.search(r"(\d+(?:[.,]\d+)?)", texte_user)
            if m:
                try:
                    pct = float(m.group(1).replace(",", "."))
                    draft["remise_pct"] = min(round(pct, 2), 100.0)
                except ValueError:
                    draft["remise_pct"] = 0.0
            else:
                draft["remise_pct"] = 0.0

    draft["statut_offre"] = "PRET"

    # Résumé avant génération
    remise = draft["remise_pct"]
    lignes = ["✅ **Récapitulatif de l'offre :**", "─" * 55]
    total_ht = 0.0
    for art in draft.get("articles", []):
        prix = art.get("prix_final") or art["prix_suggere"]
        remise_val = round(prix * remise / 100, 3)
        prix_net = round(prix - remise_val, 3)
        total_ht += prix_net
        ligne_art = f"  • {art['ref']} ({art['designation'][:25]}) : {prix:.3f}"
        if remise > 0:
            ligne_art += f" - {remise:.0f}% = **{prix_net:.3f} TND**"
        else:
            ligne_art += f" TND"
        lignes.append(ligne_art)

    lignes.append("─" * 55)
    if remise > 0:
        lignes.append(f"  Remise globale : {remise:.0f}%")
    lignes.append(f"  **Total HT net : {total_ht:.3f} TND**")
    lignes.append("")
    lignes.append("⏳ Génération du PDF en cours...")

    return draft, "\n".join(lignes)


# ─────────────────────────────────────────────────────────────────────
# GÉNÉRATION PDF OFFRE DE PRIX
# ─────────────────────────────────────────────────────────────────────

async def generer_pdf_offre_prix(draft: dict) -> str:
    """
    Génère le PDF de l'offre de prix et retourne le chemin du fichier.
    Délégation asynchrone à asyncio.to_thread.
    """
    import asyncio
    return await asyncio.to_thread(_generer_pdf_offre_sync, draft)


def _generer_pdf_offre_sync(draft: dict) -> str:
    """Génération synchrone du PDF (appelée depuis asyncio.to_thread)."""
    from formatting.pdf_generator import generer_pdf_offre_prix_doc
    return generer_pdf_offre_prix_doc(draft)
