import sys
import os
sys.path.insert(0, os.path.abspath('C:/Users/Aya/Documents/agent_sage'))
from adaptation.db_adapter import get_connection, table, col
conn = get_connection()
rows = conn.execute(f"SELECT {col('articles', 'ref')}, {col('articles', 'designation')} FROM {table('articles')}").fetchall()
print("CHORFA:")
print([r for r in rows if 'CHORFA' in str(r).upper()])
print("COAR001:")
print([r for r in rows if 'COAR001' in str(r).upper()])
