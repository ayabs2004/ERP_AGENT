def _workflow_bl(
    code_client: str,
    ref_article: str,
    quantite: float,
    prix_unitaire: float = 0.0,
    date_doc: Optional[str] = None,
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

        # â”€â”€ RÃ©solution client â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        client = _resolve_client(conn, code_client)
        if not client:
            return {
                "statut": "CLIENT_NON_TROUVE",
                "message": f"âŒ Client '{code_client}' introuvable.",
                "suggestions": _suggestions_clients(conn, code_client),
            }

        code_reel  = client[C_CT_NUM]
        nom_client = client[C_CT_INTITULE]

        # CT_Sommeil : 1 = en sommeil / bloquÃ©, 0 = actif / valide
        sommeil_cl = int(client.get(C_CT_SOMMEIL) or 0)

        if sommeil_cl != 0:
            return {
                "statut": "CLIENT_BLOQUE",
                "message": (
                    f"ðŸš« Impossible de crÃ©er le BL.\n\n"
                    f"   Client '{nom_client}' ({code_reel}) est EN SOMMEIL / BLOQUÃ‰.\n"
                    f"   Contactez le service comptabilitÃ©.\n\n"
                    f"   âž¡ï¸  Commande : 'modifier statut client {code_reel}'"
                ),
            }

        alerte_suspect = ""

        # â”€â”€ RÃ©solution article â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        article = _resolve_article(conn, ref_article)
        if not article:
            return {
                "statut": "ARTICLE_NON_TROUVE",
                "message": f"âŒ Article '{ref_article}' introuvable.",
                "suggestions": _suggestions_articles(conn, ref_article),
            }

        ref_reelle  = article[C_AR_REF]
        desig       = article[C_AR_DESIGN]
        prix_auto   = _to_decimal(article[C_AR_PRIXVEN] or 0.0)
        prix_final  = _to_decimal(prix_unitaire if prix_unitaire > 0 else float(prix_auto))
        stock_dispo = _get_stock(conn, ref_reelle)
        montant     = (prix_final * Decimal(str(quantite))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        # â”€â”€ ContrÃ´le stock â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        if _est_article_stocke(conn, article) and stock_dispo < quantite:
            manque = quantite - stock_dispo
            return {
                "statut": "STOCK_INSUFFISANT",
                "message": (
                    f"ðŸ“¦ Stock insuffisant pour '{desig}' ({ref_reelle}).\n"
                    f"   Disponible : {stock_dispo} u | "
                    f"DemandÃ© : {quantite} u | Manque : {manque} u\n\n"
                    f"   Voulez-vous lancer un Ordre de Fabrication pour {manque} u ?"
                ),
                "stock_dispo":  stock_dispo,
                "qte_demandee": quantite,
                "manque":       manque,
                "ref_article":  ref_reelle,
                "code_client":  code_reel,
                "prix_unitaire": prix_final,
                "data_bl_en_attente": {
                    "code_client":  code_reel,
                    "nom_client":   nom_client,
                    "ref_article":  ref_reelle,
                    "designation":  desig,
                    "quantite":     quantite,
                    "prix_unitaire": prix_final,
                    "montant":      montant,
                    "alerte_suspect": alerte_suspect,
                },
            }
        # â•â•â•â•â•â•â•â•â•â• NOUVEAU : bifurcation gestion par lot â•â•â•â•â•â•â•â•â•â•
        if _article_a_des_lots(conn, ref_reelle):
            from lot_engine import allouer, Lot, _date_expiration_valide

            rows = _lister_lots_disponibles(conn, ref_reelle, DEPOT_DEFAUT)
            lots_obj = [
        Lot(numero=r["numero"], qte_disponible=float(r["qte_restante"]),
            date_expiration=_date_expiration_valide(r["peremption"]),
            date_fabrication=r["fabrication"])
                for r in rows
    ]
            strategie = "FEFO" if any(l.date_expiration for l in lots_obj) else "FIFO"
            resultat = allouer(quantite, lots_obj, strategie)

            if not resultat.ok:
                lignes_lots = "\n".join(
            f"   â€¢ {l.numero} : {l.qte_disponible} u"
            + (f" (pÃ©remption {l.date_expiration})" if l.date_expiration else "")
            for l in resultat.lots_disponibles
        )
                return {
            "statut": "STOCK_INSUFFISANT",
            "message": (
                f"ðŸ“¦ Stock insuffisant pour '{desig}' ({ref_reelle}) â€” gestion par lot.\n\n"
                f"   DemandÃ© : {quantite} u | Disponible : {resultat.qte_allouee} u | "
                f"Manque : {resultat.manque} u\n\n   Lots disponibles :\n{lignes_lots}"
            ),
            "stock_dispo": resultat.qte_allouee, "qte_demandee": quantite,
            "manque": resultat.manque, "ref_article": ref_reelle, "code_client": code_reel,
        }

            num_bl = _generer_num_piece("BL", conn)
            lignes_bl = []
            for alloc in resultat.allocations:
                mvt = _ajuster_stock_db(conn, ref_reelle, alloc.qte, "SORTIE",
                                 motif=f"BL {num_bl} / lot {alloc.lot}")
                lignes_bl.append({
            "ref_article": ref_reelle, "qte": alloc.qte, "prix_unit": float(prix_final),
            "mvt_stock": 3, "depot": DEPOT_DEFAUT,
            "prix_ru": mvt["cout_ligne"], "cmup": mvt["cout_ligne"],
        })

            num_bl = _inserer_document(conn, "BL", num_bl, code_reel, lignes=lignes_bl)

    # RÃ©cupÃ©rer les vrais DL_No pour tracer prÃ©cisÃ©ment quel lot a servi quelle ligne
            lignes_inserees = conn.execute(
        f"SELECT DL_No FROM {T_DOC_LIGNE} WHERE {C_DL_PIECE}=? ORDER BY DL_No",
        (num_bl,)
    ).fetchall()
            for alloc, ligne_doc in zip(resultat.allocations, lignes_inserees):
                _decrementer_lot(conn, alloc.lot, ref_reelle, alloc.qte,
                          num_ligne_doc=ligne_doc["DL_No"])

            conn.commit()

            lots_txt = ", ".join(f"{a.lot} ({a.qte:.0f} u)" for a in resultat.allocations)
            dispos_txt = "\n".join(f"      - {r['numero']} ({r['qte_restante']:.0f} u) exp: {r.get('peremption') or 'N/A'}" for r in rows)
            if not dispos_txt:
                dispos_txt = "      - Aucun lot disponible"
            
            message = (
        f"âœ… Bon de Livraison crÃ©Ã© (gestion par lot)\n\n"
        f"  â€¢ NumÃ©ro BL   : {num_bl}\n"
        f"  â€¢ Client      : {nom_client} ({code_reel})\n"
        f"  â€¢ Article     : {desig} ({ref_reelle})\n"
        f"  â€¢ QuantitÃ©    : {quantite} u\n"
        f"  â€¢ Lots dispos :\n{dispos_txt}\n"
        f"  â€¢ Lots choisis ({strategie}) : {lots_txt}\n"
    )
            return {
        "statut": "GENERE", "DO_Piece": num_bl, "DO_Tiers": code_reel,
        "AR_Ref": ref_reelle, "montant": montant, "message": message,
        "suggestion_facture": {
            "code_client": code_reel, "nom_client": nom_client,
            "ref_article": ref_reelle, "quantite": quantite,
            "prix_unitaire": prix_final, "montant": montant, "num_bl": num_bl,
        },
    }
# â•â•â•â•â•â•â•â•â•â• FIN bifurcation lot â€” sinon comportement existant â•â•â•â•â•â•â•â•â•â•
        # â”€â”€ CrÃ©ation BL â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        if _est_article_stocke(conn, article):
            num_bl = _generer_num_piece("BL", conn)
            mvt = _ajuster_stock_db(
                conn, ref_reelle, quantite, "SORTIE", motif=f"BL {num_bl}"
            )
            mvt_stock_val, cout_ligne = 3, mvt["cout_ligne"]
            stock_apres_aff = mvt["stock_apres"]
        else:
            num_bl = _generer_num_piece("BL", conn)
            mvt_stock_val, cout_ligne, stock_apres_aff = 0, 0.0, None

        num_bl = _inserer_document(
            conn, "BL", num_bl, code_reel,
            lignes=[{
                "ref_article": ref_reelle, "qte": quantite,
                "prix_unit": float(prix_final),
                "mvt_stock": mvt_stock_val, "depot": DEPOT_DEFAUT,
                "prix_ru": cout_ligne, "cmup": cout_ligne,
            }],
            date_doc=dt_doc
        )
        conn.commit()

        stock_apres_msg = f"{stock_apres_aff} u" if stock_apres_aff is not None else "N/A (Non stockÃ©)"

        champs = {
            "NumÃ©ro BL": num_bl,
            "Client": f"{nom_client} ({code_reel})",
            "Article": f"{desig} ({ref_reelle})",
            "QuantitÃ©": f"{quantite} u",
            "Prix unit.": _money_text(prix_final),
            "Montant": _money_text(montant),
            "Stock aprÃ¨s": stock_apres_msg,
        }
        message = _formater_bloc("âœ… Bon de Livraison crÃ©Ã© !", champs)
        if alerte_suspect:
            message += f"\n\nâš ï¸ {alerte_suspect}\n"

        return {
            "statut":      "GENERE",
            "DO_Piece":    num_bl,
            "DO_Tiers":    code_reel,
            "AR_Ref":      ref_reelle,
            "montant":     montant,
            "stock_apres": stock_apres_aff,
            "message":     message,
            "alertes":     [alerte_suspect] if alerte_suspect else [],
            "suggestion_facture": {
                "code_client":  code_reel,
                "nom_client":   nom_client,
                "ref_article":  ref_reelle,
