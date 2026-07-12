from adaptation.db_adapter import get_connection, table, col

conn = get_connection()
cursor = conn.cursor()

T = table('clients_fournisseurs')
C_NUM, C_NOM, C_TYPE = (
    col('clients_fournisseurs', 'code'),
    col('clients_fournisseurs', 'nom'),
    col('clients_fournisseurs', 'type_tiers'),
)

print(f"=== {T} ===")
cursor.execute(f'SELECT {C_NUM}, {C_NOM}, {C_TYPE} FROM {T}')
for row in cursor.fetchall():
    print(tuple(row))

print(f"\n=== Count by {C_TYPE} ===")
cursor.execute(f'SELECT {C_TYPE}, COUNT(*) FROM {T} GROUP BY {C_TYPE}')
for row in cursor.fetchall():
    print(tuple(row))

conn.close()
