from app.db import repo
from app.db.database import cursor, init_schema


def _seed_closed_trade(alert_id, side, entry, exit_price, result_r, cash, balance, closed_at, equity_ts):
    with cursor() as con:
        con.execute(
            """INSERT INTO alerts (id,pair,timeframe,side,source,zone_min,zone_max,entry,stop,target,
                                   state,size,rr,armed_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (alert_id, "xauusd", "5m", side, "sweep_reclaim", entry, entry, entry,
             entry - 12 if side == "long" else entry + 12,
             entry + 24 if side == "long" else entry - 24,
             "STOPPED", 0.02, 2.0, closed_at - 100, closed_at),
        )
        con.execute(
            "INSERT INTO alert_events (alert_id,state,ts,price,reason) VALUES (?,?,?,?,?)",
            (alert_id, "FILLED", closed_at - 100, entry, "filled"))
        con.execute(
            "INSERT INTO alert_events (alert_id,state,ts,price,reason) VALUES (?,?,?,?,?)",
            (alert_id, "STOPPED", closed_at, exit_price, "stopped"))
        con.execute(
            "INSERT INTO outcomes (alert_id,pair,timeframe,zone_type,confluence,swept,result_r,closed_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (alert_id, "xauusd", "5m", "sweep_reclaim", 0, 0, result_r, closed_at))
        con.execute(
            "INSERT INTO equity (pair,ts,balance,r,alert_id,cash) VALUES (?,?,?,?,?,?)",
            ("xauusd", equity_ts, balance, result_r, alert_id, cash))


def test_closed_ledger_uses_stored_cash_not_snapshot_diff():
    init_schema()
    with cursor() as con:
        con.execute("DELETE FROM outcomes"); con.execute("DELETE FROM equity")
        con.execute("DELETE FROM alerts"); con.execute("DELETE FROM alert_events")
    repo.get_account("xauusd", starting=1000.0)

    # THREE genuine -1R losses closing on the SAME whole second, but their alert_ids
    # (arm order) are the REVERSE of the true close/equity order — the exact shape
    # that made the old snapshot-diff show a "-1R" as +cash and a rising balance.
    closed_at = 1785000000
    #            alert_id, side, entry, exit, r,  cash,   balance, closed_at, equity_ts
    _seed_closed_trade(30, "long", 4061.81, 4061.00, -1.0, -1.62, 998.38, closed_at, closed_at + 3)
    _seed_closed_trade(20, "long", 4063.32, 4061.00, -1.0, -4.64, 993.74, closed_at, closed_at + 2)
    _seed_closed_trade(10, "long", 4077.83, 4076.96, -1.0, -1.74, 992.00, closed_at, closed_at + 1)

    rows = repo.closed_ledger("xauusd")
    # every -1R loss must show NEGATIVE cash (the old bug showed +112 on a loss)
    for r in rows:
        assert r["result_r"] == -1.0
        assert r["cash_delta"] < 0, f"a -1R loss must not book positive cash: {r['cash_delta']}"
    # the stored cash is read back exactly, not reconstructed
    cash_by_alert = {r["alert_id"]: r["cash_delta"] for r in rows}
    assert cash_by_alert[10] == -1.74
    assert cash_by_alert[20] == -4.64
    assert cash_by_alert[30] == -1.62
