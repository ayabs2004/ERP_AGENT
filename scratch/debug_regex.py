import sys
import re
sys.path.insert(0, '.')
from api.orchestrateur_general import _PATTERNS_PRECLASS, _MARQUEURS_NL2SQL_FORCE_RE

phrases = ["liste les fournisseurs", "CA par mois"]

for p in phrases:
    print(f"Phrase : {p}")
    for regex in _MARQUEURS_NL2SQL_FORCE_RE:
        if regex.search(p):
            print(f"  [MARQUEURS] Matched: {regex.pattern}")
    
    for pattern, action in _PATTERNS_PRECLASS:
        if re.search(pattern, p, re.IGNORECASE):
            print(f"  [PRECLASS] Matched: {pattern} -> {action}")
            break
    print("-" * 40)
