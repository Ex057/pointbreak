"""Typed-ish read/write helpers over the SQLite schema. Plain dicts in/out."""
from __future__ import annotations

import json
from hashlib import sha1

from ..config import DATA_SOURCE, PREFERRED_MARKETS
from ..services import execution
from .database import cursor, now


_SANITY_MIN_BY_PAIR = {
    item["id"]: float(item["sanity_min"])
    for item in PREFERRED_MARKETS
    if item.get("sanity_min") is not None
}


def _row_data_valid(pair: str | None, *prices) -> bool:
    if not pair:
        return True
    floor = _SANITY_MIN_BY_PAIR.get(pair)
    if floor is None:
        return True
    for price in prices:
        if price is None:
            continue
        try:
            if float(price) < floor:
                return False
        except (TypeError, ValueError):
            return False
    return True


# ---------------------------------------------------------------- alerts
def create_alert(a: dict) -> int:
    with cursor() as con:
        cur = con.execute(
            """INSERT INTO alerts
               (pair,timeframe,side,source,zone_min,zone_max,entry,stop,target,
                score,confluence,state,swept_price,size,rr,trader_mode,
                bias_tf,setup_tf,entry_tf,feed_source,resolved_symbol,price_valid,confirmation_state,
                atr,min_risk,planned_rr,
                armed_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (a["pair"], a["timeframe"], a["side"], a["source"],
             a["zone_min"], a["zone_max"], a["entry"], a["stop"], a["target"],
             a.get("score", 0.0), a.get("confluence", 0), a.get("state", "WATCHING"),
             a.get("swept_price"), a.get("size", 0.0), a.get("rr", 2.0), a.get("trader_mode"),
             a.get("bias_tf"), a.get("setup_tf"), a.get("entry_tf"),
             a.get("feed_source"), a.get("resolved_symbol"),
             1 if a.get("price_valid", True) else 0, a.get("confirmation_state"),
             a.get("atr"), a.get("min_risk"), a.get("planned_rr"),
             now(), now()),
        )
        return int(cur.lastrowid)


def update_alert(alert_id: int, **fields) -> None:
    if not fields:
        return
    fields["updated_at"] = now()
    cols = ", ".join(f"{k}=?" for k in fields)
    with cursor() as con:
        con.execute(f"UPDATE alerts SET {cols} WHERE id=?",
                    (*fields.values(), alert_id))


def add_event(alert_id: int, state: str, price: float, reason: str) -> None:
    with cursor() as con:
        con.execute(
            "INSERT INTO alert_events (alert_id,state,ts,price,reason) VALUES (?,?,?,?,?)",
            (alert_id, state, now(), price, reason),
        )


def create_setup_journal(row: dict) -> int:
    created = row.get("created_at", now())
    fingerprint = _setup_fingerprint(row)
    if row.get("status") == "waiting" and fingerprint:
        with cursor() as con:
            existing = con.execute(
                """SELECT id, repeat_count
                   FROM setup_journal
                   WHERE pair=? AND status='waiting' AND active_fingerprint=?
                   ORDER BY last_seen_at DESC, id DESC
                   LIMIT 1""",
                (row.get("pair"), fingerprint),
            ).fetchone()
            if existing is not None:
                con.execute(
                    """UPDATE setup_journal
                       SET last_seen_at=?, repeat_count=?, reason=?, plan_json=?, quality_flags=?, feed_source=?, resolved_symbol=?, price_valid=?, reference_price=?, candle_price=?
                       WHERE id=?""",
                    (
                        created,
                        int(existing["repeat_count"] or 1) + 1,
                        row.get("reason"),
                        json.dumps(row.get("plan") or {}),
                        json.dumps(row.get("quality_flags") or []),
                        row.get("feed_source"),
                        row.get("resolved_symbol"),
                        1 if row.get("price_valid", True) else 0,
                        row.get("reference_price"),
                        row.get("candle_price"),
                        int(existing["id"]),
                    ),
                )
                return int(existing["id"])
    with cursor() as con:
        cur = con.execute(
            """INSERT INTO setup_journal
               (pair,timeframe,status,side,source,reason,entry,stop,target,rr,trader_mode,
                bias_tf,setup_tf,entry_tf,feed_source,resolved_symbol,price_valid,
                reference_price,candle_price,active_fingerprint,first_seen_at,last_seen_at,repeat_count,
                quality_flags,plan_json,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                row.get("pair"),
                row.get("timeframe"),
                row.get("status"),
                row.get("side"),
                row.get("source"),
                row.get("reason"),
                row.get("entry"),
                row.get("stop"),
                row.get("target"),
                row.get("rr"),
                row.get("trader_mode"),
                row.get("bias_tf"),
                row.get("setup_tf"),
                row.get("entry_tf"),
                row.get("feed_source"),
                row.get("resolved_symbol"),
                1 if row.get("price_valid", True) else 0,
                row.get("reference_price"),
                row.get("candle_price"),
                fingerprint,
                row.get("first_seen_at", created),
                row.get("last_seen_at", created),
                row.get("repeat_count", 1),
                json.dumps(row.get("quality_flags") or []),
                json.dumps(row.get("plan") or {}),
                created,
            ),
        )
        return int(cur.lastrowid)


