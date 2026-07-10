import sqlite3

conn = sqlite3.connect('entreprise_mock.db')
cursor = conn.cursor()

print("=== F_COMPTET ===")
cursor.execute('SELECT CT_Num, CT_Intitule, CT_Type FROM F_COMPTET')
for row in cursor.fetchall():
    print(row)

print("\n=== Count by CT_Type ===")
cursor.execute('SELECT CT_Type, COUNT(*) FROM F_COMPTET GROUP BY CT_Type')
for row in cursor.fetchall():
    print(row)

conn.close()
