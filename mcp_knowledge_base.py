"""
mcp_knowledge_base.py — La Base de Connaissance (RAG)
======================================================
Rôle : Répondre aux questions sur les procédures métier, règles de gestion,
guides fournisseurs et recommandations commerciales en cherchant dans une
base vectorielle de documents internes.

STATUT ACTUEL : Stub fonctionnel — Les réponses sont des exemples simulés.
Pour la mise en production, connecter ChromaDB ou FAISS avec vos PDF internes.

Périmètre fonctionnel prévu :
  - Procédures de relance commerciale (churn)
  - Guide des fournisseurs de secours (rupture matière)
  - Règles de remise et politique tarifaire
  - Procédures de recouvrement (balance âgée)
  - Normes qualité et fiches techniques articles
"""

import json
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Sage-Knowledge-Base-RAG")

# =====================================================================
# BASE DE CONNAISSANCE SIMULÉE
# En production : remplacer par une recherche vectorielle (ChromaDB, FAISS)
# =====================================================================
KNOWLEDGE_BASE = {
    "relance_commerciale": {
        "titre": "Procédure de Relance Commerciale Client",
        "contenu": (
            "Lorsqu'un client présente une baisse d'activité de plus de 30% sur le mois, "
            "appliquer la séquence de relance suivante :\n"
            "1. J+0 : Envoi automatique d'un email de relance commerciale avec une offre de réduction de 5% sur la prochaine commande.\n"
            "2. J+7 : Si pas de réponse, appel téléphonique du commercial référent pour identifier le motif de la baisse.\n"
            "3. J+15 : Proposition d'une visite terrain ou d'une démonstration produit.\n"
            "4. J+30 : Si le client n'a toujours pas commandé, transfert au service Grands Comptes pour suivi prioritaire.\n"
            "Règle de remise maximale autorisée sans validation direction : 8%."
        ),
        "mots_cles": ["relance", "client", "baisse", "churn", "attrition", "remise", "commercial"]
    },
    "reapprovisionnement": {
        "titre": "Guide de Réapprovisionnement Fournisseurs",
        "contenu": (
            "Procédure de réapprovisionnement en cas de rupture de stock matière première :\n"
            "1. Vérifier d'abord les stocks de substitution (articles équivalents).\n"
            "2. Consulter le fournisseur principal (FOUR01 pour les composants métalliques).\n"
            "3. En cas d'indisponibilité, activer le fournisseur de secours (FOUR02).\n"
            "4. Délai de livraison standard : 5-7 jours ouvrés.\n"
            "5. Quantité minimale de commande (QMC) : 50 unités.\n"
            "6. Générer un BC automatique et notifier le responsable de production par mail.\n"
            "Seuil d'alerte stock critique : inférieur à 10 unités pour les MP stratégiques."
        ),
        "mots_cles": ["stock", "rupture", "fournisseur", "réapprovisionnement", "achat", "matière", "seuil"]
    },
    "politique_remise": {
        "titre": "Politique Tarifaire et Remises Autorisées",
        "contenu": (
            "Grille des remises commerciales autorisées selon le volume de commande :\n"
            "• Commande < 5 000 DT  → Remise max : 3%\n"
            "• Commande 5 000-20 000 DT → Remise max : 5%\n"
            "• Commande > 20 000 DT → Remise max : 8% (validation direction requise)\n\n"
            "Conditions spéciales :\n"
            "- Client fidèle (>3 ans, >50 commandes) : +2% de remise supplémentaire autorisée.\n"
            "- Règlement comptant (virement immédiat) : +1% d'escompte.\n"
            "- Toute remise > 8% nécessite l'accord écrit du Directeur Commercial."
        ),
        "mots_cles": ["remise", "tarif", "prix", "escompte", "discount", "commercial", "négociation"]
    },
    "recouvrement": {
        "titre": "Procédure de Recouvrement des Créances",
        "contenu": (
            "Processus de recouvrement des factures impayées (par ancienneté) :\n"
            "• 0-30 jours : Rappel automatique par email.\n"
            "• 31-60 jours : Mise en demeure formelle par courrier recommandé.\n"
            "• 61-90 jours : Blocage du compte client (statut → BLOQUE), suspension des livraisons.\n"
            "• +90 jours   : Transfert au service contentieux ou cabinet de recouvrement externe.\n\n"
            "Important : Toute livraison à un client ayant des impayés >60 jours nécessite "
            "un acompte de 30% avant expédition."
        ),
        "mots_cles": ["recouvrement", "impayé", "retard", "balance", "créance", "blocage", "contentieux"]
    },
    "forecasting": {
        "titre": "Méthodologie de Prévision des Ventes (Forecasting)",
        "contenu": (
            "Méthode recommandée pour l'estimation des ventes prévisionnelles :\n"
            "1. Utiliser la moyenne mobile sur les 3 derniers mois pondérée par la saisonnalité historique.\n"
            "2. Appliquer un coefficient de croissance sectoriel (estimé à +4% par an pour notre marché).\n"
            "3. Ajuster manuellement pour les promotions connues et les appels d'offres en cours.\n"
            "4. Précision cible du forecasting : erreur ≤ 15% par rapport au réalisé.\n\n"
            "Alertes automatiques à déclencher :\n"
            "- Si le forecast prédit une rupture dans les 30 jours → Lancer un OF automatique.\n"
            "- Si le forecast prédit un surplus de stock >20% sur 2 mois → Proposer une action promotionnelle."
        ),
        "mots_cles": ["forecast", "prévision", "ventes", "stock", "planification", "production"]
    }
}


