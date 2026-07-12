from adaptation.db_adapter import get_connection, table, col

conn = get_connection()
cursor = conn.cursor()

sql = (
    f"SELECT {col('clients_fournisseurs', 'code')}, {col('clients_fournisseurs', 'nom')} "
    f"FROM {table('clients_fournisseurs')} "
    f"WHERE {col('clients_fournisseurs', 'type_tiers')} = 1 "
    f"ORDER BY {col('clients_fournisseurs', 'nom')}"
)
print(f"SQL: {sql}")
cursor.execute(sql)
results = [tuple(r) for r in cursor.fetchall()]
print(f"Résultats: {results}")
print(f"Nombre de résultats: {len(results)}")

conn.close()
