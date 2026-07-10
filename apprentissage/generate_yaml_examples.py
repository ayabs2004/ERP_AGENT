import yaml
import os

# Define the 9 families and their actions
FAMILIES_ACTIONS = {
    "CLIENT": [
        "LISTE_CLIENTS", "FICHE_CLIENT", "STATUT_CLIENT", "TOP_CLIENTS", 
        "CREER_CLIENT", "MODIFIER_STATUT", "RECOMMANDATION"
    ],
    "FOURNISSEUR": [
        "LISTE_FOURNISSEURS", "FICHE_FOURNISSEUR", "TOP_FOURNISSEURS", "CREER_FOURNISSEUR"
    ],
    "ARTICLE": [
        "LISTE_ARTICLES", "VERIFIER_STOCK", "PALMARES_ARTICLES", "RENTABILITE", "SEUIL_STOCK"
    ],
    "DOCUMENT": [
        "GENERER_DOC", "TRANSFORMER_DOC", "CREER_AVOIR", "REGLEMENT", 
        "TOUTES_FACTURES_CLIENT", "FACTURES_NON_REGLEES", "FACTURES_NON_REGLEES_FOURN", 
        "DOCS_PERIODE", "WORKFLOW_COMMANDE"
    ],
    "ANALYTIQUE": [
        "CA_GLOBAL", "SAISONNALITE", "DSO", "RFM", "CLIENTS_BAISSE"
    ],
    "PROCEDURE": [
        "RECHERCHE_PROCEDURE", "LISTE_PROCEDURES"
    ],
    "EXPORT": [
        "OFFRE_PRIX_EXCEL", "DECLARATION_EXCEL", "BALANCE_AGEE_EXCEL", "DASHBOARD_EXCEL"
    ],
    "NL2SQL": [
        "NL2SQL_LIBRE"
    ],
    "STOCK_MVT": [
        "MOUVEMENT_STOCK", "PROPOSITION_ACHAT"
    ]
}

