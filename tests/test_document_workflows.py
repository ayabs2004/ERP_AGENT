import asyncio
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from database import init_db_complet
from api import mcp_actions_sage as actions
from adaptation import db_adapter


class DocumentWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "test.db"
        # Forcer le driver sqlite et le chemin DB pour les tests
        os.environ["DB_DRIVER"] = "sqlite"
        os.environ["DB_PATH"] = str(self.db_path)
        db_adapter.reload_config()
        init_db_complet.init_database_complete(str(self.db_path))

    def tearDown(self):
        # Restaurer l'environnement
        os.environ.pop("DB_DRIVER", None)
        os.environ.pop("DB_PATH", None)
        db_adapter.reload_config()
        self.tmpdir.cleanup()

    def _seed_article_and_client(self):
        conn = actions._get_conn()
        try:
            conn.execute("""
                INSERT OR REPLACE INTO F_COMPTET (CT_Num, CT_Intitule, CT_Type, CT_Validite, CT_EncoursMax, CT_Encours)
                VALUES (?, ?, 0, 'VALIDE', 1000.0, 0.0)
            """, ("CLI001", "Client Test"))
            conn.execute("""
                INSERT OR REPLACE INTO F_ARTICLE (AR_Ref, AR_Design, AR_PrixVen, AR_PrixAch)
                VALUES (?, ?, 10.0, 5.0)
            """, ("ART1", "Article Test"))
            conn.execute("""
                INSERT OR REPLACE INTO F_ARTSTOCK (AR_Ref, AS_QteSto, AS_QteCom)
                VALUES (?, ?, 0.0)
            """, ("ART1", 2.0))
            conn.commit()
        finally:
            conn.close()

    def test_inserer_document_accepts_multiple_lines_and_generates_unique_numbers(self):
        self._seed_article_and_client()
        conn = actions._get_conn()
        try:
            piece1 = actions._generer_num_piece("BL", conn)
            piece2 = actions._generer_num_piece("BL", conn)
            self.assertNotEqual(piece1, piece2)

            inserted_piece = actions._inserer_document(
                conn,
                "BL",
                piece1,
                "CLI001",
                [
                    {"ref_article": "ART1", "qte": 1.0, "prix_unit": 10.0},
                    {"ref_article": "ART1", "qte": 2.0, "prix_unit": 9.0},
                ],
            )
            self.assertEqual(inserted_piece, piece1)
            rows = conn.execute("SELECT COUNT(*) AS c FROM F_DOCLIGNE WHERE DO_Piece = ?", (piece1,)).fetchone()
            self.assertEqual(rows["c"], 2)
        finally:
            conn.close()

    def test_direct_invoice_checks_stock_before_creating(self):
        self._seed_article_and_client()
        result = actions._generer_facture_directe("CLI001", "ART1", 3.0, 10.0)
        self.assertEqual(result["statut"], "STOCK_INSUFFISANT")

        conn = actions._get_conn()
        try:
            conn.execute("UPDATE F_COMPTET SET CT_EncoursMax = ? WHERE CT_Num = ?", (10000.0, "CLI001"))
            conn.commit()
        finally:
            conn.close()

        result = actions._generer_facture_directe("CLI001", "ART1", 1.0, 10.0)
        self.assertEqual(result["statut"], "GENERE")
        conn = actions._get_conn()
        try:
            stock = conn.execute("SELECT AS_QteSto FROM F_ARTSTOCK WHERE AR_Ref = ?", ("ART1",)).fetchone()
            self.assertEqual(float(stock["AS_QteSto"]), 1.0)
        finally:
            conn.close()

    def test_transformer_document_rejects_duplicate_transformations(self):
        self._seed_article_and_client()
        conn = actions._get_conn()
        try:
            conn.execute("UPDATE F_COMPTET SET CT_EncoursMax = ? WHERE CT_Num = ?", (100000.0, "CLI001"))
            num_bl = actions._inserer_document(conn, "BL", "", "CLI001", "ART1", 1.0, 10.0, 10.0)
            conn.commit()
        finally:
            conn.close()

        first = asyncio.run(actions.call_tool("transformer_document", {
            "num_piece_source": num_bl,
            "type_destination": "FACTURE",
        }))
        first_payload = json.loads(first[0].text)
        self.assertEqual(first_payload["statut"], "TRANSFORME")

        second = asyncio.run(actions.call_tool("transformer_document", {
            "num_piece_source": num_bl,
            "type_destination": "FACTURE",
        }))
        second_payload = json.loads(second[0].text)
        self.assertEqual(second_payload["statut"], "EXISTE_DEJA")

    def test_reglement_rejects_duplicate_payment(self):
        self._seed_article_and_client()
        conn = actions._get_conn()
        try:
            num_facture = actions._inserer_document(conn, "FACTURE", "", "CLI001", "ART1", 1.0, 10.0, 10.0)
            conn.commit()
        finally:
            conn.close()

        first = asyncio.run(actions.call_tool("enregistrer_reglement_facture", {
            "num_piece": num_facture,
            "mode_paiement": "Virement",
        }))
        first_payload = json.loads(first[0].text)
        self.assertEqual(first_payload["statut"], "REGLE")

        second = asyncio.run(actions.call_tool("enregistrer_reglement_facture", {
            "num_piece": num_facture,
            "mode_paiement": "Virement",
        }))
        second_payload = json.loads(second[0].text)
        self.assertEqual(second_payload["statut"], "EXISTE_DEJA")


if __name__ == "__main__":
    unittest.main()