def list_setup_journal(pair: str | None = None, since: int | None = None, until: int | None = None, limit: int = 100) -> list[dict]:
    clauses = []
    args: list = []
    if pair:
        clauses.append("pair=?")
        args.append(pair)
    if since is not None:
        clauses.append("created_at>=?")
        args.append(since)
    if until is not None:
        clauses.append("created_at<=?")
        args.append(until)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with cursor() as con:
        rows = [
            dict(r)
            for r in con.execute(
                f"SELECT * FROM setup_journal {where} ORDER BY COALESCE(last_seen_at, created_at) DESC, id DESC LIMIT ?",
                (*args, limit),
            )
        ]
    for row in rows:
        row["quality_flags"] = json.loads(row.get("quality_flags") or "[]")
        row["plan_json"] = json.loads(row.get("plan_json") or "{}")
        row["repeat_count"] = int(row.get("repeat_count") or 1)
        row["first_seen_at"] = row.get("first_seen_at") or row.get("created_at")
        row["last_seen_at"] = row.get("last_seen_at") or row.get("created_at")
    return [row for row in rows if _visible_journal_row(row)]


def active_alerts(pair: str | None = None) -> list[dict]:
    """Alerts still in a live (non-terminal) state."""
    live = ("WATCHING", "TOUCHED", "SWEPT", "FILLED")
    q = f"SELECT * FROM alerts WHERE state IN ({','.join('?' * len(live))})"
    args: list = list(live)
    if pair:
        q += " AND pair=?"; args.append(pair)
    with cursor() as con:
        rows = [dict(r) for r in con.execute(q + " ORDER BY score DESC", args)]
    return [
        row for row in rows
        if _row_data_valid(row.get("pair"), row.get("entry"), row.get("stop"), row.get("target"))
    ]


def list_alerts(pair: str | None = None, limit: int = 50) -> list[dict]:
    q = "SELECT * FROM alerts"
    args: list = []
    if pair:
        q += " WHERE pair=?"; args.append(pair)
    q += " ORDER BY updated_at DESC LIMIT ?"; args.append(limit)
    with cursor() as con:
        rows = [dict(r) for r in con.execute(q, args)]
    return [
        row for row in rows
        if _row_data_valid(row.get("pair"), row.get("entry"), row.get("stop"), row.get("target"))
    ]


def list_alerts_page(pair: str | None = None, page: int = 1, per_page: int = 10) -> dict:
    page = max(1, page)
    per_page = max(1, per_page)
    offset = (page - 1) * per_page
    base = "FROM alerts"
    args: list = []
    if pair:
        base += " WHERE pair=?"
        args.append(pair)
    with cursor() as con:
        rows = con.execute(
            f"SELECT * {base} ORDER BY updated_at DESC",
            args,
        ).fetchall()
    valid_rows = [
        dict(r) for r in rows
        if _row_data_valid(r["pair"], r["entry"], r["stop"], r["target"])
    ]
    total = len(valid_rows)
    page_rows = valid_rows[offset: offset + per_page]
    return {
        "items": page_rows,
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": max(1, (total + per_page - 1) // per_page),
    }


def alert_events(alert_id: int) -> list[dict]:
    with cursor() as con:
        return [dict(r) for r in con.execute(
            "SELECT * FROM alert_events WHERE alert_id=? ORDER BY ts ASC, id ASC",
            (alert_id,))]


def open_positions(pair: str | None = None) -> list[dict]:
    """FILLED alerts — entered paper trades still running, with live unrealized R."""
    q = "SELECT * FROM alerts WHERE state='FILLED'"
    args: list = []
    if pair:
        q += " AND pair=?"; args.append(pair)
    with cursor() as con:
        rows = [dict(r) for r in con.execute(q + " ORDER BY updated_at DESC", args)]
    return [
        row for row in rows
        if _row_data_valid(row.get("pair"), row.get("entry"), row.get("stop"), row.get("target"))
    ]