# Seed formulations for each action. We will generate variations from these.
SEEDS = {
    # CLIENT
    "LISTE_CLIENTS": [
        "liste tous les clients", "affiche les clients", "donne la liste", "je veux voir mes clients",
        "la liste client", "nos clients", "ma clientèle", "quels clients avons-nous", "affiche la clientèle",
        "qui sont nos clients", "je veux consulter les clients", "liste client", "clients actifs",
        "clients enregistrés", "consulter les comptes clients", "tous les clients", "voir nos clients",
        "obtenir la liste des clients", "liste des clients de la base", "comptes clients", "les clients",
        "quelles entreprises sont clientes", "nos clients en compte"
    ],
    "FICHE_CLIENT": [
        "fiche du client <CLIENT>", "informations sur le client <CLIENT>", "détail du client <CLIENT>",
        "profil du client <CLIENT>", "détails de <CLIENT>", "montre-moi la fiche de <CLIENT>",
        "qui est le client <CLIENT>", "données du client <CLIENT>", "consulter la fiche de <CLIENT>",
        "ouvrir la fiche client <CLIENT>", "infos du client <CLIENT>", "présente le client <CLIENT>",
        "fiche complète pour <CLIENT>", "quelles sont les infos de <CLIENT>", "donne-moi le profil de <CLIENT>",
        "fiche signalétique du client <CLIENT>", "fiche technique client <CLIENT>"
    ],
    "STATUT_CLIENT": [
        "quel est le statut du client <CLIENT>", "le client <CLIENT> est-il bloqué", "validité du client <CLIENT>",
        "quel est l'état du compte du client <CLIENT>", "vérifie le statut de <CLIENT>", "est-ce que <CLIENT> est actif",
        "statut du compte <CLIENT>", "est-ce que le client <CLIENT> est autorisé", "état de blocage du client <CLIENT>",
        "le client <CLIENT> est bloqué ou valide", "compte de <CLIENT> bloqué", "vérifier validité de <CLIENT>",
        "quel est le statut actuel du compte <CLIENT>"
    ],
    "TOP_CLIENTS": [
        "top 5 clients", "meilleurs clients par chiffre d'affaires", "classement des clients par CA",
        "clients par chiffre d'affaires", "clients qui commandent le plus", "clients les plus fidèles",
        "top acheteurs", "plus gros clients", "qui achète le plus", "qui a le plus gros CA",
        "palmarès des meilleurs clients", "top clients", "classement CA client", "qui commande le plus chez nous",
        "qui sont nos plus importants clients", "top acheteurs de l'année", "clients leaders par chiffre d affaires"
    ],
    "CREER_CLIENT": [
        "crée un nouveau client", "ajoute un client appelé <CLIENT>", "enregistre un nouveau client",
        "saisis un nouveau client", "nouveau client <CLIENT>", "création d'un client",
        "enregistre le client <CLIENT>", "ajouter le client <CLIENT> dans la base", "créer fiche client <CLIENT>",
        "saisir le nouveau client <CLIENT>", "ajouter client <CLIENT>", "créer un client"
    ],
    "MODIFIER_STATUT": [
        "bloque le client <CLIENT>", "débloque le client <CLIENT>", "réactive le client <CLIENT>",
        "modifie le statut du client <CLIENT>", "changer le statut de <CLIENT> à bloqué",
        "passer le client <CLIENT> en actif", "bloquer le compte de <CLIENT>", "débloquer le client <CLIENT> svp",
        "changer la validité du client <CLIENT>", "autoriser à nouveau le client <CLIENT>",
        "suspendre le client <CLIENT>", "lever le blocage de <CLIENT>", "activer le client <CLIENT>"
    ],
    "RECOMMANDATION": [
        "que recommandes-tu pour ce client", "quelle action recommandes-tu", "quelles sont tes suggestions pour <CLIENT>",
        "donne-moi des recommandations pour le client <CLIENT>", "que suggères-tu pour le client <CLIENT>",
        "conseils pour le client <CLIENT>", "quelles actions mener pour le client <CLIENT>",
        "recommandation commerciale pour <CLIENT>", "que devrions-nous proposer à <CLIENT>"
    ],

    # FOURNISSEUR
    "LISTE_FOURNISSEURS": [
        "liste tous les fournisseurs", "affiche les fournisseurs", "quels sont nos fournisseurs actifs",
        "montre-moi les fournisseurs", "donne-moi les fournisseurs", "liste fournisseur", "qui nous approvisionne",
        "nos fournisseurs", "consulter la liste des fournisseurs", "obtenir les fournisseurs",
        "tous les fournisseurs", "base des fournisseurs", "fournisseurs enregistrés"
    ],
    "FICHE_FOURNISSEUR": [
        "fiche du fournisseur <FOURNISSEUR>", "informations sur le fournisseur <FOURNISSEUR>",
        "détails du fournisseur <FOURNISSEUR>", "profil du fournisseur <FOURNISSEUR>",
        "consulter la fiche de <FOURNISSEUR>", "donne-moi les infos de <FOURNISSEUR>",
        "montre la fiche fournisseur <FOURNISSEUR>", "qui est le fournisseur <FOURNISSEUR>",
        "détails sur <FOURNISSEUR>", "fiche complète du fournisseur <FOURNISSEUR>"
    ],
    "TOP_FOURNISSEURS": [
        "top des fournisseurs", "meilleurs fournisseurs par volume d'achat", "achats par fournisseur",
        "classement des fournisseurs par volume", "les plus gros fournisseurs", "nos fournisseurs principaux",
        "qui nous vend le plus", "top fournisseurs par montant", "classement des fournisseurs par achats",
        "qui sont nos plus grands fournisseurs"
    ],
    "CREER_FOURNISSEUR": [
        "crée un nouveau fournisseur", "ajoute un fournisseur", "enregistre un nouveau fournisseur",
        "nouveau fournisseur <FOURNISSEUR>", "création d'un fournisseur", "saisir un nouveau fournisseur",
        "ajouter le fournisseur <FOURNISSEUR>", "créer fiche fournisseur <FOURNISSEUR>"
    ],

    # ARTICLE
    "LISTE_ARTICLES": [
        "liste tous les articles", "affiche le catalogue produits", "montre-moi les articles",
        "tous les produits", "catalogue des articles", "liste des produits", "quelles sont nos références",
        "donne la liste des articles", "nos articles", "catalogue de vente", "toutes nos pièces",
        "consulter le catalogue", "voir les articles en stock et vente"
    ],
    "VERIFIER_STOCK": [
        "quel est le stock de l'article <ARTICLE>", "stock disponible de l'article",
        "combien de stock reste-t-il", "articles en rupture de stock", "y a-t-il du stock pour <ARTICLE>",
        "vérifie le stock de <ARTICLE>", "quantité restante pour <ARTICLE>", "combien de pièces de <ARTICLE> en stock",
        "état du stock de <ARTICLE>", "vérifier la disponibilité de <ARTICLE>", "stock actuel pour <ARTICLE>",
        "disponibilité de l'article <ARTICLE>"
    ],
    "PALMARES_ARTICLES": [
        "palmarès des articles les plus vendus", "meilleurs articles", "top des produits vendus",
        "produits les plus populaires", "quelles sont nos meilleures ventes", "classement des ventes par produit",
        "les articles les plus achetés", "top ventes articles", "palmarès articles", "quels produits se vendent le mieux"
    ],
    "RENTABILITE": [
        "marge brute par article", "rentabilité des articles", "taux de marge",
        "quels articles sont les plus rentables", "marge par produit", "analyse de la rentabilité des articles",
        "quelles pièces rapportent le plus de marge", "rentabilité du catalogue", "calculer la marge des articles",
        "taux de marge brute par article"
    ],
    "SEUIL_STOCK": [
        "quel est le seuil de stock de l'article <ARTICLE>", "seuil de stock de <ARTICLE>",
        "limite de stock pour <ARTICLE>", "alerte de stock minimum pour <ARTICLE>",
        "à partir de combien de pièces commande-t-on <ARTICLE>", "seuil critique de stock pour <ARTICLE>"
    ],

    # DOCUMENT
    "GENERER_DOC": [
        "crée un bon de livraison pour le client <CLIENT>", "génère une facture pour <CLIENT>",
        "crée un ordre de fabrication de 100 pièces", "crée un bon de fabrication pour l'OF",
        "crée un bon de commande", "établis une facture pour le client <CLIENT>",
        "crée un bon de réception fournisseur", "crée un BL achat pour le fournisseur <FOURNISSEUR>",
        "génère un bon de livraison", "génère un bon", "crée un ordre de fabrication",
        "prépare une facture", "je veux une facture pour <CLIENT>", "peux-tu me générer un bon de livraison",
        "création d'un bon de commande pour le client <CLIENT>", "générer BL pour le client <CLIENT>",
        "générer une commande d'achat pour <FOURNISSEUR>", "créer un document de vente pour <CLIENT>"
    ],
    "TRANSFORMER_DOC": [
        "transforme le BL en facture", "transforme cette commande en bon de livraison",
        "convertis le bon de livraison en facture", "facture le BL <BL>", "passe le BL en facture",
        "passe l'OF en BF", "transforme l'OF en BF", "transforme le bon de fabrication en ordre de fabrication",
        "convertir le BL <BL> en facture", "transformer la commande client en bon de livraison",
        "passer la commande en BL", "générer la facture à partir du BL <BL>", "transformer le bon de livraison <BL> en facture"
    ],
    "CREER_AVOIR": [
        "crée un avoir pour la facture <FACTURE>", "génère un avoir client", "générer un avoir pour <CLIENT>",
        "créer un avoir sur la facture <FACTURE>", "avoir client pour <CLIENT>",
        "création d'un avoir pour la facture <FACTURE>", "faire un avoir pour la facture <FACTURE>"
    ],
    "REGLEMENT": [
        "règle la facture <FACTURE>", "enregistre le paiement de la facture <FACTURE>",
        "marque la facture comme réglée", "paye la facture par virement", "règlement de la facture du client",
        "change le statut de la facture en réglée", "paiement d'une facture", "enregistrer règlement de <FACTURE>",
        "valider paiement de la facture <FACTURE>", "marquer la facture <FACTURE> comme payée",
        "règlement reçu pour la facture <FACTURE>", "payer la facture <FACTURE> en chèque"
    ],
    "TOUTES_FACTURES_CLIENT": [
        "toutes les factures du client <CLIENT>", "factures du client <CLIENT>",
        "quelles factures avons-nous pour le client <CLIENT>", "liste des factures de <CLIENT>",
        "historique des factures de <CLIENT>", "obtenir toutes les factures de <CLIENT>",
        "factures de l'entreprise <CLIENT>", "les factures émises pour <CLIENT>"
    ],
    "FACTURES_NON_REGLEES": [
        "factures impayées", "factures non réglées", "factures en attente de paiement",
        "quelles factures ne sont pas réglées", "liste des impayés client", "factures clients en retard",
        "retard de paiement facture", "impayés des clients", "toutes les factures non payées",
        "factures en attente de règlement"
    ],
    "FACTURES_NON_REGLEES_FOURN": [
        "factures fournisseur impayées", "factures fournisseur non réglées", "achats non réglés",
        "nos dettes fournisseurs", "factures à payer aux fournisseurs", "liste des factures d'achat non payées",
        "quelles factures fournisseurs devons-nous régler", "impayés fournisseurs"
    ],
    "DOCS_PERIODE": [
        "documents entre deux dates", "documents créés en 2024", "liste des documents de la période",
        "quelles pièces ont été créées le mois dernier", "documents du mois", "historique des documents sur la période",
        "quelles factures entre le 1er janvier et le 31 janvier"
    ],
    "WORKFLOW_COMMANDE": [
        "traite la commande complète du client <CLIENT>", "lance le flux commande",
        "exécuter le workflow commande pour <CLIENT>", "suivi du flux de commande pour le client <CLIENT>",
        "processus complet commande client <CLIENT>", "lancer la commande du client <CLIENT>"
    ],

    # ANALYTIQUE
    "CA_GLOBAL": [
        "chiffre d'affaires global", "CA total de l'entreprise", "chiffre d'affaires annuel",
        "quel est le CA de cette année", "montant total des ventes", "CA global réalisé",
        "combien a-t-on vendu au total", "chiffre d'affaires global HT", "somme totale facturée"
    ],
    "SAISONNALITE": [
        "chiffre d'affaires mensuel", "CA par mois", "évolution du CA mois par mois",
        "graphique du CA mensuel", "ventes mensuelles de l'entreprise", "évolution des ventes par mois",
        "saisonnalité du chiffre d'affaires", "CA réalisé mois après mois", "comparatif mensuel du CA"
    ],
    "DSO": [
        "délai de paiement moyen", "DSO des clients", "retard de paiement",
        "quel est le DSO global", "délai moyen de règlement client", "days sales outstanding",
        "temps moyen pour être payé par nos clients", "délai de paiement moyen des clients"
    ],
    "RFM": [
        "analyse RFM des clients", "segmentation des clients", "score RFM de la clientèle",
        "segmentation RFM", "récence fréquence montant des clients", "classer les clients par RFM",
        "analyse de fidélité des clients"
    ],
    "CLIENTS_BAISSE": [
        "clients en baisse de chiffre d'affaires", "clients dont le CA diminue",
        "perte de chiffre d'affaires par client", "quels clients achètent moins", "clients en déclin",
        "baisse d'activité chez nos clients", "qui commande moins par rapport à l'an dernier"
    ],

    # PROCEDURE
    "RECHERCHE_PROCEDURE": [
        "quelle est la procédure pour", "comment fait-on pour", "comment fait-on pour bloquer un client",
        "comment fait-on pour débloquer un client", "quelle est la démarche pour créer un avoir",
        "comment procéder pour régler une facture", "quelle est la procédure de création d'une facture",
        "comment fait-on pour transformer un document", "procédures internes de l'entreprise",
        "comment faire pour enregistrer un règlement", "guide pour valider un stock"
    ],
    "LISTE_PROCEDURES": [
        "liste toutes les procédures", "quelles procédures sont disponibles", "liste des guides internes",
        "donne la liste des procédures", "toutes les documentations de procédures", "quelles sont les consignes"
    ],

    # EXPORT
    "OFFRE_PRIX_EXCEL": [
        "génère une offre de prix pour le client <CLIENT>", "exporte l'offre de prix en Excel",
        "générer le devis en Excel pour le client <CLIENT>", "créer offre de prix Excel",
        "exporter offre de prix en format Excel", "générer Excel devis <CLIENT>"
    ],
    "DECLARATION_EXCEL": [
        "crée une déclaration du mois de juin", "génère la déclaration fiscale de janvier",
        "déclaration mensuelle achat vente", "prépare la déclaration TVA du mois",
        "exporte la déclaration en Excel", "déclaration de juillet", "déclaration fiscale Excel",
        "générer le fichier Excel de déclaration de TVA", "déclaration achats ventes Excel"
    ],
    "BALANCE_AGEE_EXCEL": [
        "exporte la balance âgée", "génère la balance âgée en Excel", "balance âgée client Excel",
        "obtenir la balance âgée en Excel", "générer le fichier Excel de la balance âgée",
        "export Excel balance âgée"
    ],
    "DASHBOARD_EXCEL": [
        "affiche le tableau de bord", "montre le dashboard KPI", "résumé général de l'activité",
        "donne-moi un résumé global", "exporte le dashboard KPI en Excel", "générer le tableau de bord Excel",
        "dashboard Excel de l'activité"
    ],

    # NL2SQL
    "NL2SQL_LIBRE": [
        "liste les bons de livraison du client <CLIENT>", "factures supérieures à 1000 euros",
        "clients ayant plus de 3 factures", "articles dont le prix dépasse 500", "clients bloqués",
        "clients inactifs depuis 6 mois", "encours du client <CLIENT>", "nombre de commandes par client",
        "articles en stock insuffisant qui sont commandés", "factures du mois de juin", "toutes les factures",
        "quel client a le meilleur panier moyen", "quel est le ratio de marge",
        "clients classés par nombre de commandes",
        "clients qui ont des factures impayées et un encours élevé",
        "clients ayant des commandes et un solde important",
        "articles qui sont en rupture et commandés par des clients actifs"
    ],

    # STOCK_MVT
    "MOUVEMENT_STOCK": [
        "entrée de stock pour l'article <ARTICLE>", "sortie de stock", "ajuste le stock de l'article <ARTICLE>",
        "enregistre un mouvement de stock de 5 pièces", "mouvement de stock entrée", "faire une sortie de stock pour <ARTICLE>",
        "mouvementer le stock de l'article <ARTICLE>", "ajouter du stock pour l'article <ARTICLE>"
    ],
    "PROPOSITION_ACHAT": [
        "propose une commande d'achat pour cet article", "génère une proposition d'achat",
        "suggestion d'achat pour <ARTICLE>", "calculer les propositions d'achat d'articles",
        "générer une proposition de commande d'achat pour <ARTICLE>"
    ]
}