def _rechercher_document(requete: str) -> dict | None:
    """Recherche naïve par mots-clés dans la base. Remplacer par embed+cosine en production."""
    requete_lower = requete.lower()
    meilleur_score = 0
    meilleur_doc = None
    for doc_id, doc in KNOWLEDGE_BASE.items():
        score = sum(1 for mot in doc["mots_cles"] if mot in requete_lower)
        if score > meilleur_score:
            meilleur_score = score
            meilleur_doc = doc
    return meilleur_doc if meilleur_score > 0 else None


# =====================================================================
# OUTILS MCP
# =====================================================================

@mcp.tool()
def rechercher_procedure(requete: str) -> str:
    """
    Recherche une procédure ou règle de gestion interne en langage naturel.
    Retourne le document le plus pertinent de la base de connaissance.

    Args:
        requete: Question ou contexte en langage naturel
                 (ex: "que faire si un client ne paie pas depuis 60 jours ?")
    """
    doc = _rechercher_document(requete)
    if not doc:
        return (
            "🔍 Aucune procédure correspondante trouvée dans la base de connaissance.\n"
            "💡 Suggestion : Contactez le service qualité ou consultez le manuel de procédures."
        )
    return (
        f"📚 PROCÉDURE INTERNE — {doc['titre']}\n"
        f"{'─'*55}\n"
        f"{doc['contenu']}"
    )


@mcp.tool()
def generer_recommandation_commerciale(
    contexte: str,
    code_client: str = "",
    indicateur: str = ""
) -> str:
    """
    Génère une recommandation d'action commerciale basée sur le contexte
    analytique fourni (baisse de commandes, chute de CA, impayés…).

    Args:
        contexte:    Description de la situation détectée
        code_client: Code du client concerné (optionnel)
        indicateur:  KPI déclencheur (ex: "baisse 30%", "DSO 75 jours")
    """
    doc = _rechercher_document(contexte)

    intro = f"🤖 RECOMMANDATION AUTOMATIQUE"
    if code_client:
        intro += f" — Client {code_client}"
    intro += "\n" + "─" * 55

    if doc:
        reco = (
            f"{intro}\n"
            f"📊 Contexte détecté : {contexte}\n"
            f"📋 Indicateur clé   : {indicateur or 'Non précisé'}\n\n"
            f"✅ Procédure applicable : {doc['titre']}\n\n"
            f"{doc['contenu'][:400]}…\n\n"
            f"⚡ Action prioritaire : Consulter la procédure complète et agir sous 48h."
        )
    else:
        reco = (
            f"{intro}\n"
            f"📊 Contexte : {contexte}\n\n"
            f"ℹ️  Aucune procédure standard trouvée pour ce cas.\n"
            f"   Recommandation générale : escalader au responsable commercial."
        )
    return reco


@mcp.tool()
def lister_procedures_disponibles() -> str:
    """
    Liste l'ensemble des procédures et guides disponibles dans la base de connaissance.
    Utile pour découvrir les ressources accessibles.
    """
    res = "📚 PROCÉDURES DISPONIBLES DANS LA BASE DE CONNAISSANCE :\n\n"
    for i, (doc_id, doc) in enumerate(KNOWLEDGE_BASE.items(), 1):
        mots = ", ".join(doc["mots_cles"][:4])
        res += f"  {i}. {doc['titre']}\n"
        res += f"     Mots-clés : {mots}\n\n"
    res += (
        "─" * 55 + "\n"
        "💡 En production, cette base sera connectée à vos documents PDF internes\n"
        "   via ChromaDB/FAISS avec recherche par similarité vectorielle (embeddings)."
    )
    return res


@mcp.tool()
def obtenir_seuil_alerte_stock(ref_article: str) -> str:
    """
    Retourne le seuil d'alerte de stock recommandé pour un article
    et la procédure de réapprovisionnement associée.

    Args:
        ref_article: Référence de l'article (ex: DALLE01, CHASSIS)
    """
    # En production : lire depuis une table F_ARTICLE étendue avec seuils personnalisés
    SEUILS_MOCK = {
        "ECRAN4K": {"seuil": 5,  "fournisseur": "FOUR01", "qmc": 10},
        "DALLE01": {"seuil": 8,  "fournisseur": "FOUR01", "qmc": 20},
        "CHASSIS": {"seuil": 10, "fournisseur": "FOUR01", "qmc": 50},
    }

    cfg = SEUILS_MOCK.get(ref_article.upper())
    if not cfg:
        return (
            f"ℹ️  Aucun seuil d'alerte configuré pour {ref_article}.\n"
            f"   Seuil générique recommandé par la politique interne : 10 unités."
        )

    return (
        f"⚙️  PARAMÈTRES D'ALERTE STOCK — {ref_article}\n"
        f"  • Seuil critique           : {cfg['seuil']} unités\n"
        f"  • Fournisseur habituel     : {cfg['fournisseur']}\n"
        f"  • Quantité min. commande   : {cfg['qmc']} unités\n\n"
        f"📋 Procédure applicable : Guide de Réapprovisionnement Fournisseurs\n"
        f"   → En cas d'alerte, générer un BC vers {cfg['fournisseur']} "
        f"pour {cfg['qmc']} unités minimum."
    )


if __name__ == "__main__":
    mcp.run()