# ---------------------------------------------------------------- outcomes
def record_outcome(o: dict) -> None:
    with cursor() as con:
        existing = con.execute(
            "SELECT 1 FROM outcomes WHERE alert_id=? LIMIT 1",
            (o["alert_id"],),
        ).fetchone()
        if existing is not None:
            return
        # OR IGNORE + the unique index below close the TOCTOU window between the
        # SELECT above and this INSERT; two threads racing here previously wrote
        # the same trade twice and double-counted its R in the stats.
        con.execute(
            """INSERT OR IGNORE INTO outcomes
               (alert_id,pair,timeframe,zone_type,confluence,swept,result_r,closed_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (o["alert_id"], o["pair"], o["timeframe"], o["zone_type"],
             o.get("confluence", 0), 1 if o.get("swept") else 0,
             o["result_r"], now()),
        )


def outcomes(pair: str | None = None) -> list[dict]:
    q = "SELECT * FROM outcomes"
    args: list = []
    if pair:
        q += " WHERE pair=?"; args.append(pair)
    q += " ORDER BY closed_at DESC"
    with cursor() as con:
        return [dict(r) for r in con.execute(q, args)]


# ---------------------------------------------------------------- account
def get_account(pair: str, starting: float = 1000.0) -> dict:
    with cursor() as con:
        row = con.execute("SELECT * FROM accounts WHERE pair=?", (pair,)).fetchone()
        if row is None:
            con.execute(
                "INSERT INTO accounts (pair,starting_balance,balance,updated_at) VALUES (?,?,?,?)",
                (pair, starting, starting, now()))
            return {"pair": pair, "starting_balance": starting, "balance": starting,
                    "wins": 0, "losses": 0, "updated_at": now()}
        return dict(row)


def apply_outcome_to_account(pair: str, r: float, risk_pct: float, alert_id: int, cash_delta: float | None = None) -> dict:
    """Move the paper balance by R × (balance·risk%) and log an equity point."""
    acct = get_account(pair)
    with cursor() as con:
        existing = con.execute(
            "SELECT balance FROM equity WHERE alert_id=? LIMIT 1",
            (alert_id,),
        ).fetchone()
        if existing is not None:
            return {**acct, "balance": float(existing["balance"])}
    risk_amount = acct["balance"] * risk_pct / 100.0
    delta = cash_delta if cash_delta is not None else (risk_amount * r)
    new_balance = round(acct["balance"] + delta, 2)
    win = 1 if r > 0 else 0
    with cursor() as con:
        con.execute(
            "UPDATE accounts SET balance=?, wins=wins+?, losses=losses+?, updated_at=? WHERE pair=?",
            (new_balance, win, 1 - win, now(), pair))
        con.execute(
            "INSERT INTO equity (pair,ts,balance,r,alert_id,cash) VALUES (?,?,?,?,?,?)",
            (pair, now(), new_balance, r, alert_id, round(float(delta), 2)))
    return {**acct, "balance": new_balance}


def rebuild_account(pair: str, starting_balance: float = 1000.0) -> dict:
    """Replay every surviving outcome to rebuild the balance, W/L and equity curve.

    `apply_outcome_to_account` mutates incrementally and each `equity` row stores
    a *cumulative* balance, so deleting outcomes leaves both the counters and the
    surviving curve describing trades that no longer exist. Incremental repair is
    not possible; the curve has to be replayed. Needed after any purge.
    """
    with cursor() as con:
        rows = con.execute(
            """SELECT o.alert_id, o.result_r, o.closed_at,
                      a.side, a.entry, a.stop, a.target, a.size, a.fill_price, a.state
               FROM outcomes o JOIN alerts a ON a.id = o.alert_id
               WHERE o.pair = ? ORDER BY o.closed_at ASC""",
            (pair,),
        ).fetchall()

    from ..config import RISK_PCT
    from ..services import execution  # local: repo must not import services at module scope

    balance = float(starting_balance)
    wins = losses = 0
    points: list[tuple] = []
    for row in rows:
        r = float(row["result_r"] or 0.0)
        entry = row["fill_price"] if row["fill_price"] is not None else row["entry"]
        exit_px = row["target"] if r > 0 else row["stop"]
        cash = execution.cash_delta(pair, row["side"], entry, exit_px, row["size"] or 0.0)
        if cash is None:
            cash = (balance * RISK_PCT / 100.0) * r
        balance = round(balance + cash, 2)
        wins += 1 if r > 0 else 0
        losses += 0 if r > 0 else 1
        points.append((pair, row["closed_at"], balance, r, row["alert_id"], round(float(cash), 2)))

    with cursor() as con:
        con.execute("DELETE FROM equity WHERE pair=?", (pair,))
        con.executemany(
            "INSERT INTO equity (pair,ts,balance,r,alert_id,cash) VALUES (?,?,?,?,?,?)", points)
        con.execute(
            "UPDATE accounts SET balance=?, wins=?, losses=?, updated_at=? WHERE pair=?",
            (balance, wins, losses, now(), pair))
    return {"pair": pair, "balance": balance, "wins": wins, "losses": losses,
            "trades": len(points)}


def equity_curve(pair: str, limit: int = 200) -> list[dict]:
    with cursor() as con:
        rows = con.execute(
            "SELECT ts,balance,r FROM equity WHERE pair=? ORDER BY ts ASC LIMIT ?",
            (pair, limit)).fetchall()
        return [dict(r) for r in rows]


def closed_ledger(pair: str | None = None, since: int | None = None, until: int | None = None) -> list[dict]:
    clauses = []
    args: list = []
    if pair:
        clauses.append("o.pair=?")
        args.append(pair)
    if since is not None:
        clauses.append("o.closed_at>=?")
        args.append(since)
    if until is not None:
        clauses.append("o.closed_at<=?")
        args.append(until)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    query = f"""
        WITH latest_outcomes AS (
          SELECT MAX(id) AS id
          FROM outcomes
          GROUP BY alert_id
        ),
        latest_equity AS (
          SELECT MAX(id) AS id, alert_id
          FROM equity
          GROUP BY alert_id
        )
        SELECT
          o.alert_id,
          o.pair,
          o.timeframe,
          o.zone_type,
          o.confluence,
          o.swept,
          o.result_r,
          o.closed_at,
          a.side,
          a.source,
          a.entry,
          a.stop,
          a.target,
          a.size,
          a.rr,
          a.trader_mode,
          a.armed_at,
          a.updated_at,
          e.balance,
          e.cash AS trade_cash,
          e.id AS equity_id,
          (
            SELECT MIN(ev.ts)
            FROM alert_events ev
            WHERE ev.alert_id = o.alert_id AND ev.state = 'FILLED'
          ) AS filled_at,
          (
            SELECT ev.price
            FROM alert_events ev
            WHERE ev.alert_id = o.alert_id AND ev.state = 'FILLED'
            ORDER BY ev.ts ASC, ev.id ASC
            LIMIT 1
          ) AS filled_price,
          (
            SELECT ev.price
            FROM alert_events ev
            WHERE ev.alert_id = o.alert_id AND ev.state IN ('TARGET', 'STOPPED')
            ORDER BY ev.ts DESC, ev.id DESC
            LIMIT 1
          ) AS exit_price
        FROM outcomes o
        JOIN latest_outcomes lo ON lo.id = o.id
        LEFT JOIN alerts a ON a.id = o.alert_id
        LEFT JOIN latest_equity le ON le.alert_id = o.alert_id
        LEFT JOIN equity e ON e.id = le.id
        {where}
        -- order by the TRUE account-write sequence (equity.id), not by the
        -- whole-second closed_at + arm-time alert_id which mis-orders same-bar closes
        ORDER BY (e.id IS NULL), e.id ASC, o.closed_at ASC, o.alert_id ASC
    """
    with cursor() as con:
        rows = [dict(r) for r in con.execute(query, args)]

    filtered: list[dict] = []
    prior_balance: dict[str, float] = {}
    for row in rows:
        quality_flags: list[str] = []
        if not _row_data_valid(row.get("pair"), row.get("entry"), row.get("stop"), row.get("target"), row.get("exit_price")):
            quality_flags.append("invalid_price")
        starting = get_account(row["pair"]).get("starting_balance", 1000.0)
        prev = prior_balance.get(row["pair"], starting)
        balance = row.get("balance") or prev
        if row.get("side") is None and row.get("entry") is not None and row.get("target") is not None:
            row["side"] = "long" if float(row["target"]) >= float(row["entry"]) else "short"
        # Prefer the per-trade cash stored at apply time; fall back to the
        # (now correctly-ordered) balance diff only for legacy rows that predate it.
        stored_cash = row.get("trade_cash")
        row["cash_delta"] = round(float(stored_cash), 2) if stored_cash is not None else round(float(balance) - float(prev), 2)
        row["balance"] = round(float(balance), 2)
        row["entry_time"] = row.get("filled_at") or row.get("armed_at")
        # actual fill price (from the FILLED event) so the chart's entry marker sits
        # where price really filled, not at the planned entry
        row["fill_price"] = row.get("filled_price") if row.get("filled_price") is not None else row.get("entry")
        row["exit_time"] = row.get("closed_at")
        row["exit_price"] = row.get("exit_price") or row.get("target")
        close_metrics = execution.distance_metrics(row["pair"], row.get("entry"), row.get("exit_price"))
        stop_metrics = execution.distance_metrics(row["pair"], row.get("entry"), row.get("stop"))
        target_metrics = execution.distance_metrics(row["pair"], row.get("entry"), row.get("target"))
        row["entry_to_close_points"] = close_metrics["points"]
        row["entry_to_close_pips"] = close_metrics["pips"]
        row["stop_distance_points"] = stop_metrics["points"]
        row["stop_distance_pips"] = stop_metrics["pips"]
        row["target_distance_points"] = target_metrics["points"]
        row["target_distance_pips"] = target_metrics["pips"]
        if row.get("filled_at") and row.get("entry") is not None and row.get("exit_price") is not None:
            row["replay_mode"] = "exact"
            row["entry_time_source"] = "filled_event"
            row["exit_time_source"] = "terminal_event"
        elif row.get("armed_at") and row.get("entry") is not None and row.get("exit_price") is not None:
            row["replay_mode"] = "estimated"
            row["entry_time_source"] = "armed_at"
            row["exit_time_source"] = "closed_at"
            quality_flags.append("missing_filled_event")
            row["estimated_replay_reason"] = "No recorded FILLED event; using armed time as the entry estimate."
        else:
            row["replay_mode"] = "unavailable"
            quality_flags.append("legacy_incomplete")
            row["estimated_replay_reason"] = "Trade history is incomplete and cannot be replayed exactly."
        if row.get("source") == "manual":
            quality_flags.append("manual_trade")
        row["data_quality_flags"] = quality_flags
        prior_balance[row["pair"]] = float(balance)
        filtered.append(row)
    visible = [row for row in filtered if "invalid_price" not in row.get("data_quality_flags", [])]
    return list(reversed(visible))


# ---------------------------------------------------------------- stats
def stats(pair: str | None = None, since: int | None = None, until: int | None = None) -> dict:
    rows = closed_ledger(pair, since, until)
    if not rows:
        return {"trades": 0, "win_rate": 0.0, "avg_r": 0.0, "net_r": 0.0, "net_cash": 0.0, "by_zone": [], "by_pair": []}
    wins = sum(1 for r in rows if r["result_r"] > 0)
    avg_r = sum(r["result_r"] for r in rows) / len(rows)
    by_zone: dict[str, list[float]] = {}
    by_pair: dict[str, list[dict]] = {}
    for r in rows:
        by_zone.setdefault(r["zone_type"], []).append(r["result_r"])
        by_pair.setdefault(r["pair"], []).append(r)
    zone_stats = [
        {"zone_type": z, "trades": len(v),
         "win_rate": round(100 * sum(1 for x in v if x > 0) / len(v), 1),
         "avg_r": round(sum(v) / len(v), 2)}
        for z, v in sorted(by_zone.items(), key=lambda kv: -len(kv[1]))
    ]
    pair_stats = [
        {
            "pair": p,
            "trades": len(v),
            "win_rate": round(100 * sum(1 for x in v if x["result_r"] > 0) / len(v), 1),
            "net_r": round(sum(x["result_r"] for x in v), 2),
            "net_cash": round(sum(x.get("cash_delta", 0.0) for x in v), 2),
        }
        for p, v in sorted(by_pair.items(), key=lambda kv: -sum(x.get("cash_delta", 0.0) for x in kv[1]))
    ]
    return {
        "trades": len(rows),
        "win_rate": round(100 * wins / len(rows), 1),
        "avg_r": round(avg_r, 2),
        "net_r": round(sum(r["result_r"] for r in rows), 2),
        "net_cash": round(sum(r.get("cash_delta", 0.0) for r in rows), 2),
        "by_zone": zone_stats,
        "by_pair": pair_stats,
    }


# ---------------------------------------------------------------- connectors
def list_connectors() -> list[dict]:
    with cursor() as con:
        rows = con.execute(
            """SELECT id, name, url, oauth_client_id, enabled, created_at, updated_at,
                      CASE WHEN oauth_client_secret IS NOT NULL
                            AND TRIM(oauth_client_secret) <> '' THEN 1 ELSE 0 END AS has_secret
               FROM connectors
               ORDER BY updated_at DESC, id DESC"""
        ).fetchall()
    return [
        {
            **dict(r),
            "enabled": bool(r["enabled"]),
            "has_secret": bool(r["has_secret"]),
        }
        for r in rows
    ]


def create_connector(item: dict) -> int:
    with cursor() as con:
        cur = con.execute(
            """INSERT INTO connectors
               (name, url, oauth_client_id, oauth_client_secret, enabled, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                item["name"],
                item["url"],
                item.get("oauth_client_id"),
                item.get("oauth_client_secret"),
                1 if item.get("enabled", True) else 0,
                now(),
                now(),
            ),
        )
        return int(cur.lastrowid)


