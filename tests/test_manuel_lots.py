from api.mcp_actions_sage import _get_conn, _creer_lot, _lister_lots_disponibles, _article_a_des_lots, _decrementer_lot

conn = _get_conn()

# Créer un lot de test
numero = _creer_lot(conn, "ARTICLE_TEST", 100.0, depot=1)
conn.commit()
print("Lot créé :", numero)

# Vérifier qu'il est détecté
print("A des lots ?", _article_a_des_lots(conn, "ARTICLE_TEST"))

# Le lister
lots = _lister_lots_disponibles(conn, "ARTICLE_TEST", depot=1)
print("Lots disponibles :", lots)

# Le décrémenter
res = _decrementer_lot(conn, numero, "ARTICLE_TEST", 30.0, num_ligne_doc=1)
conn.commit()
print("Décrément :", res)

# Revérifier
lots_apres = _lister_lots_disponibles(conn, "ARTICLE_TEST", depot=1)
print("Lots après décrément :", lots_apres)

conn.close()