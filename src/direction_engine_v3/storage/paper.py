"""SQLite-backed append-only PAPER execution audit repository."""

import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path

from direction_engine_v3.domain._validation import require_decimal, require_text, require_utc


@dataclass(frozen=True, slots=True)
class StoredExecution:
    idempotency_key: str
    plan_id: str
    payload: Mapping[str, object]
    recorded_at: datetime


@dataclass(frozen=True, slots=True)
class PaperTradeSnapshot:
    trade_id: str
    decision_id: str
    strategy: str
    asset: str
    horizon: str
    condition_id: str
    side: str
    status: str
    label: str
    payload: Mapping[str, object]
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class PaperAbstainRecord:
    abstain_id: str
    strategy: str
    asset: str
    horizon: str
    condition_id: str
    reason: str
    payload: Mapping[str, object]
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class PaperTradeSettlement:
    settlement_id: str
    trade_id: str
    condition_id: str
    official_winning_side: str
    selected_side: str
    settlement_source_kind: str
    settlement_source: str
    official_resolved_at: datetime
    settled_at: datetime
    filled_shares: Decimal
    cost_basis_usdc: Decimal
    payout_usdc: Decimal
    realized_paper_pnl: Decimal
    win_loss: str
    evidence_hash: str
    payload: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class PaperTradeIdentityOverlay:
    condition_id: str
    status: str
    market_id: str | None
    asset: str
    horizon: str
    window_start: datetime | None
    window_end: datetime | None
    outcome_tokens: Mapping[str, object]
    source_kind: str
    source: str
    retrieved_at: datetime
    verified_at: datetime
    evidence_hash: str
    blocker_reason: str | None
    payload: Mapping[str, object]


