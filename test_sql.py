import sqlite3

conn = sqlite3.connect('entreprise_mock.db')
cursor = conn.cursor()

sql = "SELECT CT_Num, CT_Intitule FROM F_COMPTET WHERE CT_Type = 1 ORDER BY CT_Intitule"
print(f"SQL: {sql}")
cursor.execute(sql)
results = cursor.fetchall()
print(f"Résultats: {results}")
print(f"Nombre de résultats: {len(results)}")

conn.close()
