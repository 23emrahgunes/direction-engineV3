"""Durable Price-to-Beat persistence for restart-safe shadow runtime."""

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from direction_engine_v3.domain import Asset, Horizon, OfficialReference
from direction_engine_v3.domain._validation import require_text, require_utc
from direction_engine_v3.market_data.canonical import PriceToBeatRecord


@dataclass(frozen=True, slots=True)
class PriceToBeatIdentity:
    asset: Asset
    horizon: Horizon
    market_id: str
    condition_id: str
    window_start: datetime
    official_source: str

    def __post_init__(self) -> None:
        if not isinstance(self.asset, Asset):
            raise TypeError("asset must be Asset")
        if not isinstance(self.horizon, Horizon):
            raise TypeError("horizon must be Horizon")
        require_text("market_id", self.market_id)
        require_text("condition_id", self.condition_id)
        require_utc("window_start", self.window_start)
        require_text("official_source", self.official_source)

    @property
    def persistence_id(self) -> str:
        return (
            f"ptb:{self.asset.value}:{self.horizon.value}:{self.condition_id}:"
            f"{int(self.window_start.timestamp())}:{self.official_source}"
        )


class SQLitePriceToBeatRepository:
    """Append-only-ish PTB owner; existing rows are never recomputed mid-window."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def initialize(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS price_to_beat (
                    persistence_id TEXT PRIMARY KEY,
                    asset TEXT NOT NULL,
                    horizon TEXT NOT NULL,
                    market_id TEXT NOT NULL,
                    condition_id TEXT NOT NULL,
                    window_start TEXT NOT NULL,
                    official_source TEXT NOT NULL,
                    reference_id TEXT NOT NULL,
                    value TEXT NOT NULL,
                    source_ts TEXT NOT NULL,
                    recv_ts TEXT NOT NULL,
                    effective_ts TEXT NOT NULL,
                    established_at TEXT NOT NULL
                )
                """
            )

    def save_once(
        self, identity: PriceToBeatIdentity, record: PriceToBeatRecord
    ) -> PriceToBeatRecord:
        if record.persistence_id != identity.persistence_id:
            raise ValueError("PTB persistence identity mismatch")
        if record.condition_id != identity.condition_id:
            raise ValueError("PTB condition identity mismatch")
        reference = record.reference
        with sqlite3.connect(self._path) as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO price_to_beat VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    identity.persistence_id,
                    identity.asset.value,
                    identity.horizon.value,
                    identity.market_id,
                    identity.condition_id,
                    identity.window_start.isoformat(),
                    identity.official_source,
                    reference.reference_id,
                    str(reference.value),
                    reference.source_ts.isoformat(),
                    reference.recv_ts.isoformat(),
                    reference.effective_ts.isoformat(),
                    record.established_at.isoformat(),
                ),
            )
        restored = self.get(identity)
        if restored is None:
            raise RuntimeError("PTB was not durably persisted")
        return restored

    def get(self, identity: PriceToBeatIdentity) -> PriceToBeatRecord | None:
        with sqlite3.connect(self._path) as connection:
            row = connection.execute(
                """
                SELECT reference_id,value,source_ts,recv_ts,effective_ts,established_at
                FROM price_to_beat WHERE persistence_id=?
                """,
                (identity.persistence_id,),
            ).fetchone()
        if row is None:
            return None
        reference = OfficialReference(
            reference_id=str(row[0]),
            market_id=identity.market_id,
            asset=identity.asset,
            value=Decimal(str(row[1])),
            source=identity.official_source,
            source_ts=datetime.fromisoformat(str(row[2])),
            recv_ts=datetime.fromisoformat(str(row[3])),
            effective_ts=datetime.fromisoformat(str(row[4])),
            is_price_to_beat=True,
        )
        return PriceToBeatRecord(
            condition_id=identity.condition_id,
            reference=reference,
            established_at=datetime.fromisoformat(str(row[5])),
            persistence_id=identity.persistence_id,
        )

    @property
    def path(self) -> Path:
        return self._path
