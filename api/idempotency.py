"""Module providing idempotency lock mechanisms for operations using a primary key constraint in MSSQL.

The module defines functions to acquire a lock for an operation (`lock_operation`) and to
finalize the operation (`complete_operation`). It handles retry‑safe failures, orphaned
pending entries based on a configurable TTL, and concurrency conflicts, ensuring that
business writes and idempotency updates occur within the same transaction.
"""

import json
import logging
import os
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger(__name__)

IDEMPOTENCY_PENDING_TTL_SECONDS = int(os.getenv("IDEMPOTENCY_PENDING_TTL_SECONDS", "300"))

_ERP_OPERATION_TABLE = "ERP_OPERATION"


class ConcurrencyError(Exception):
    """Raised when a recent PENDING operation already exists, indicating active concurrency."""
    pass


def lock_operation(conn: Any, operation_id: str, tool_name: str, payload: dict) -> Optional[dict]:
    """Attempt to acquire a lock for an operation by inserting a PENDING record.

    Returns:
        None if the lock is acquired; the caller should perform the action and then call
        `complete_operation`.
        dict if the operation has already been COMPLETED; the cached response is returned.

    Raises:
        ConcurrencyError: when a recent PENDING operation exists, indicating another thread
        is processing the same operation.
    """
    payload_json = json.dumps(payload, ensure_ascii=False, default=str)

    def _try_insert() -> bool:
        """Attempt the INSERT; returns True on success, False on primary‑key violation."""
        try:
            conn.execute(
                f"""
                INSERT INTO {_ERP_OPERATION_TABLE}
                    (operation_id, tool_name, status, request_payload, created_at, updated_at)
                VALUES (?, ?, 'PENDING', ?, GETDATE(), GETDATE())
                """,
                (operation_id, tool_name, payload_json)
            )
            return True
        except Exception as e:
            err = str(e).upper()
            if "2627" in err or "2601" in err or "PRIMARY KEY" in err or "UNIQUE" in err:
                return False
            raise

    if _try_insert():
        conn.commit()
        return None

    row = conn.execute(
        f"SELECT status, response_payload, created_at FROM {_ERP_OPERATION_TABLE} WHERE operation_id = ?",
        (operation_id,)
    ).fetchone()

    if not row:
        if _try_insert():
            conn.commit()
            return None
        raise RuntimeError(f"[Idempotency] Impossible d'acquérir le verrou pour {operation_id}.")

    status, response_payload, created_at = row[0], row[1], row[2]

    if status == "COMPLETED":
        logger.info("operation_id=%s déjà COMPLETED → réponse en cache", operation_id)
        try:
            return json.loads(response_payload) if response_payload else {"statut": "SUCCES", "message": "Déjà effectué"}
        except (TypeError, ValueError):
            return {"statut": "SUCCES", "message": "Déjà effectué"}

    if status == "FAILED":
        logger.info("operation_id=%s était FAILED → DELETE + ré-INSERT (retry safe)", operation_id)
        conn.execute(f"DELETE FROM {_ERP_OPERATION_TABLE} WHERE operation_id = ?", (operation_id,))
        if _try_insert():
            conn.commit()
            return None
        raise RuntimeError(f"[Idempotency] Impossible de ré-acquérir le verrou après DELETE FAILED pour {operation_id}.")

    if status == "PENDING":
        age_seconds = None
        if created_at is not None:
            try:
                if isinstance(created_at, str):
                    created_at_dt = datetime.fromisoformat(created_at)
                else:
                    created_at_dt = created_at
                age_seconds = (datetime.now() - created_at_dt).total_seconds()
            except Exception:
                age_seconds = None

        if age_seconds is None or age_seconds > IDEMPOTENCY_PENDING_TTL_SECONDS:
            logger.warning(
                "operation_id=%s PENDING orphelin (âge=%.0fs > TTL=%ds) → DELETE + ré-INSERT",
                operation_id, age_seconds or -1, IDEMPOTENCY_PENDING_TTL_SECONDS
            )
            conn.execute(f"DELETE FROM {_ERP_OPERATION_TABLE} WHERE operation_id = ?", (operation_id,))
            if _try_insert():
                conn.commit()
                return None
            raise RuntimeError(f"[Idempotency] Impossible de ré-acquérir le verrou après DELETE orphelin pour {operation_id}.")

        logger.warning("operation_id=%s PENDING récent (âge=%.0fs) → ConcurrencyError", operation_id, age_seconds)
        raise ConcurrencyError(
            f"L'opération '{tool_name}' est déjà en cours de traitement (verrou actif depuis {age_seconds:.0f}s). "
            f"Patientez ou réessayez dans quelques instants."
        )

    raise RuntimeError(f"[Idempotency] Statut inconnu '{status}' pour operation_id={operation_id}.")


def complete_operation(
    conn: Any,
    operation_id: str,
    result: dict,
    status: str = "COMPLETED",
) -> None:
    """Mark the operation as finished and store the response payload.

    This function must be called before `conn.commit()`. The final commit should include
    both the business writes and this idempotency update so that a rollback undoes both.
    """
    result_json = json.dumps(result, ensure_ascii=False, default=str) if result else None
    conn.execute(
        f"""
        UPDATE {_ERP_OPERATION_TABLE}
        SET status = ?, response_payload = ?, updated_at = GETDATE()
        WHERE operation_id = ?
        """,
        (status, result_json, operation_id)
    )