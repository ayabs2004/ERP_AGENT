"""Module for managing ERP stock reservations.

This module provides utilities to initialize the reservation table,
calculate available stock, reserve stock, and release reservations.
All operations are performed using a database connection compatible
with the `adaptation.db_adapter` conventions.
"""

from adaptation.db_adapter import table, col
from typing import Tuple

def _init_reservation_table(conn):
    """Create the ERP_RESERVATION table if it does not already exist.

    The table stores reserved quantities per article, depot, and piece
    identifier. It is created with a composite primary key on
    (ref_article, depot, num_piece).
    """
    conn.execute('''
        IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = 'ERP_RESERVATION')
        CREATE TABLE ERP_RESERVATION (
            ref_article NVARCHAR(50),
            depot INT,
            num_piece NVARCHAR(50),
            qte_reservee DECIMAL(18,4),
            created_at DATETIME DEFAULT GETDATE(),
            PRIMARY KEY (ref_article, depot, num_piece)
        )
    ''')

def calculer_disponible(conn, ref_article: str, depot: int) -> float:
    """Calculate the available stock for a given article and depot.

    The function ensures the reservation table exists, purges stale
    reservations older than 15 days, retrieves the physical stock,
    subtracts any existing reservations, and returns the non‑negative
    available quantity.
    """
    _init_reservation_table(conn)
    conn.execute("DELETE FROM ERP_RESERVATION WHERE created_at < DATEADD(day, -15, GETDATE())")
    q_stock = conn.execute(
        f"SELECT {col('stock', 'qte_stock')} FROM {table('stock')} WHERE {col('stock', 'ref')} = ? AND DE_No = ?",
        (ref_article, depot)
    ).fetchone()
    physique = float(q_stock[0]) if q_stock else 0.0
    q_res = conn.execute(
        "SELECT SUM(qte_reservee) FROM ERP_RESERVATION WHERE ref_article = ? AND depot = ?",
        (ref_article, depot)
    ).fetchone()
    reserve = float(q_res[0]) if q_res and q_res[0] else 0.0
    return max(0.0, physique - reserve)

def reserver_stock(conn, ref_article: str, qte: float, depot: int, num_piece: str) -> bool:
    """Reserve a quantity of stock for a specific article, depot, and piece.

    The function first checks if the requested quantity is available.
    If sufficient stock exists, it inserts or updates a reservation
    record in the ERP_RESERVATION table using a MERGE statement.
    Returns ``True`` if the reservation succeeds, otherwise ``False``.
    """
    dispo = calculer_disponible(conn, ref_article, depot)
    if dispo < qte:
        return False
    conn.execute(
        '''
        MERGE ERP_RESERVATION WITH (HOLDLOCK) AS target
        USING (SELECT ? AS ref, ? AS dep, ? AS piece, ? AS qte) AS source
        ON (target.ref_article = source.ref AND target.depot = source.dep AND target.num_piece = source.piece)
        WHEN MATCHED THEN 
            UPDATE SET qte_reservee = target.qte_reservee + source.qte
        WHEN NOT MATCHED THEN
            INSERT (ref_article, depot, num_piece, qte_reservee)
            VALUES (source.ref, source.dep, source.piece, source.qte);
        ''',
        (ref_article, depot, num_piece, qte)
    )
    return True

def liberer_reservation(conn, ref_article: str, depot: int, num_piece: str) -> None:
    """Release a previously made reservation for a given article, depot, and piece.

    The function ensures the reservation table exists and then deletes
    the matching reservation record.
    """
    _init_reservation_table(conn)
    conn.execute(
        "DELETE FROM ERP_RESERVATION WHERE ref_article = ? AND depot = ? AND num_piece = ?",
        (ref_article, depot, num_piece)
    )