def delete_connector(connector_id: int) -> bool:
    with cursor() as con:
        cur = con.execute("DELETE FROM connectors WHERE id=?", (connector_id,))
        return cur.rowcount > 0


def create_learning_model(row: dict) -> int:
    with cursor() as con:
        cur = con.execute(
            """INSERT INTO learning_models
               (pair, model_kind, version, status, trained_at, window_start, window_end,
                sample_count, holdout_size, artifact_json, metrics_json, summary_json, promotion_reason)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                row["pair"],
                row.get("model_kind", "setup_quality_v1"),
                row["version"],
                row["status"],
                row["trained_at"],
                row.get("window_start"),
                row.get("window_end"),
                row.get("sample_count", 0),
                row.get("holdout_size", 0),
                json.dumps(row.get("artifact") or {}),
                json.dumps(row.get("metrics") or {}),
                json.dumps(row.get("summary") or {}),
                row.get("promotion_reason"),
            ),
        )
        return int(cur.lastrowid)


def latest_learning_model(pair: str, status: str | None = None) -> dict | None:
    query = "SELECT * FROM learning_models WHERE pair=?"
    args: list = [pair]
    if status:
        query += " AND status=?"
        args.append(status)
    query += " ORDER BY trained_at DESC, id DESC LIMIT 1"
    with cursor() as con:
        row = con.execute(query, args).fetchone()
    if row is None:
        return None
    return _decode_learning_model(dict(row))


def learning_model_history(pair: str, limit: int = 10) -> list[dict]:
    with cursor() as con:
        rows = con.execute(
            "SELECT * FROM learning_models WHERE pair=? ORDER BY trained_at DESC, id DESC LIMIT ?",
            (pair, limit),
        ).fetchall()
    return [_decode_learning_model(dict(row)) for row in rows]


def get_user_settings() -> dict:
    with cursor() as con:
        row = con.execute("SELECT * FROM user_settings WHERE user_id='local'").fetchone()
    if row is None:
        return {
            "user_id": "local",
            "risk_pct": 1.0,
            "min_rr": 2.0,
            "max_positions": 3,
            "news_window_min": 30,
            "turtle_soup": 1,
            "trader_mode": "Swing Trader",
            "ai_provider": "builtin",
            "auto_apply": 0,
        }
    data = dict(row)
    if not data.get("trader_mode"):
        data["trader_mode"] = "Swing Trader"
    if not data.get("ai_provider"):
        data["ai_provider"] = "builtin"
    data["auto_apply"] = int(data.get("auto_apply") or 0)
    return data


def update_user_settings(**fields) -> dict:
    if not fields:
        return get_user_settings()
    cols = ", ".join(f"{k}=?" for k in fields)
    with cursor() as con:
        con.execute(
            """INSERT OR IGNORE INTO user_settings
               (user_id, risk_pct, min_rr, max_positions, news_window_min, turtle_soup, trader_mode, ai_provider)
               VALUES ('local', 1.0, 2.0, 3, 30, 1, 'Swing Trader', 'builtin')"""
        )
        con.execute(
            f"UPDATE user_settings SET {cols} WHERE user_id='local'",
            (*fields.values(),),
        )
    return get_user_settings()


def _setup_fingerprint(row: dict) -> str | None:
    if row.get("status") != "waiting":
        return None
    payload = {
        "pair": row.get("pair"),
        "trader_mode": row.get("trader_mode"),
        "side": row.get("side"),
        "source": row.get("source"),
        "reason": row.get("reason"),
        "entry": row.get("entry"),
        "stop": row.get("stop"),
        "target": row.get("target"),
        "bias_tf": row.get("bias_tf"),
        "setup_tf": row.get("setup_tf"),
        "entry_tf": row.get("entry_tf"),
    }
    return sha1(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _visible_journal_row(row: dict) -> bool:
    if "invalid_price" in row.get("quality_flags", []):
        return False
    reason = (row.get("reason") or "").lower()
    if "test_verdict_query_supports_safe_pair_ids" in reason:
        return False
    if DATA_SOURCE != "fixture" and row.get("feed_source") == "fixture":
        return False
    return True


def _decode_learning_model(row: dict) -> dict:
    row["artifact"] = json.loads(row.get("artifact_json") or "{}")
    row["metrics"] = json.loads(row.get("metrics_json") or "{}")
    row["summary"] = json.loads(row.get("summary_json") or "{}")
    row.pop("artifact_json", None)
    row.pop("metrics_json", None)
    row.pop("summary_json", None)
    return row


# ---------------------------------------------------------------- candles
def upsert_candles(pair: str, timeframe: str, rows: list[dict]) -> int:
    """Persist OHLC candles so history accumulates for the what-if replay.

    `rows` are dicts with epoch/open/high/low/close (as produced by the feed's
    `_norm_candle`). Dedupe is on (pair, timeframe, epoch) — a replayed epoch
    updates in place rather than duplicating.
    """
    if not rows:
        return 0
    payload = []
    for r in rows:
        epoch = r.get("epoch")
        if epoch is None:
            continue
        payload.append((
            pair, timeframe, int(epoch),
            r.get("open"), r.get("high"), r.get("low"), r.get("close"),
        ))
    if not payload:
        return 0
    with cursor() as con:
        con.executemany(
            """INSERT INTO candles (pair, timeframe, epoch, open, high, low, close)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(pair, timeframe, epoch) DO UPDATE SET
                 open=excluded.open, high=excluded.high,
                 low=excluded.low, close=excluded.close""",
            payload,
        )
    return len(payload)


def candle_timeframes(pair: str) -> list[str]:
    """Timeframes we have recorded candles for, on this pair."""
    with cursor() as con:
        rows = con.execute(
            "SELECT DISTINCT timeframe FROM candles WHERE pair=?", (pair,)
        ).fetchall()
    return [r[0] for r in rows]


def candle_range(pair: str, timeframe: str) -> tuple[int | None, int | None]:
    """(min_epoch, max_epoch) of recorded candles for pair/timeframe, or (None, None)."""
    with cursor() as con:
        row = con.execute(
            "SELECT MIN(epoch), MAX(epoch) FROM candles WHERE pair=? AND timeframe=?",
            (pair, timeframe),
        ).fetchone()
    if row is None or row[0] is None:
        return None, None
    return int(row[0]), int(row[1])


def delete_eod_reviews_for_day(pair: str, day: str) -> None:
    """Drop any prior review(s) for this pair+day so a history re-scan replaces
    rather than duplicates."""
    with cursor() as con:
        con.execute("DELETE FROM eod_reviews WHERE pair=? AND day=?", (pair, day))


def read_candles(pair: str, timeframe: str, start: int | None = None, until: int | None = None, limit: int = 5000) -> list[dict]:
    """Read persisted candles in [start, until] ascending by epoch."""
    clauses = ["pair=?", "timeframe=?"]
    args: list = [pair, timeframe]
    if start is not None:
        clauses.append("epoch>=?")
        args.append(int(start))
    if until is not None:
        clauses.append("epoch<=?")
        args.append(int(until))
    where = " AND ".join(clauses)
    with cursor() as con:
        rows = con.execute(
            f"SELECT epoch, open, high, low, close FROM candles WHERE {where} "
            f"ORDER BY epoch ASC LIMIT ?",
            (*args, limit),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- eod reviews
def create_eod_review(row: dict) -> int:
    with cursor() as con:
        cur = con.execute(
            """INSERT INTO eod_reviews
               (pair, day, day_start, day_end, realized_r, missed_r,
                review_json, flags_json, provider, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                row["pair"],
                row["day"],
                row.get("day_start"),
                row.get("day_end"),
                row.get("realized_r"),
                row.get("missed_r"),
                json.dumps(row.get("review") or {}),
                json.dumps(row.get("flags") or []),
                row.get("provider"),
                row.get("created_at", now()),
            ),
        )
        return int(cur.lastrowid)


