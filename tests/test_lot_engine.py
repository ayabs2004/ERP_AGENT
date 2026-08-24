from datetime import date
from api.lot_engine import allouer, Lot, _date_expiration_valide, DATE_SENTINELLE_SAGE

def test_fifo_simple():
    lots = [Lot("LOT-A", 40, None, date(2026,8,1)), Lot("LOT-B", 60, None, date(2026,8,10))]
    res = allouer(50, lots, "FIFO")
    assert res.ok
    assert [(a.lot, a.qte) for a in res.allocations] == [("LOT-A", 40), ("LOT-B", 10)]

def test_fefo_avec_peremption():
    lots = [
        Lot("LOT-A", 20, date(2026,9,10), date(2026,1,1)),
        Lot("LOT-B", 50, date(2026,9,25), date(2026,1,1)),
    ]
    res = allouer(30, lots, "FEFO")
    assert res.ok
    assert [(a.lot, a.qte) for a in res.allocations] == [("LOT-A", 20), ("LOT-B", 10)]

def test_stock_insuffisant():
    lots = [Lot("LOT-A", 30, None, date(2026,1,1))]
    res = allouer(200, lots, "FIFO")
    assert not res.ok
    assert res.manque == 170

def test_date_sentinelle_neutralisee():
    assert _date_expiration_valide(DATE_SENTINELLE_SAGE) is None
    assert _date_expiration_valide(date(2026,12,31)) == date(2026,12,31)

def test_lot_epuise_ignore():
    lots = [Lot("LOT-A", 0, None, date(2026,1,1)), Lot("LOT-B", 20, None, date(2026,1,2))]
    res = allouer(15, lots, "FIFO")
    assert res.ok
    assert res.allocations[0].lot == "LOT-B"