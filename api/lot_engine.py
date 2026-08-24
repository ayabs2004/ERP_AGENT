"""
Moteur déterministe de sélection de lots (FEFO/FIFO).
Aucun accès MCP/SQL ici : entrée/sortie en dataclasses Python pures.
Confirmé : Sage n'a aucun trigger automatique sur F_LOTSERIE — ce moteur
et le code appelant sont seuls responsables de la cohérence des lots.
"""
from dataclasses import dataclass
from datetime import date, datetime

DATE_SENTINELLE_SAGE = date(1753, 1, 1)


def _date_expiration_valide(d) -> date | None:
    """Neutralise la date sentinelle Sage (1753-01-01 = 'pas de péremption')."""
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
    numero: str
    qte_disponible: float
    date_expiration: date | None
    date_fabrication: date


@dataclass
class AllocationLigne:
    lot: str
    qte: float


@dataclass
class ResultatAllocation:
    ok: bool
    allocations: list
    qte_allouee: float
    qte_demandee: float
    manque: float
    lots_disponibles: list
    message: str = ""


def _trier_lots(lots: list[Lot], strategie: str) -> list[Lot]:
    utilisables = [l for l in lots if l.qte_disponible > 0]
    if strategie == "FEFO":
        return sorted(
            utilisables,
            key=lambda l: (l.date_expiration is None, l.date_expiration or date.max, l.date_fabrication)
        )
    return sorted(utilisables, key=lambda l: l.date_fabrication)


def allouer(qte_demandee: float, lots: list[Lot], strategie: str = "FIFO") -> ResultatAllocation:
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
            ok=False, allocations=allocations, qte_allouee=qte_allouee,
            qte_demandee=qte_demandee, manque=reste, lots_disponibles=lots_tries,
            message=f"Stock insuffisant : {qte_demandee:.0f} demandées, {qte_allouee:.0f} disponibles.",
        )
    return ResultatAllocation(
        ok=True, allocations=allocations, qte_allouee=qte_allouee,
        qte_demandee=qte_demandee, manque=0.0, lots_disponibles=lots_tries,
    )