def _decode_eod_review(row: dict) -> dict:
    row["review"] = json.loads(row.pop("review_json", None) or "{}")
    row["flags"] = json.loads(row.pop("flags_json", None) or "[]")
    return row


def latest_eod_review(pair: str, day: str | None = None) -> dict | None:
    query = "SELECT * FROM eod_reviews WHERE pair=?"
    args: list = [pair]
    if day is not None:
        query += " AND day=?"
        args.append(day)
    query += " ORDER BY created_at DESC, id DESC LIMIT 1"
    with cursor() as con:
        row = con.execute(query, args).fetchone()
    return _decode_eod_review(dict(row)) if row else None


def get_eod_review(review_id: int) -> dict | None:
    with cursor() as con:
        row = con.execute("SELECT * FROM eod_reviews WHERE id=?", (review_id,)).fetchone()
    return _decode_eod_review(dict(row)) if row else None


def eod_review_history(pair: str, limit: int = 30) -> list[dict]:
    with cursor() as con:
        rows = con.execute(
            "SELECT * FROM eod_reviews WHERE pair=? ORDER BY created_at DESC, id DESC LIMIT ?",
            (pair, limit),
        ).fetchall()
    return [_decode_eod_review(dict(r)) for r in rows]


# ------------------------------------------------ strategy recommendations
def create_recommendation(row: dict) -> int:
    with cursor() as con:
        cur = con.execute(
            """INSERT INTO strategy_recommendations
               (pair, eod_review_id, param, current_value, proposed_value, rationale,
                expected_added_trades, expected_r_impact, shadow_delta_r, shadow_added_trades,
                confidence, status, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                row["pair"],
                row.get("eod_review_id"),
                row["param"],
                row.get("current_value"),
                row.get("proposed_value"),
                row.get("rationale"),
                row.get("expected_added_trades"),
                row.get("expected_r_impact"),
                row.get("shadow_delta_r"),
                row.get("shadow_added_trades"),
                row.get("confidence"),
                row.get("status", "proposed"),
                row.get("created_at", now()),
                now(),
            ),
        )
        return int(cur.lastrowid)


def list_recommendations(pair: str | None = None, status: str | None = None, limit: int = 50) -> list[dict]:
    clauses = []
    args: list = []
    if pair:
        clauses.append("pair=?")
        args.append(pair)
    if status:
        clauses.append("status=?")
        args.append(status)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with cursor() as con:
        rows = con.execute(
            f"SELECT * FROM strategy_recommendations {where} "
            f"ORDER BY created_at DESC, id DESC LIMIT ?",
            (*args, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def get_recommendation(rec_id: int) -> dict | None:
    with cursor() as con:
        row = con.execute("SELECT * FROM strategy_recommendations WHERE id=?", (rec_id,)).fetchone()
    return dict(row) if row else None


def update_recommendation_status(rec_id: int, status: str) -> None:
    with cursor() as con:
        con.execute(
            "UPDATE strategy_recommendations SET status=?, updated_at=? WHERE id=?",
            (status, now(), rec_id),
        )


# ---------------------------------------------------------------- param overrides
def set_param_override(scope: str, param: str, value: float, source_recommendation_id: int | None = None) -> None:
    with cursor() as con:
        con.execute(
            """INSERT INTO param_overrides (scope, param, value, source_recommendation_id, updated_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(scope, param) DO UPDATE SET
                 value=excluded.value,
                 source_recommendation_id=excluded.source_recommendation_id,
                 updated_at=excluded.updated_at""",
            (scope, param, value, source_recommendation_id, now()),
        )


def clear_param_override(scope: str, param: str) -> None:
    with cursor() as con:
        con.execute("DELETE FROM param_overrides WHERE scope=? AND param=?", (scope, param))


def clear_all_overrides(scope: str) -> int:
    """Remove every parameter override for a scope (a pair id, or 'global') — the
    Reset control that reverts the planner/monitor to config defaults."""
    with cursor() as con:
        cur = con.execute("DELETE FROM param_overrides WHERE scope=?", (scope,))
        return cur.rowcount


def get_param_overrides(scope: str | None = None) -> dict:
    """Effective overrides as {param: value}. When `scope` is a pair, pair-scoped
    values win over 'global'. Returns an empty dict when nothing is overridden."""
    with cursor() as con:
        rows = con.execute("SELECT scope, param, value FROM param_overrides").fetchall()
    global_vals: dict[str, float] = {}
    scoped_vals: dict[str, float] = {}
    for r in rows:
        if r["scope"] == "global":
            global_vals[r["param"]] = r["value"]
        elif scope is not None and r["scope"] == scope:
            scoped_vals[r["param"]] = r["value"]
    return {**global_vals, **scoped_vals}
