import sqlite3

conn = sqlite3.connect('entreprise_mock.db')
c = conn.cursor()

# List all tables
c.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [r[0] for r in c.fetchall()]
print("=== TABLES ===")
print(tables)

# For each table, show columns
for t in tables:
    c.execute(f"PRAGMA table_info({t})")
    cols = [(r[1], r[2]) for r in c.fetchall()]
    print(f"\n=== {t} COLUMNS ===")
    for col in cols:
        print(f"  {col[0]} ({col[1]})")

# Check row counts
print("\n=== ROW COUNTS ===")
for t in tables:
    c.execute(f"SELECT COUNT(*) FROM {t}")
    print(f"  {t}: {c.fetchone()[0]} rows")

conn.close()