# Expand seed examples programmatically to generate 30-40 highly diverse formulations per action
# We will use prefixes/suffixes/paraphrasing templates.
PREFIXES = [
    "", "je veux ", "peux-tu ", "svp ", "veuillez ", "il faut ", "pourriez-vous ",
    "merci de ", "affiche ", "montre-moi ", "donne-moi ", "consulter ", "voir ",
    "générer ", "créer ", "obtenir ", "lancer ", "faire ", "vérifier "
]

def clean_sentence(s):
    s = s.strip().lower()
    # collapse multiple spaces
    s = " ".join(s.split())
    # remove leading/trailing punctuation commonly asked
    s = s.rstrip("?.!")
    return s

def expand_seed(seeds):
    expanded = set()
    # Add all seeds directly
    for s in seeds:
        expanded.add(clean_sentence(s))
    
    # Generate variations using prefixes for diversity
    # But only if we need more. We aim for 30-40 formulations per action.
    # To keep the quality high, we will generate grammatical permutations:
    # E.g. adding polite prefixes, changing verbs, etc.
    # We will filter to ensure we don't just add trivial variations.
    for seed in seeds:
        seed_clean = clean_sentence(seed)
        # E.g., if seed starts with "liste", we can replace "liste" with "affiche", "montre", "donne-moi la liste de", etc.
        # Let's do smart substitutions
        syns = []
        if seed_clean.startswith("liste "):
            rest = seed_clean[6:]
            syns = [f"affiche {rest}", f"montre {rest}", f"donne la liste de {rest}", f"je veux voir la liste de {rest}", f"extraire la liste de {rest}"]
        elif seed_clean.startswith("crée "):
            rest = seed_clean[5:]
            syns = [f"ajoute {rest}", f"enregistre {rest}", f"génère {rest}", f"faire la création de {rest}", f"peux-tu créer {rest}"]
        elif seed_clean.startswith("génère "):
            rest = seed_clean[7:]
            syns = [f"crée {rest}", f"édite {rest}", f"générer {rest}", f"peux-tu générer {rest}"]
        elif seed_clean.startswith("quel est "):
            rest = seed_clean[9:]
            syns = [f"donne-moi {rest}", f"vérifie {rest}", f"afficher {rest}"]
        elif seed_clean.startswith("fiche du "):
            rest = seed_clean[9:]
            syns = [f"détails du {rest}", f"profil du {rest}", f"montre-moi la fiche du {rest}", f"donne-moi les infos du {rest}"]
        elif seed_clean.startswith("statut du "):
            rest = seed_clean[10:]
            syns = [f"état du {rest}", f"vérifier le statut du {rest}", f"est-ce que le {rest} est valide"]
        
        for syn in syns:
            expanded.add(clean_sentence(syn))
            
        # Add basic polite variations
        expanded.add(clean_sentence(f"peux-tu {seed_clean}"))
        expanded.add(clean_sentence(f"je voudrais {seed_clean}"))
        expanded.add(clean_sentence(f"merci de {seed_clean}"))
        expanded.add(clean_sentence(f"est-ce possible de {seed_clean}"))

    # Return as list, sorted to ensure determinism
    result = sorted(list(expanded))
    # Limit to maximum 40 for quality and to prevent identical embeddings clutter
    return result[:40]

