def _workflow_bl(
    code_client: str,
    ref_article: str = "",
    quantite: float = 0.0,
    prix_unitaire: float = 0.0,
    date_doc: Optional[str] = None,
    lignes: Optional[list[dict]] = None,
) -> dict:
    conn = _get_conn()
    try:
        dt_doc = None
        if date_doc:
            try:
                from dateutil import parser as dt_parser
                dt_doc = dt_parser.parse(date_doc, dayfirst=True)
            except Exception:
                pass

        # ── Résolution client ─────────────────────────────────────────
        client = _resolve_client(conn, code_client)
        if not client:
            return {
                "statut": "CLIENT_NON_TROUVE",
                "message": f"❌ Client '{code_client}' introuvable.",
                "suggestions": _suggestions_clients(conn, code_client),
            }

        code_reel  = client[C_CT_NUM]
        nom_client = client[C_CT_INTITULE]

        sommeil_cl = int(client.get(C_CT_SOMMEIL) or 0)
        if sommeil_cl != 0:
            return {
                "statut": "CLIENT_BLOQUE",
                "message": (
                    f"🚫 Impossible de créer le BL.\n\n"
                    f"   Client '{nom_client}' ({code_reel}) est EN SOMMEIL / BLOQUÉ.\n"
                    f"   Contactez le service comptabilité.\n\n"
                    f"   ➡️  Commande : 'modifier statut client {code_reel}'"
                ),
            }

        alerte_suspect = ""

        if not lignes:
            lignes = [{"ref_article": ref_article, "quantite": quantite, "prix_unitaire": prix_unitaire}]

        lignes_bl_finales = []
        montant_total = Decimal("0.00")
        
        # Validation et construction des lignes
        for ligne in lignes:
            r_art = ligne.get("ref_article", "")
            qte = ligne.get("quantite", 0.0)
            pu = ligne.get("prix_unitaire", 0.0)
            
            article = _resolve_article(conn, r_art)
            if not article:
                return {
                    "statut": "ARTICLE_NON_TROUVE",
                    "message": f"❌ Article '{r_art}' introuvable.",
                    "suggestions": _suggestions_articles(conn, r_art),
                }

            ref_reelle  = article[C_AR_REF]
            desig       = article[C_AR_DESIGN]
            prix_auto   = _to_decimal(article[C_AR_PRIXVEN] or 0.0)
            prix_final  = _to_decimal(pu if pu > 0 else float(prix_auto))
            stock_dispo = _get_stock(conn, ref_reelle)
            montant_ligne = (prix_final * Decimal(str(qte))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            
            # ── Contrôle stock ────────────────────────────────────────────
            if _est_article_stocke(conn, article) and stock_dispo < qte:
                manque = qte - stock_dispo
                return {
                    "statut": "STOCK_INSUFFISANT",
                    "message": (
                        f"📦 Stock insuffisant pour '{desig}' ({ref_reelle}).\n"
                        f"   Disponible : {stock_dispo} u | Demandé : {qte} u | Manque : {manque} u"
                    ),
                    "stock_dispo": stock_dispo,
                    "qte_demandee": qte,
                    "ref_article": ref_reelle,
                    "code_client": code_reel,
                }
                
            lignes_bl_finales.append({
                "article": article,
                "ref_reelle": ref_reelle,
                "desig": desig,
                "qte": qte,
                "prix_final": float(prix_final),
                "montant_ligne": montant_ligne
            })
            montant_total += montant_ligne

        # Génération du numéro BL
        num_bl = _generer_num_piece("BL", conn)
        
        lignes_insert = []
        messages_lots = []
        
        for l_bl in lignes_bl_finales:
            ref = l_bl["ref_reelle"]
            qte = l_bl["qte"]
            pu = l_bl["prix_final"]
            
            if _article_a_des_lots(conn, ref):
                from lot_engine import allouer, Lot, _date_expiration_valide
                rows = _lister_lots_disponibles(conn, ref, DEPOT_DEFAUT)
                lots_obj = [Lot(numero=r["numero"], qte_disponible=float(r["qte_restante"]), date_expiration=_date_expiration_valide(r["peremption"]), date_fabrication=r["fabrication"]) for r in rows]
                strategie = "FEFO" if any(lt.date_expiration for lt in lots_obj) else "FIFO"
                resultat = allouer(qte, lots_obj, strategie)
                
                if not resultat.ok:
                    return {"statut": "STOCK_INSUFFISANT", "message": f"📦 Stock insuffisant (lots) pour {ref}."}
                
                for alloc in resultat.allocations:
                    mvt = _ajuster_stock_db(conn, ref, alloc.qte, "SORTIE", motif=f"BL {num_bl} / lot {alloc.lot}")
                    lignes_insert.append({
                        "ref_article": ref, "qte": alloc.qte, "prix_unit": pu,
                        "mvt_stock": 3, "depot": DEPOT_DEFAUT,
                        "prix_ru": mvt["cout_ligne"], "cmup": mvt["cout_ligne"],
                        "lot_alloue": alloc.lot
                    })
                messages_lots.append(f"{ref} (Lots: {', '.join(a.lot for a in resultat.allocations)})")
            else:
                if _est_article_stocke(conn, l_bl["article"]):
                    mvt = _ajuster_stock_db(conn, ref, qte, "SORTIE", motif=f"BL {num_bl}")
                    mvt_stock_val, cout_ligne = 3, mvt["cout_ligne"]
                else:
                    mvt_stock_val, cout_ligne = 0, 0.0
                    
                lignes_insert.append({
                    "ref_article": ref, "qte": qte, "prix_unit": pu,
                    "mvt_stock": mvt_stock_val, "depot": DEPOT_DEFAUT,
                    "prix_ru": cout_ligne, "cmup": cout_ligne
                })

        num_bl = _inserer_document(conn, "BL", num_bl, code_reel, lignes=lignes_insert, date_doc=dt_doc)
        
        # Décrémenter les lots en DB via DL_No
        lignes_inserees = conn.execute(f"SELECT DL_No, AR_Ref FROM {T_DOC_LIGNE} WHERE {C_DL_PIECE}=? ORDER BY DL_No", (num_bl,)).fetchall()
        for ligne_insert, ligne_doc in zip(lignes_insert, lignes_inserees):
            if "lot_alloue" in ligne_insert:
                _decrementer_lot(conn, ligne_insert["lot_alloue"], ligne_insert["ref_article"], ligne_insert["qte"], num_ligne_doc=ligne_doc["DL_No"])

        conn.commit()

        article_desc = f"{len(lignes)} article(s)" if len(lignes) > 1 else f"{lignes_bl_finales[0]['desig']} ({lignes_bl_finales[0]['ref_reelle']})"
        
        champs = {
            "Numéro BL": num_bl,
            "Client": f"{nom_client} ({code_reel})",
            "Article(s)": article_desc,
            "Montant total": _money_text(float(montant_total)),
        }
        if messages_lots:
            champs["Lots alloués"] = "\n".join(messages_lots)
            
        message = _formater_bloc("✅ Bon de Livraison créé !", champs)

        return {
            "statut": "GENERE", "DO_Piece": num_bl, "DO_Tiers": code_reel,
            "montant": float(montant_total), "message": message,
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
