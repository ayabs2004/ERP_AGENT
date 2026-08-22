import sys
sys.path.insert(0, '.')
from api.orchestrateur_general import _pre_classifier

phrases = [
    "Quels clients sont en baisse de CA ?",
    "CA par mois",
    "liste les fournisseurs",
    "Clients qui n'ont pas commandé depuis 3 mois",
    "Top fournisseurs par volume d'achat"
]

for p in phrases:
    print(f"Phrase : {p}")
    print(f"PreClass : {_pre_classifier(p)}")
    print("-" * 40)
