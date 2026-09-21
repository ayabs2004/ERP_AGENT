import os
from decimal import Decimal

filepath = 'api/mcp_actions_sage.py'
with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Rename existing _workflow_bl
content = content.replace("def _workflow_bl(", "def _workflow_bl_single(")

new_workflow_bl = """
def _workflow_bl(
    code_client: str,
    ref_article: str = "",
    quantite: float = 0.0,
    prix_unitaire: float = 0.0,
    date_doc: Optional[str] = None,
    lignes: Optional[list[dict]] = None,
) -> dict:
    if not lignes:
        return _workflow_bl_single(code_client, ref_article, quantite, prix_unitaire, date_doc)
        
    conn = _get_conn()
    try:
        dt_doc = None
        if date_doc:
            try:
                from dateutil import parser as dt_parser
                dt_doc = dt_parser.parse(date_doc, dayfirst=True)
            except Exception:
                pass

        client = _resolve_client(conn, code_client)
        if not client:
            return {"statut": "CLIENT_NON_TROUVE", "message": f"❌ Client '{code_client}' introuvable.", "suggestions": _suggestions_clients(conn, code_client)}

        code_reel  = client[C_CT_NUM]
        nom_client = client[C_CT_INTITULE]
        if int(client.get(C_CT_SOMMEIL) or 0) != 0:
            return {"statut": "CLIENT_BLOQUE", "message": f"🚫 Client '{nom_client}' est EN SOMMEIL."}

        # Validation articles et stock
        lignes_validees = []
        montant_total = Decimal("0.00")
        
        for l in lignes:
            art = _resolve_article(conn, l.get("ref_article", ""))
            if not art:
                return {"statut": "ARTICLE_NON_TROUVE", "message": f"❌ Article '{l.get('ref_article')}' introuvable.", "suggestions": _suggestions_articles(conn, l.get("ref_article", ""))}
            
            ref = art[C_AR_REF]
            desig = art[C_AR_DESIGN]
            qte = float(l.get("quantite", 0.0))
            pu = float(l.get("prix_unitaire", 0.0))
            prix_auto = _to_decimal(art[C_AR_PRIXVEN] or 0.0)
            prix_final = _to_decimal(pu if pu > 0 else float(prix_auto))
            
            stock_dispo = _get_stock(conn, ref)
            if _est_article_stocke(conn, art) and stock_dispo < qte:
                return {"statut": "STOCK_INSUFFISANT", "message": f"📦 Stock insuffisant pour '{desig}' ({ref}). Demande {qte}, Dispo {stock_dispo}.", "stock_dispo": stock_dispo, "qte_demandee": qte, "ref_article": ref, "code_client": code_reel}
                
            montant_ligne = (prix_final * Decimal(str(qte))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            montant_total += montant_ligne
            
            lignes_validees.append({
                "article": art, "ref": ref, "desig": desig,
                "qte": qte, "prix": float(prix_final)
            })

        num_bl = _generer_num_piece("BL", conn)
        lignes_insert = []
        messages_lots = []
        
        for l_val in lignes_validees:
            ref = l_val["ref"]
            qte = l_val["qte"]
            pu = l_val["prix"]
            art = l_val["article"]
            
            if _article_a_des_lots(conn, ref):
                from lot_engine import allouer, Lot, _date_expiration_valide
                rows = _lister_lots_disponibles(conn, ref, DEPOT_DEFAUT)
                lots_obj = [Lot(numero=r["numero"], qte_disponible=float(r["qte_restante"]), date_expiration=_date_expiration_valide(r["peremption"]), date_fabrication=r["fabrication"]) for r in rows]
                resultat = allouer(qte, lots_obj, "FEFO" if any(lt.date_expiration for lt in lots_obj) else "FIFO")
                
                if not resultat.ok:
                    return {"statut": "STOCK_INSUFFISANT", "message": f"📦 Stock insuffisant (lots) pour {ref}."}
                
                for alloc in resultat.allocations:
                    mvt = _ajuster_stock_db(conn, ref, alloc.qte, "SORTIE", motif=f"BL {num_bl} / lot {alloc.lot}")
                    lignes_insert.append({
                        "ref_article": ref, "qte": alloc.qte, "prix_unit": pu,
                        "mvt_stock": 3, "depot": DEPOT_DEFAUT, "prix_ru": mvt["cout_ligne"], "cmup": mvt["cout_ligne"],
                        "lot_alloue": alloc.lot
                    })
            else:
                if _est_article_stocke(conn, art):
                    mvt = _ajuster_stock_db(conn, ref, qte, "SORTIE", motif=f"BL {num_bl}")
                    mvt_stock, cout = 3, mvt["cout_ligne"]
                else:
                    mvt_stock, cout = 0, 0.0
                    
                lignes_insert.append({
                    "ref_article": ref, "qte": qte, "prix_unit": pu,
                    "mvt_stock": mvt_stock, "depot": DEPOT_DEFAUT, "prix_ru": cout, "cmup": cout
                })

        num_bl = _inserer_document(conn, "BL", num_bl, code_reel, lignes=lignes_insert, date_doc=dt_doc)
        
        # Decrement lots via DL_No
        lignes_inserees = conn.execute(f"SELECT DL_No FROM {T_DOC_LIGNE} WHERE {C_DL_PIECE}=? ORDER BY DL_No", (num_bl,)).fetchall()
        for l_in, l_doc in zip(lignes_insert, lignes_inserees):
            if "lot_alloue" in l_in:
                _decrementer_lot(conn, l_in["lot_alloue"], l_in["ref_article"], l_in["qte"], num_ligne_doc=l_doc["DL_No"])

        conn.commit()
        
        return {
            "statut": "GENERE", "DO_Piece": num_bl, "DO_Tiers": code_reel,
            "montant": float(montant_total), 
            "message": f"✅ Bon de Livraison multi-lignes créé : {num_bl} pour {nom_client}.\\nMontant total : {_money_text(float(montant_total))}",
            "suggestion_facture": {
                "code_client": code_reel, "nom_client": nom_client,
                "num_bl": num_bl, "lignes_panier": lignes
            },
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def _generer_facture_directe_single(
"""

