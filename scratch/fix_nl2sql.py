"""
Fix the corrupted line 1913 in mcp_nl2sql.py - no print of unicode chars
"""
import sys
import os

os.environ['PYTHONIOENCODING'] = 'utf-8'

filepath = r'api/mcp_nl2sql.py'

with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

bad_marker = '\\r\\n))\\r\\n'
idx = content.find(bad_marker)

if idx != -1:
    # Find start: go backward to find the newline before '    }'
    start_search = content.rfind('\n    }', 0, idx)
    # Find end: the CA par annee line starts with some spaces then r"
    end_search = content.find('\n        r"(?:ca|chiffre', idx)
    
    # Build replacement
    good_chunk = '\n    }\n))\n\n_NL_PATTERNS.extend([\n    # CA par annee - FIX v4.3 : type=3 = BL, pas facture\n    ('
    
    content = content[:start_search] + good_chunk + content[end_search:]
    
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
    
    sys.stdout.buffer.write(b"Fixed and saved!\n")
else:
    sys.stdout.buffer.write(b"Bad sequence not found!\n")
