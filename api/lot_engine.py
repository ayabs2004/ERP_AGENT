"""Deterministic lot selection engine (FEFO/FIFO).

This module provides pure‑Python data structures and functions to allocate
requested quantities across available lots using either a FIFO (first‑in‑first‑out)
or FEFO (first‑expiring‑first‑out) strategy. No external database or
MCP/SQL access is performed; the engine operates solely on dataclass
instances supplied by the caller, ensuring that lot consistency is
maintained by the surrounding application logic."""


from dataclasses import dataclass
from datetime import date, datetime

DATE_SENTINELLE_SAGE = date(1753, 1, 1)


def _date_expiration_valide(d) -> date | None:
    """Convert various date representations to a valid expiration date.

    The function accepts ``None``, ``datetime`` objects, ISO‑format strings,
    or ``date`` objects. It normalises the value to a ``date`` instance,
    treating the sentinel value ``1753‑01‑01`` as “no expiration”. If the
    input cannot be parsed or represents the sentinel, ``None`` is returned.
    """
    if d is None:
        return None
    if isinstance(d, datetime):
        d = d.date()
    if isinstance(d, str):
        try:
            d = datetime.fromisoformat(d.split(" ")[0]).date()
        except ValueError:
            return None
    return None if d <= DATE_SENTINELLE_SAGE else d


@dataclass
class Lot:
    """Represents a stock lot."""
    numero: str
    qte_disponible: float
    date_expiration: date | None
    date_fabrication: date


@dataclass
class AllocationLigne:
    """Allocation result for a single lot."""
    lot: str
    qte: float


@dataclass
class ResultatAllocation:
    """Overall allocation outcome."""
    ok: bool
    allocations: list
    qte_allouee: float
    qte_demandee: float
    manque: float
    lots_disponibles: list
    message: str


def _trier_lots(lots: list[Lot], strategie: str) -> list[Lot]:
    """Sort lots according to the chosen allocation strategy.

    Parameters
    ----------
    lots: list[Lot]
        The collection of lots to be sorted.
    strategie: str
        Allocation strategy, either ``"FEFO"`` (first‑expiring‑first‑out) or any
        other value interpreted as FIFO.

    Returns
    -------
    list[Lot]
        Lots filtered to those with a positive available quantity and sorted
        according to the strategy.
    """
    utilisables = [l for l in lots if l.qte_disponible > 0]
    if strategie == "FEFO":
        return sorted(
            utilisables,
            key=lambda l: (l.date_expiration is None, l.date_expiration or date.max, l.date_fabrication)
        )
    return sorted(utilisables, key=lambda l: l.date_fabrication)


def allouer(qte_demandee: float, lots: list[Lot], strategie: str = "FIFO") -> ResultatAllocation:
    """Allocate a requested quantity across available lots.

    The function attempts to satisfy ``qte_demandee`` by consuming quantities
    from the provided ``lots`` according to the specified ``strategie``.
    It returns a :class:`ResultatAllocation` describing the outcome.

    Parameters
    ----------
    qte_demandee: float
        Quantity requested for allocation.
    lots: list[Lot]
        List of available lots.
    strategie: str, optional
        Allocation strategy, either ``"FIFO"`` (default) or ``"FEFO"``.

    Returns
    -------
    ResultatAllocation
        Detailed result of the allocation attempt.
    """
    if qte_demandee <= 0:
        return ResultatAllocation(False, [], 0.0, qte_demandee, 0.0, lots, "Quantité demandée invalide.")

    lots_tries = _trier_lots(lots, strategie)
    allocations = []
    reste = qte_demandee

    for lot in lots_tries:
        if reste <= 0:
            break
        prise = min(lot.qte_disponible, reste)
        if prise > 0:
            allocations.append(AllocationLigne(lot=lot.numero, qte=prise))
            reste -= prise

    qte_allouee = qte_demandee - reste
    if reste > 1e-9:
        return ResultatAllocation(
            ok=False,
            allocations=allocations,
            qte_allouee=qte_allouee,
            qte_demandee=qte_demandee,
            manque=reste,
            lots_disponibles=lots_tries,
            message=f"Stock insuffisant : {qte_demandee:.0f} demandées, {qte_allouee:.0f} disponibles.",
        )
    return ResultatAllocation(
        ok=True,
        allocations=allocations,
        qte_allouee=qte_allouee,
        qte_demandee=qte_demandee,
        manque=0.0,
        lots_disponibles=lots_tries,
    )