# Replace the beginning of facture logic to inject the new multi-line workflow_bl before it, and rename facture
content = content.replace("def _generer_facture_directe(", new_workflow_bl)


new_facture = """
def _generer_facture_directe(
    code_client: str,
    ref_article: str = "",
    qte: float = 0.0,
    prix_unitaire: float = 0.0,
    date_doc: Optional[str] = None,
    lignes: Optional[list[dict]] = None,
) -> dict:
    if not lignes:
        return _generer_facture_directe_single(code_client, ref_article, qte, prix_unitaire, date_doc)
        
    conn = _get_conn()
    try:
        client = _resolve_client(conn, code_client)
        if not client: return {"statut": "CLIENT_NON_TROUVE", "suggestions": _suggestions_clients(conn, code_client)}
        if int(client.get(C_CT_SOMMEIL) or 0) != 0: return {"statut": "CLIENT_BLOQUE", "message": "🚫 Client bloqué."}

        lignes_validees = []
        montant_total = Decimal("0.00")
        
        for l in lignes:
            art = _resolve_article(conn, l.get("ref_article", ""))
            if not art: return {"statut": "ARTICLE_NON_TROUVE", "suggestions": _suggestions_articles(conn, l.get("ref_article", ""))}
            ref = art[C_AR_REF]
            q = float(l.get("quantite", 0.0))
            pu = float(l.get("prix_unitaire", 0.0))
            p_final = _to_decimal(pu if pu > 0 else float(art[C_AR_PRIXVEN] or 0.0))
            
            if _est_article_stocke(conn, art) and _get_stock(conn, ref) < q:
                return {"statut": "STOCK_INSUFFISANT", "message": f"📦 Stock insuffisant pour {ref}.", "stock_dispo": _get_stock(conn, ref), "qte_demandee": q}
                
            montant_total += (p_final * Decimal(str(q))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            lignes_validees.append({"article": art, "ref": ref, "qte": q, "prix": float(p_final)})

        num_fa = _generer_num_piece("FACTURE", conn)
        lignes_insert = []
        
        for l_val in lignes_validees:
            if _est_article_stocke(conn, l_val["article"]):
                mvt = _ajuster_stock_db(conn, l_val["ref"], l_val["qte"], "SORTIE", motif=f"FACTURE {num_fa}")
                mvt_stock, cout = 3, mvt["cout_ligne"]
            else:
                mvt_stock, cout = 0, 0.0
                
            lignes_insert.append({
                "ref_article": l_val["ref"], "qte": l_val["qte"], "prix_unit": l_val["prix"],
                "mvt_stock": mvt_stock, "depot": DEPOT_DEFAUT, "prix_ru": cout, "cmup": cout
            })

        _inserer_document(conn, "FACTURE", num_fa, client[C_CT_NUM], lignes=lignes_insert, date_doc=date_doc)
        conn.commit()
        return {
            "statut": "GENERE", "DO_Piece": num_fa, "DO_Tiers": client[C_CT_NUM],
            "montant": float(montant_total),
            "message": f"✅ Facture multi-lignes créée : {num_fa} pour {client[C_CT_INTITULE]}.\\nMontant total : {_money_text(float(montant_total))}"
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def _generer_bc_direct(
"""

content = content.replace("def _generer_bc_direct(", new_facture)

with open('api/mcp_actions_sage.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Patch applied successfully.")
