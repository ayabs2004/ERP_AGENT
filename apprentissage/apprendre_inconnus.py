import json
import asyncio
from pathlib import Path
import sys

# Ajouter le répertoire racine au PYTHONPATH pour pouvoir importer classification
sys.path.insert(0, str(Path(__file__).parent.parent))
from classification.semantic_classifier import inserer_exemple_valide, warmup_semantic_classifier

ACTIONS_DISPONIBLES = [
    'LISTE_CLIENTS', 'FICHE_CLIENT', 'STATUT_CLIENT', 'TOP_CLIENTS', 'CREER_CLIENT', 'MODIFIER_STATUT', 'RECOMMANDATION',
    'LISTE_FOURNISSEURS', 'FICHE_FOURNISSEUR', 'TOP_FOURNISSEURS', 'CREER_FOURNISSEUR',
    'LISTE_ARTICLES', 'VERIFIER_STOCK', 'PALMARES_ARTICLES', 'RENTABILITE', 'SEUIL_STOCK',
    'GENERER_DOC', 'TRANSFORMER_DOC', 'CREER_AVOIR', 'REGLEMENT', 'TOUTES_FACTURES_CLIENT', 'FACTURES_NON_REGLEES', 'FACTURES_NON_REGLEES_FOURN', 'DOCS_PERIODE', 'WORKFLOW_COMMANDE',
    'CA_GLOBAL', 'SAISONNALITE', 'DSO', 'RFM', 'CLIENTS_BAISSE',
    'RECHERCHE_PROCEDURE', 'LISTE_PROCEDURES',
    'OFFRE_PRIX_EXCEL', 'DECLARATION_EXCEL', 'BALANCE_AGEE_EXCEL', 'DASHBOARD_EXCEL',
    'NL2SQL_LIBRE',
    'MOUVEMENT_STOCK', 'PROPOSITION_ACHAT'
]

async def main():
    fichier_cas = Path("cas_reels.json")
    if not fichier_cas.exists():
        print("❌ Le fichier cas_reels.json est introuvable. Exécutez d'abord l'extraction.")
        return

    print("⏳ Chargement du classifieur sémantique...")
    await warmup_semantic_classifier()

    with open(fichier_cas, "r", encoding="utf-8") as f:
        cas_reels = json.load(f)

    # Filtrer uniquement les cas qui n'ont pas été compris (INCONNUE) ou qui ont échoué
    cas_inconnus = [c for c in cas_reels if c.get("_origine_loggee") == "INCONNUE"]

    if not cas_inconnus:
        print("🎉 Aucun cas inconnu à traiter !")
        return

    print(f"\n⏳ Pré-filtrage : test de {len(cas_inconnus)} cas avec le moteur sémantique...")
    from classification.semantic_classifier import classifier_semantique
    a_labelliser = []
    deja_resolus = 0
    for cas in cas_inconnus:
        action_sem, score, _ = await classifier_semantique(cas["question"])
        if action_sem is not None:
            deja_resolus += 1
        else:
            a_labelliser.append(cas)

    print(f"✅ {deja_resolus} cas déjà résolus par la sémantique — ignorés.")
    print(f"🔴 {len(a_labelliser)} cas restants à labelliser manuellement.\n")

    if not a_labelliser:
        print("🎉 Tous les cas inconnus sont maintenant couverts par le moteur sémantique !")
        return
    print(f"🚀 Début de l'apprentissage manuel sur {len(a_labelliser)} cas.")
    print("Tapez le numéro de l'action correcte, 's' pour passer (skip), ou 'q' pour quitter.\n")

    ajouts = 0
    for i, cas in enumerate(a_labelliser):
        question = cas["question"]
        print("-" * 50)
        print(f"[{i+1}/{len(a_labelliser)}] Requête utilisateur : \033[96m\"{question}\"\033[0m")
        
        while True:
            reponse = input("Action (? pour la liste, s=skip, q=quit) > ").strip().upper()
            
            if reponse == 'Q':
                print(f"\n👋 Apprentissage interrompu. {ajouts} exemples ajoutés.")
                return
            if reponse == 'S' or reponse == '':
                break
            if reponse == '?':
                print("\n--- ACTIONS DISPONIBLES ---")
                for idx, act in enumerate(ACTIONS_DISPONIBLES):
                    print(f"{idx+1:2d}. {act}")
                print("---------------------------\n")
                continue
            
            try:
                num = int(reponse)
                if 1 <= num <= len(ACTIONS_DISPONIBLES):
                    action_choisie = ACTIONS_DISPONIBLES[num - 1]
                    # Insérer dans le YAML et mettre à jour le cache sémantique
                    await inserer_exemple_valide(question, action_choisie)
                    ajouts += 1
                    print(f"✅ Ajouté : \"{question}\" -> {action_choisie}")
                    break
                else:
                    print("❌ Numéro invalide.")
            except ValueError:
                # Si l'utilisateur tape directement le nom de l'action (ex: MODIFIER_ARTICLE)
                action_custom = reponse.strip().upper()
                if action_custom:
                    await inserer_exemple_valide(question, action_custom)
                    ajouts += 1
                    print(f"✅ Ajouté (Action personnalisée) : \"{question}\" -> {action_custom}")
                    break
                else:
                    print("❌ Entrée non reconnue. Tapez un numéro, le nom exact, 's' ou 'q'.")

    print(f"\n🎉 Terminé ! {ajouts} nouveaux exemples ont été appris par l'IA.")

if __name__ == "__main__":
    asyncio.run(main())
