import re
import json
import glob

with open('adaptation/db_config.json', 'r', encoding='utf-8') as f:
    config = json.load(f)

cols_cfg = config.get('columns', {})
missing = {}

col_pattern = re.compile(r"col\(\s*['\"](\w+)['\"]\s*,\s*['\"](\w+)['\"]\s*\)")

for filepath in glob.glob('**/*.py', recursive=True):
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
            for t, c in col_pattern.findall(content):
                if t in cols_cfg:
                    if c not in cols_cfg[t]:
                        missing.setdefault(t, set()).add(c)
    except Exception as e:
        pass

print("MISSING LOGICAL COLUMNS:")
for t, cols in missing.items():
    print(f"Table '{t}': {sorted(list(cols))}")