def main():
    print("Generating semantic examples and configuration...")
    
    # Base configuration structure
    config = {
        "version": 2,
        "defaults": {
            "threshold": 0.90,
            "margin": 0.08,
            "centroid_weight": 0.6,
            "topk_weight": 0.4,
            "family_threshold": 0.60
        },
        "families": {}
    }
    
    # Specific thresholds and margins for actions as requested/configured
    SPECIFIC_THRESHOLDS = {
        "LISTE_CLIENTS": {"threshold": 0.82, "margin": 0.06},
        "GENERER_DOC": {"threshold": 0.92, "margin": 0.08},
        "TOP_CLIENTS": {"threshold": 0.84, "margin": 0.06},
        "TRANSFORMER_DOC": {"threshold": 0.78, "margin": 0.05},
        "FICHE_CLIENT": {"threshold": 0.88, "margin": 0.08},
        "STATUT_CLIENT": {"threshold": 0.88, "margin": 0.06},
        "VERIFIER_STOCK": {"threshold": 0.85, "margin": 0.06},
        "NL2SQL_LIBRE": {"threshold": 0.85, "margin": 0.06}
    }
    
    total_examples = 0
    
    # Build families and actions hierarchy
    for family, actions in FAMILIES_ACTIONS.items():
        config["families"][family] = {
            "actions": {}
        }
        for action in actions:
            # Expand examples for this action
            seeds = SEEDS.get(action, [action.lower().replace("_", " ")])
            examples = expand_seed(seeds)
            total_examples += len(examples)
            
            action_config = {
                "examples": examples
            }
            
            # If specific thresholds exist, add them
            if action in SPECIFIC_THRESHOLDS:
                action_config["threshold"] = SPECIFIC_THRESHOLDS[action]["threshold"]
                action_config["margin"] = SPECIFIC_THRESHOLDS[action]["margin"]
                
            config["families"][family]["actions"][action] = action_config
            
    # Write to semantic_examples.yaml
    with open("semantic_examples.yaml", "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        
    print(f"Generated semantic_examples.yaml successfully.")
    print(f"Total families: {len(FAMILIES_ACTIONS)}")
    print(f"Total actions: {sum(len(a) for a in FAMILIES_ACTIONS.values())}")
    print(f"Total examples generated: {total_examples}")

if __name__ == "__main__":
    main()