class SQLitePaperRepository:
    """Durable idempotency and immutable audit events; never stores credentials."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def initialize(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._path) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS paper_executions (
                    idempotency_key TEXT PRIMARY KEY,
                    plan_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    recorded_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audit_events (
                    event_id TEXT PRIMARY KEY,
                    reference_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    occurred_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS paper_trade_snapshots (
                    trade_id TEXT PRIMARY KEY,
                    decision_id TEXT NOT NULL,
                    strategy TEXT NOT NULL,
                    asset TEXT NOT NULL,
                    horizon TEXT NOT NULL,
                    condition_id TEXT NOT NULL,
                    side TEXT NOT NULL,
                    status TEXT NOT NULL,
                    label TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    observed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS paper_abstains (
                    abstain_id TEXT PRIMARY KEY,
                    strategy TEXT NOT NULL,
                    asset TEXT NOT NULL,
                    horizon TEXT NOT NULL,
                    condition_id TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    observed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS paper_trade_settlements (
                    settlement_id TEXT PRIMARY KEY,
                    trade_id TEXT NOT NULL UNIQUE,
                    condition_id TEXT NOT NULL,
                    official_winning_side TEXT NOT NULL,
                    selected_side TEXT NOT NULL,
                    settlement_source_kind TEXT NOT NULL,
                    settlement_source TEXT NOT NULL,
                    official_resolved_at TEXT NOT NULL,
                    settled_at TEXT NOT NULL,
                    filled_shares TEXT NOT NULL,
                    cost_basis_usdc TEXT NOT NULL,
                    payout_usdc TEXT NOT NULL,
                    realized_paper_pnl TEXT NOT NULL,
                    win_loss TEXT NOT NULL,
                    evidence_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS paper_trade_identity_overlays (
                    condition_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    market_id TEXT,
                    asset TEXT NOT NULL,
                    horizon TEXT NOT NULL,
                    window_start TEXT,
                    window_end TEXT,
                    outcome_tokens_json TEXT NOT NULL,
                    source_kind TEXT NOT NULL,
                    source TEXT NOT NULL,
                    retrieved_at TEXT NOT NULL,
                    verified_at TEXT NOT NULL,
                    evidence_hash TEXT NOT NULL,
                    blocker_reason TEXT,
                    payload_json TEXT NOT NULL
                );
                """
            )

    def get(self, idempotency_key: str) -> StoredExecution | None:
        require_text("idempotency_key", idempotency_key)
        with sqlite3.connect(self._path) as connection:
            row = connection.execute(
                "SELECT plan_id,payload_json,recorded_at FROM paper_executions "
                "WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(row[1])
        if not isinstance(payload, dict):
            raise RuntimeError("stored execution payload is not an object")
        return StoredExecution(idempotency_key, row[0], payload, datetime.fromisoformat(row[2]))

    def save_once(
        self,
        *,
        idempotency_key: str,
        plan_id: str,
        payload: Mapping[str, object],
        recorded_at: datetime,
    ) -> StoredExecution:
        require_text("idempotency_key", idempotency_key)
        require_text("plan_id", plan_id)
        require_utc("recorded_at", recorded_at)
        encoded = json.dumps(_jsonable(dict(payload)), sort_keys=True, separators=(",", ":"))
        with sqlite3.connect(self._path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT OR IGNORE INTO paper_executions VALUES (?,?,?,?)",
                (idempotency_key, plan_id, encoded, recorded_at.isoformat()),
            )
            connection.execute(
                "INSERT OR IGNORE INTO audit_events VALUES (?,?,?,?,?)",
                (
                    f"paper:{idempotency_key}",
                    plan_id,
                    "PAPER_EXECUTION",
                    encoded,
                    recorded_at.isoformat(),
                ),
            )
        stored = self.get(idempotency_key)
        if stored is None:
            raise RuntimeError("execution was not durably recorded")
        return stored

    def audit_count(self, reference_id: str) -> int:
        require_text("reference_id", reference_id)
        with sqlite3.connect(self._path) as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM audit_events WHERE reference_id=?", (reference_id,)
            ).fetchone()
        return int(row[0]) if row is not None else 0

    def save_trade_snapshot(
        self,
        *,
        trade_id: str,
        decision_id: str,
        strategy: str,
        asset: str,
        horizon: str,
        condition_id: str,
        side: str,
        status: str,
        payload: Mapping[str, object],
        observed_at: datetime,
    ) -> PaperTradeSnapshot:
        require_text("trade_id", trade_id)
        require_text("decision_id", decision_id)
        require_text("strategy", strategy)
        require_text("asset", asset)
        require_text("horizon", horizon)
        require_text("condition_id", condition_id)
        require_text("side", side)
        require_text("status", status)
        require_utc("observed_at", observed_at)
        label = "PAPER / SHADOW — NO REAL ORDER"
        encoded = json.dumps(_jsonable(dict(payload)), sort_keys=True, separators=(",", ":"))
        with sqlite3.connect(self._path) as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO paper_trade_snapshots
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    trade_id,
                    decision_id,
                    strategy,
                    asset,
                    horizon,
                    condition_id,
                    side,
                    status,
                    label,
                    encoded,
                    observed_at.isoformat(),
                ),
            )
        stored = self.trade_by_id(trade_id)
        if stored is None:
            raise RuntimeError("paper trade snapshot was not durably recorded")
        return stored

    def save_abstain(
        self,
        *,
        abstain_id: str,
        strategy: str,
        asset: str,
        horizon: str,
        condition_id: str,
        reason: str,
        payload: Mapping[str, object],
        observed_at: datetime,
    ) -> PaperAbstainRecord:
        require_text("abstain_id", abstain_id)
        require_text("strategy", strategy)
        require_text("asset", asset)
        require_text("horizon", horizon)
        require_text("condition_id", condition_id)
        require_text("reason", reason)
        require_utc("observed_at", observed_at)
        encoded = json.dumps(_jsonable(dict(payload)), sort_keys=True, separators=(",", ":"))
        with sqlite3.connect(self._path) as connection:
            connection.execute(
                "INSERT OR IGNORE INTO paper_abstains VALUES (?,?,?,?,?,?,?,?)",
                (
                    abstain_id,
                    strategy,
                    asset,
                    horizon,
                    condition_id,
                    reason,
                    encoded,
                    observed_at.isoformat(),
                ),
            )
        stored = self.abstains(
            limit=1,
            reason=reason,
            strategy=strategy,
            asset=asset,
            horizon=horizon,
        )
        if not stored:
            raise RuntimeError("paper abstain record was not durably recorded")
        return stored[0]

    def save_settlement_once(
        self,
        *,
        settlement_id: str,
        trade_id: str,
        condition_id: str,
        official_winning_side: str,
        selected_side: str,
        settlement_source_kind: str,
        settlement_source: str,
        official_resolved_at: datetime,
        settled_at: datetime,
        filled_shares: Decimal,
        cost_basis_usdc: Decimal,
        payout_usdc: Decimal,
        realized_paper_pnl: Decimal,
        win_loss: str,
        evidence_hash: str,
        payload: Mapping[str, object],
    ) -> PaperTradeSettlement:
        for name, value in (
            ("settlement_id", settlement_id),
            ("trade_id", trade_id),
            ("condition_id", condition_id),
            ("official_winning_side", official_winning_side),
            ("selected_side", selected_side),
            ("settlement_source_kind", settlement_source_kind),
            ("settlement_source", settlement_source),
            ("win_loss", win_loss),
            ("evidence_hash", evidence_hash),
        ):
            require_text(name, value)
        require_utc("official_resolved_at", official_resolved_at)
        require_utc("settled_at", settled_at)
        require_decimal("filled_shares", filled_shares, minimum=Decimal("0"))
        require_decimal("cost_basis_usdc", cost_basis_usdc, minimum=Decimal("0"))
        require_decimal("payout_usdc", payout_usdc, minimum=Decimal("0"))
        require_decimal("realized_paper_pnl", realized_paper_pnl)
        encoded = json.dumps(_jsonable(dict(payload)), sort_keys=True, separators=(",", ":"))
        values = (
            settlement_id,
            trade_id,
            condition_id,
            official_winning_side,
            selected_side,
            settlement_source_kind,
            settlement_source,
            official_resolved_at.isoformat(),
            settled_at.isoformat(),
            str(filled_shares),
            str(cost_basis_usdc),
            str(payout_usdc),
            str(realized_paper_pnl),
            win_loss,
            evidence_hash,
            encoded,
        )
        with sqlite3.connect(self._path) as connection:
            existing = connection.execute(
                "SELECT settlement_id,trade_id,condition_id,official_winning_side,"
                "selected_side,settlement_source_kind,settlement_source,official_resolved_at,"
                "settled_at,filled_shares,cost_basis_usdc,payout_usdc,realized_paper_pnl,"
                "win_loss,evidence_hash,payload_json FROM paper_trade_settlements "
                "WHERE trade_id=?",
                (trade_id,),
            ).fetchone()
            if existing is not None:
                stored = _settlement_from_row(existing)
                if stored.evidence_hash != evidence_hash or stored.win_loss != win_loss:
                    raise RuntimeError("conflicting PAPER settlement for trade")
                return stored
            connection.execute(
                """
                INSERT INTO paper_trade_settlements
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                values,
            )
            connection.execute(
                "INSERT OR IGNORE INTO audit_events VALUES (?,?,?,?,?)",
                (
                    f"paper-settlement:{trade_id}",
                    trade_id,
                    "PAPER_SETTLEMENT",
                    encoded,
                    settled_at.isoformat(),
                ),
            )
        inserted = self.settlement_for_trade(trade_id)
        if inserted is None:
            raise RuntimeError("paper settlement was not durably recorded")
        return inserted

    def settlement_for_trade(self, trade_id: str) -> PaperTradeSettlement | None:
        require_text("trade_id", trade_id)
        with sqlite3.connect(self._path) as connection:
            row = connection.execute(
                "SELECT settlement_id,trade_id,condition_id,official_winning_side,"
                "selected_side,settlement_source_kind,settlement_source,official_resolved_at,"
                "settled_at,filled_shares,cost_basis_usdc,payout_usdc,realized_paper_pnl,"
                "win_loss,evidence_hash,payload_json FROM paper_trade_settlements "
                "WHERE trade_id=?",
                (trade_id,),
            ).fetchone()
        return None if row is None else _settlement_from_row(row)

    def settlements(self) -> tuple[PaperTradeSettlement, ...]:
        with sqlite3.connect(self._path) as connection:
            rows = connection.execute(
                "SELECT settlement_id,trade_id,condition_id,official_winning_side,"
                "selected_side,settlement_source_kind,settlement_source,official_resolved_at,"
                "settled_at,filled_shares,cost_basis_usdc,payout_usdc,realized_paper_pnl,"
                "win_loss,evidence_hash,payload_json FROM paper_trade_settlements "
                "ORDER BY settled_at DESC"
            ).fetchall()
        return tuple(_settlement_from_row(row) for row in rows)

    def save_identity_overlay_once(
        self,
        *,
        condition_id: str,
        status: str,
        asset: str,
        horizon: str,
        source_kind: str,
        source: str,
        retrieved_at: datetime,
        verified_at: datetime,
        evidence_hash: str,
        market_id: str | None = None,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
        outcome_tokens: Mapping[str, object] | None = None,
        blocker_reason: str | None = None,
        payload: Mapping[str, object] | None = None,
    ) -> PaperTradeIdentityOverlay:
        require_text("condition_id", condition_id)
        require_text("status", status)
        require_text("asset", asset)
        require_text("horizon", horizon)
        require_text("source_kind", source_kind)
        require_text("source", source)
        require_text("evidence_hash", evidence_hash)
        require_utc("retrieved_at", retrieved_at)
        require_utc("verified_at", verified_at)
        if market_id is not None:
            require_text("market_id", market_id)
        if window_start is not None:
            require_utc("window_start", window_start)
        if window_end is not None:
            require_utc("window_end", window_end)
        token_payload = dict(outcome_tokens or {})
        overlay_payload = dict(payload or {})
        values = (
            condition_id,
            status,
            market_id,
            asset,
            horizon,
            window_start.isoformat() if window_start is not None else None,
            window_end.isoformat() if window_end is not None else None,
            json.dumps(_jsonable(token_payload), sort_keys=True, separators=(",", ":")),
            source_kind,
            source,
            retrieved_at.isoformat(),
            verified_at.isoformat(),
            evidence_hash,
            blocker_reason,
            json.dumps(_jsonable(overlay_payload), sort_keys=True, separators=(",", ":")),
        )
        with sqlite3.connect(self._path) as connection:
            existing = connection.execute(
                "SELECT condition_id,status,market_id,asset,horizon,window_start,window_end,"
                "outcome_tokens_json,source_kind,source,retrieved_at,verified_at,evidence_hash,"
                "blocker_reason,payload_json FROM paper_trade_identity_overlays "
                "WHERE condition_id=?",
                (condition_id,),
            ).fetchone()
            if existing is not None:
                stored = _identity_overlay_from_row(existing)
                if (
                    stored.status != status
                    or stored.market_id != market_id
                    or stored.asset != asset
                    or stored.horizon != horizon
                    or stored.window_start != window_start
                    or stored.window_end != window_end
                    or dict(stored.outcome_tokens) != token_payload
                    or stored.evidence_hash != evidence_hash
                    or stored.blocker_reason != blocker_reason
                ):
                    raise RuntimeError("conflicting paper identity overlay")
                return stored
            connection.execute(
                "INSERT INTO paper_trade_identity_overlays VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                values,
            )
        inserted = self.identity_overlay(condition_id)
        if inserted is None:
            raise RuntimeError("paper identity overlay was not durably recorded")
        return inserted

    def identity_overlay(self, condition_id: str) -> PaperTradeIdentityOverlay | None:
        require_text("condition_id", condition_id)
        with sqlite3.connect(self._path) as connection:
            row = connection.execute(
                "SELECT condition_id,status,market_id,asset,horizon,window_start,window_end,"
                "outcome_tokens_json,source_kind,source,retrieved_at,verified_at,evidence_hash,"
                "blocker_reason,payload_json FROM paper_trade_identity_overlays "
                "WHERE condition_id=?",
                (condition_id,),
            ).fetchone()
        return None if row is None else _identity_overlay_from_row(row)

    def identity_overlay_counts(self) -> dict[str, int]:
        with sqlite3.connect(self._path) as connection:
            rows = connection.execute(
                "SELECT status,COUNT(*) FROM paper_trade_identity_overlays GROUP BY status"
            ).fetchall()
        counts = {str(status): int(count) for status, count in rows}
        return {
            "identity_recovered_count": counts.get("RECOVERED", 0),
            "identity_blocked_count": counts.get("BLOCKED", 0),
        }

    def trades(
        self,
        *,
        asset: str | None = None,
        horizon: str | None = None,
        strategy: str | None = None,
        side: str | None = None,
        status: str | None = None,
        win_loss: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 250,
        offset: int = 0,
    ) -> tuple[PaperTradeSnapshot, ...]:
        if limit < 1:
            raise ValueError("limit must be positive")
        if offset < 0:
            raise ValueError("offset must be non-negative")
        clauses: list[str] = []
        params: list[object] = []
        post_status = status
        post_win_loss = win_loss
        for column, value in (
            ("asset", asset),
            ("horizon", horizon),
            ("strategy", strategy),
            ("side", side),
        ):
            if value is not None:
                require_text(column, value)
                clauses.append(f"{column}=?")
                params.append(value)
        if post_status is not None:
            require_text("status", post_status)
        if post_win_loss is not None:
            require_text("win_loss", post_win_loss)
        if start is not None:
            require_utc("start", start)
            clauses.append("observed_at>=?")
            params.append(start.isoformat())
        if end is not None:
            require_utc("end", end)
            clauses.append("observed_at<=?")
            params.append(end.isoformat())
        query = (
            "SELECT trade_id,decision_id,strategy,asset,horizon,condition_id,side,status,"
            "label,payload_json,observed_at FROM paper_trade_snapshots"
        )
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY observed_at DESC"
        with sqlite3.connect(self._path) as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        trades = tuple(self._overlay_trade(_trade_from_row(row)) for row in rows)
        if post_status is not None:
            trades = tuple(item for item in trades if item.status == post_status)
        if post_win_loss is not None:
            trades = tuple(item for item in trades if item.payload.get("win_loss") == post_win_loss)
        return trades[offset : offset + limit]

    def trade_by_id(self, trade_id: str) -> PaperTradeSnapshot | None:
        require_text("trade_id", trade_id)
        with sqlite3.connect(self._path) as connection:
            row = connection.execute(
                "SELECT trade_id,decision_id,strategy,asset,horizon,condition_id,side,status,"
                "label,payload_json,observed_at FROM paper_trade_snapshots WHERE trade_id=?",
                (trade_id,),
            ).fetchone()
        return None if row is None else self._overlay_trade(_trade_from_row(row))

    def abstains(
        self,
        *,
        reason: str | None = None,
        strategy: str | None = None,
        asset: str | None = None,
        horizon: str | None = None,
        limit: int = 250,
        offset: int = 0,
    ) -> tuple[PaperAbstainRecord, ...]:
        if limit < 1:
            raise ValueError("limit must be positive")
        if offset < 0:
            raise ValueError("offset must be non-negative")
        clauses: list[str] = []
        params: list[object] = []
        for column, value in (
            ("reason", reason),
            ("strategy", strategy),
            ("asset", asset),
            ("horizon", horizon),
        ):
            if value is not None:
                require_text(column, value)
                clauses.append(f"{column}=?")
                params.append(value)
        query = (
            "SELECT abstain_id,strategy,asset,horizon,condition_id,reason,payload_json,"
            "observed_at FROM paper_abstains"
        )
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY observed_at DESC LIMIT ? OFFSET ?"
        params.extend((limit, offset))
        with sqlite3.connect(self._path) as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return tuple(_abstain_from_row(row) for row in rows)

    def summary(
        self,
        *,
        initial_equity: Decimal = Decimal("1000"),
        now: datetime | None = None,
    ) -> dict[str, object]:
        require_decimal("initial_equity", initial_equity, minimum=Decimal("0"))
        if now is None:
            now = datetime.now(UTC)
        require_utc("now", now)
        trades = self.trades(limit=100_000)
        realized_pnl = sum(
            (Decimal(str(item.payload.get("realized_paper_pnl", "0"))) for item in trades),
            Decimal("0"),
        )
        total_fees = sum(
            (Decimal(str(item.payload.get("fee", "0"))) for item in trades),
            Decimal("0"),
        )
        settled = tuple(item for item in trades if item.status == "SETTLED")
        open_trades = tuple(item for item in trades if item.status == "OPEN")
        acknowledged = tuple(item for item in trades if item.status == "ACKNOWLEDGED")
        pending = tuple(item for item in trades if item.status == "SETTLEMENT_PENDING")
        wins = sum(1 for item in settled if item.payload.get("win_loss") == "WIN")
        losses = sum(1 for item in settled if item.payload.get("win_loss") == "LOSS")
        voids = sum(1 for item in settled if item.payload.get("win_loss") == "VOID")
        settled_cost_basis = sum(
            (_capital_basis(item) for item in settled),
            Decimal("0"),
        )
        net_edges = [
            Decimal(str(item.payload["net_edge"]))
            for item in trades
            if item.payload.get("net_edge") is not None
        ]
        overlays = {
            item.condition_id: self.identity_overlay(item.condition_id) for item in open_trades
        }
        open_cost_basis = sum(
            (_capital_basis(item) for item in open_trades),
            Decimal("0"),
        )
        unfilled_reservations = sum(
            (_capital_basis(item) for item in acknowledged),
            Decimal("0"),
        )
        known_active_open_cost_basis = sum(
            (
                _capital_basis(item)
                for item in open_trades
                if (window_end := _effective_window_end(item, overlays.get(item.condition_id)))
                is not None
                and window_end > now
            ),
            Decimal("0"),
        )
        known_expired_unsettled_cost_basis = sum(
            (
                _capital_basis(item)
                for item in open_trades
                if (window_end := _effective_window_end(item, overlays.get(item.condition_id)))
                is not None
                and window_end <= now
            ),
            Decimal("0"),
        )
        unknown_window_open_cost_basis = sum(
            (
                _capital_basis(item)
                for item in open_trades
                if _effective_window_end(item, overlays.get(item.condition_id)) is None
            ),
            Decimal("0"),
        )
        legacy_missing_window_end_count = sum(
            1 for item in open_trades if _payload_datetime(item.payload.get("window_end")) is None
        )
        open_unique_condition_count = len({item.condition_id for item in open_trades})
        identity_counts = self.identity_overlay_counts()
        raw_available_capital = (
            initial_equity + realized_pnl - open_cost_basis - unfilled_reservations
        )
        spendable_capital = max(Decimal("0"), raw_available_capital)
        return {
            "label": "PAPER / SHADOW — NO REAL ORDER",
            "initial_equity": str(initial_equity),
            "paper_initial_equity": str(initial_equity),
            "paper_current_equity": str(initial_equity + realized_pnl),
            "available_capital": str(raw_available_capital),
            "raw_available_capital": str(raw_available_capital),
            "spendable_capital": str(spendable_capital),
            "realized_pnl": str(realized_pnl),
            "open_cost_basis": str(open_cost_basis),
            "known_active_open_cost_basis": str(known_active_open_cost_basis),
            "known_expired_unsettled_cost_basis": str(known_expired_unsettled_cost_basis),
            "unknown_window_open_cost_basis": str(unknown_window_open_cost_basis),
            "expired_but_unsettled_cost_basis": str(known_expired_unsettled_cost_basis),
            "unfilled_reservations": str(unfilled_reservations),
            "unrealized_open_exposure": str(open_cost_basis),
            "open_positions": len(open_trades),
            "open_trade_count": len(open_trades),
            "open_unique_condition_count": open_unique_condition_count,
            "legacy_missing_window_end_count": legacy_missing_window_end_count,
            **identity_counts,
            "settlement_pending": len(pending),
            "settled_trades": len(settled),
            "wins": wins,
            "losses": losses,
            "voids": voids,
            "win_rate": str(Decimal(wins) / Decimal(len(settled))) if settled else "0",
            "roi": str(realized_pnl / settled_cost_basis) if settled_cost_basis else "0",
            "total_fees": str(total_fees),
            "average_net_edge": str(sum(net_edges, Decimal("0")) / Decimal(len(net_edges)))
            if net_edges
            else "0",
            "maximum_drawdown": str(_maximum_drawdown(trades)),
            "current_losing_streak": _current_losing_streak(trades),
        }

    def performance(self, *, strategy: str = "DIRECTIONAL_EDGE") -> dict[str, object]:
        require_text("strategy", strategy)
        trades = self.trades(strategy=strategy, limit=100_000)
        summary = self.summary()
        buckets: dict[str, dict[str, object]] = {}
        for item in trades:
            key = f"{item.asset}-{item.horizon}"
            bucket = buckets.setdefault(
                key,
                {
                    "asset": item.asset,
                    "horizon": item.horizon,
                    "total_trades": 0,
                    "open_positions": 0,
                    "settled_trades": 0,
                    "wins": 0,
                    "losses": 0,
                    "realized_pnl": "0",
                },
            )
            bucket["total_trades"] = int(str(bucket["total_trades"])) + 1
            if item.status == "OPEN":
                bucket["open_positions"] = int(str(bucket["open_positions"])) + 1
            if item.status == "SETTLED":
                bucket["settled_trades"] = int(str(bucket["settled_trades"])) + 1
                if item.payload.get("win_loss") == "WIN":
                    bucket["wins"] = int(str(bucket["wins"])) + 1
                if item.payload.get("win_loss") == "LOSS":
                    bucket["losses"] = int(str(bucket["losses"])) + 1
                pnl = Decimal(str(bucket["realized_pnl"])) + Decimal(
                    str(item.payload.get("realized_paper_pnl", "0"))
                )
                bucket["realized_pnl"] = str(pnl)
        return {
            "label": "PAPER / SHADOW — NO REAL ORDER",
            "strategy": strategy,
            "summary": summary,
            "buckets": tuple(buckets.values()),
        }

    def _overlay_trade(self, trade: PaperTradeSnapshot) -> PaperTradeSnapshot:
        settlement = self.settlement_for_trade(trade.trade_id)
        if settlement is None:
            return trade
        payload = dict(trade.payload)
        payload.update(
            {
                "official_winning_side": settlement.official_winning_side,
                "settlement_source_kind": settlement.settlement_source_kind,
                "settlement_source": settlement.settlement_source,
                "official_resolved_at": settlement.official_resolved_at.isoformat(),
                "settled_at": settlement.settled_at.isoformat(),
                "filled_shares": str(settlement.filled_shares),
                "cost_basis_usdc": str(settlement.cost_basis_usdc),
                "payout_usdc": str(settlement.payout_usdc),
                "realized_paper_pnl": str(settlement.realized_paper_pnl),
                "win_loss": settlement.win_loss,
                "evidence_hash": settlement.evidence_hash,
                "position_status": "SETTLED",
            }
        )
        return PaperTradeSnapshot(
            trade.trade_id,
            trade.decision_id,
            trade.strategy,
            trade.asset,
            trade.horizon,
            trade.condition_id,
            trade.side,
            "SETTLED",
            trade.label,
            payload,
            trade.observed_at,
        )

    @property
    def path(self) -> Path:
        return self._path


def _trade_from_row(row: tuple[object, ...]) -> PaperTradeSnapshot:
    payload = json.loads(str(row[9]))
    if not isinstance(payload, dict):
        raise RuntimeError("paper trade payload is not an object")
    return PaperTradeSnapshot(
        str(row[0]),
        str(row[1]),
        str(row[2]),
        str(row[3]),
        str(row[4]),
        str(row[5]),
        str(row[6]),
        str(row[7]),
        str(row[8]),
        payload,
        datetime.fromisoformat(str(row[10])),
    )


def _abstain_from_row(row: tuple[object, ...]) -> PaperAbstainRecord:
    payload = json.loads(str(row[6]))
    if not isinstance(payload, dict):
        raise RuntimeError("paper abstain payload is not an object")
    return PaperAbstainRecord(
        str(row[0]),
        str(row[1]),
        str(row[2]),
        str(row[3]),
        str(row[4]),
        str(row[5]),
        payload,
        datetime.fromisoformat(str(row[7])),
    )


def _settlement_from_row(row: tuple[object, ...]) -> PaperTradeSettlement:
    payload = json.loads(str(row[15]))
    if not isinstance(payload, dict):
        raise RuntimeError("paper settlement payload is not an object")
    return PaperTradeSettlement(
        str(row[0]),
        str(row[1]),
        str(row[2]),
        str(row[3]),
        str(row[4]),
        str(row[5]),
        str(row[6]),
        datetime.fromisoformat(str(row[7])),
        datetime.fromisoformat(str(row[8])),
        Decimal(str(row[9])),
        Decimal(str(row[10])),
        Decimal(str(row[11])),
        Decimal(str(row[12])),
        str(row[13]),
        str(row[14]),
        payload,
    )


def _identity_overlay_from_row(row: tuple[object, ...]) -> PaperTradeIdentityOverlay:
    tokens = json.loads(str(row[7]))
    payload = json.loads(str(row[14]))
    if not isinstance(tokens, dict) or not isinstance(payload, dict):
        raise RuntimeError("paper identity overlay payload is not an object")
    return PaperTradeIdentityOverlay(
        condition_id=str(row[0]),
        status=str(row[1]),
        market_id=None if row[2] is None else str(row[2]),
        asset=str(row[3]),
        horizon=str(row[4]),
        window_start=None if row[5] is None else datetime.fromisoformat(str(row[5])),
        window_end=None if row[6] is None else datetime.fromisoformat(str(row[6])),
        outcome_tokens=tokens,
        source_kind=str(row[8]),
        source=str(row[9]),
        retrieved_at=datetime.fromisoformat(str(row[10])),
        verified_at=datetime.fromisoformat(str(row[11])),
        evidence_hash=str(row[12]),
        blocker_reason=None if row[13] is None else str(row[13]),
        payload=payload,
    )


def _current_losing_streak(trades: tuple[PaperTradeSnapshot, ...]) -> int:
    streak = 0
    for item in sorted(trades, key=lambda trade: trade.observed_at, reverse=True):
        outcome = item.payload.get("win_loss")
        if outcome == "LOSS":
            streak += 1
            continue
        if outcome == "WIN":
            break
    return streak


def _maximum_drawdown(trades: tuple[PaperTradeSnapshot, ...]) -> Decimal:
    equity = peak = maximum = Decimal("0")
    settled = sorted(
        (item for item in trades if item.status == "SETTLED"),
        key=lambda trade: str(trade.payload.get("settled_at", trade.observed_at.isoformat())),
    )
    for item in settled:
        equity += Decimal(str(item.payload.get("realized_paper_pnl", "0")))
        peak = max(peak, equity)
        maximum = max(maximum, peak - equity)
    return maximum


def _capital_basis(trade: PaperTradeSnapshot) -> Decimal:
    return Decimal(str(trade.payload.get("cost_basis_usdc", trade.payload.get("stake", "0"))))


def _payload_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def _effective_window_end(
    trade: PaperTradeSnapshot, overlay: PaperTradeIdentityOverlay | None
) -> datetime | None:
    return _payload_datetime(trade.payload.get("window_end")) or (
        overlay.window_end if overlay is not None and overlay.status == "RECOVERED" else None
    )


def _jsonable(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value
