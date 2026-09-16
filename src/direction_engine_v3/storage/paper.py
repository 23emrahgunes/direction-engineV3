"""SQLite-backed append-only PAPER execution audit repository."""

import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
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
        for column, value in (
            ("asset", asset),
            ("horizon", horizon),
            ("strategy", strategy),
            ("side", side),
            ("status", status),
        ):
            if value is not None:
                require_text(column, value)
                clauses.append(f"{column}=?")
                params.append(value)
        if win_loss is not None:
            require_text("win_loss", win_loss)
            clauses.append("json_extract(payload_json, '$.win_loss')=?")
            params.append(win_loss)
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
        query += " ORDER BY observed_at DESC LIMIT ? OFFSET ?"
        params.extend((limit, offset))
        with sqlite3.connect(self._path) as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return tuple(_trade_from_row(row) for row in rows)

    def trade_by_id(self, trade_id: str) -> PaperTradeSnapshot | None:
        require_text("trade_id", trade_id)
        with sqlite3.connect(self._path) as connection:
            row = connection.execute(
                "SELECT trade_id,decision_id,strategy,asset,horizon,condition_id,side,status,"
                "label,payload_json,observed_at FROM paper_trade_snapshots WHERE trade_id=?",
                (trade_id,),
            ).fetchone()
        return None if row is None else _trade_from_row(row)

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

    def summary(self, *, initial_equity: Decimal = Decimal("1000")) -> dict[str, object]:
        require_decimal("initial_equity", initial_equity, minimum=Decimal("0"))
        trades = self.trades(limit=10_000)
        realized_pnl = sum(
            (Decimal(str(item.payload.get("realized_paper_pnl", "0"))) for item in trades),
            Decimal("0"),
        )
        total_fees = sum(
            (Decimal(str(item.payload.get("fee", "0"))) for item in trades),
            Decimal("0"),
        )
        settled = tuple(item for item in trades if item.status == "SETTLED")
        wins = sum(1 for item in settled if item.payload.get("win_loss") == "WIN")
        losses = sum(1 for item in settled if item.payload.get("win_loss") == "LOSS")
        net_edges = [
            Decimal(str(item.payload["net_edge"]))
            for item in trades
            if item.payload.get("net_edge") is not None
        ]
        open_positions = sum(1 for item in trades if item.status not in {"SETTLED", "REJECTED"})
        return {
            "label": "PAPER / SHADOW — NO REAL ORDER",
            "paper_initial_equity": str(initial_equity),
            "paper_current_equity": str(initial_equity + realized_pnl),
            "realized_pnl": str(realized_pnl),
            "unrealized_open_exposure": str(
                sum(
                    (
                        Decimal(str(item.payload.get("stake", "0")))
                        for item in trades
                        if item.status == "OPEN"
                    ),
                    Decimal("0"),
                )
            ),
            "open_positions": open_positions,
            "settled_trades": len(settled),
            "wins": wins,
            "losses": losses,
            "win_rate": str(Decimal(wins) / Decimal(len(settled))) if settled else "0",
            "total_fees": str(total_fees),
            "average_net_edge": str(sum(net_edges, Decimal("0")) / Decimal(len(net_edges)))
            if net_edges
            else "0",
            "maximum_drawdown": "0",
            "current_losing_streak": _current_losing_streak(trades),
        }

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
