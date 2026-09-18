"""Official Polymarket settlement resolution for PAPER burn-in."""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from direction_engine_v3.adapters.polymarket.market_data import GAMMA_MARKETS_URL
from direction_engine_v3.domain import Asset, Horizon, OutcomeSide
from direction_engine_v3.domain._validation import require_text, require_utc
from direction_engine_v3.storage import SQLiteDirectionalCorpusRepository, SQLitePaperRepository
from direction_engine_v3.storage.paper import PaperTradeSnapshot


class _Clock(Protocol):
    def utc_now(self) -> datetime:
        ...


class _Transport(Protocol):
    async def get_json(
        self, url: str, *, params: Mapping[str, str] | None = None
    ) -> object:
        ...


class OfficialSettlementStatus(StrEnum):
    SETTLED = "SETTLED"
    SETTLEMENT_PENDING = "SETTLEMENT_PENDING"
    SETTLEMENT_BLOCKED = "SETTLEMENT_BLOCKED"
    VOID = "VOID"


@dataclass(frozen=True, slots=True)
class OfficialSettlementResult:
    status: OfficialSettlementStatus
    market_id: str
    condition_id: str
    asset: Asset
    horizon: Horizon
    winning_side: OutcomeSide | None
    settlement_source_kind: str
    source: str
    resolved_at: datetime | None
    observed_at: datetime
    evidence_hash: str
    reason: str
    evidence: Mapping[str, object]


class GammaOfficialSettlementResolver:
    """Read-only resolver that uses official Gamma metadata only."""

    def __init__(self, transport: _Transport, clock: _Clock) -> None:
        self._transport = transport
        self._clock = clock

    async def resolve(
        self,
        *,
        condition_id: str,
        asset: Asset,
        horizon: Horizon,
        expected_market_id: str | None = None,
    ) -> OfficialSettlementResult:
        require_text("condition_id", condition_id)
        payload = await self._transport.get_json(
            GAMMA_MARKETS_URL,
            params={"condition_ids": condition_id, "limit": "1"},
        )
        items = payload if isinstance(payload, list) else [payload]
        if not items:
            return _blocked(condition_id, asset, horizon, "MARKET_METADATA_NOT_FOUND", self._now())
        return parse_gamma_official_settlement(
            items[0],
            asset=asset,
            horizon=horizon,
            condition_id=condition_id,
            expected_market_id=expected_market_id,
            observed_at=self._now(),
        )

    def _now(self) -> datetime:
        return self._clock.utc_now()


class PaperSettlementService:
    """Bounded idempotent settlement pass for Directional PAPER trades."""

    def __init__(
        self,
        *,
        paper_repository: SQLitePaperRepository,
        corpus_repository: SQLiteDirectionalCorpusRepository | None,
        resolver: GammaOfficialSettlementResolver,
        clock: _Clock,
        max_trades_per_pass: int = 25,
    ) -> None:
        self._paper = paper_repository
        self._corpus = corpus_repository
        self._resolver = resolver
        self._clock = clock
        self._max = max_trades_per_pass

    async def run_once(self) -> dict[str, object]:
        now = self._clock.utc_now()
        checked = pending = completed = blocked = 0
        errors: list[str] = []
        seen_conditions: set[str] = set()
        candidates = []
        for item in self._paper.trades(
            strategy="DIRECTIONAL_EDGE", status="OPEN", limit=100_000
        ):
            window_end = _trade_window_end(item)
            if window_end is None or window_end <= now:
                candidates.append(item)
        for trade in candidates:
            if checked >= self._max:
                break
            if trade.condition_id in seen_conditions:
                continue
            seen_conditions.add(trade.condition_id)
            matching = tuple(item for item in candidates if item.condition_id == trade.condition_id)
            checked += len(matching)
            if _trade_window_end(trade) is None:
                blocked += len(matching)
                errors.append(
                    f"{trade.condition_id}:SETTLEMENT_BLOCKED:"
                    "LEGACY_MARKET_IDENTITY_INCOMPLETE"
                )
                continue
            try:
                result = await self._resolver.resolve(
                    condition_id=trade.condition_id,
                    asset=Asset(trade.asset),
                    horizon=Horizon(trade.horizon),
                    expected_market_id=str(trade.payload.get("market_id", "")) or None,
                )
                if result.status is OfficialSettlementStatus.SETTLEMENT_PENDING:
                    pending += len(matching)
                    continue
                if result.status not in {
                    OfficialSettlementStatus.SETTLED,
                    OfficialSettlementStatus.VOID,
                }:
                    blocked += len(matching)
                    errors.append(f"{trade.condition_id}:{result.reason}")
                    continue
                for item in matching:
                    self._settle_trade(item, result, now)
                    completed += 1
                self._label_corpus(result, now)
            except Exception as exc:
                blocked += len(matching)
                errors.append(f"{trade.condition_id}:{type(exc).__name__}")
        return {
            "settlement_checked": checked,
            "settlement_pending": pending,
            "settlement_completed": completed,
            "settlement_blocked": blocked,
            "last_settlement_check_at": now.isoformat(),
            "last_settlement_error": errors[-1] if errors else None,
        }

    def _settle_trade(
        self, trade: PaperTradeSnapshot, result: OfficialSettlementResult, settled_at: datetime
    ) -> None:
        if result.winning_side is None or result.resolved_at is None:
            raise RuntimeError("settled result requires winning side and resolved_at")
        shares = _decimal_from_payload(trade, "shares")
        cost_basis = _cost_basis(trade)
        win_loss = "WIN" if trade.side == result.winning_side.value else "LOSS"
        payout = shares if win_loss == "WIN" else Decimal("0")
        pnl = payout - cost_basis
        payload = {
            "trade_id": trade.trade_id,
            "condition_id": trade.condition_id,
            "official_winning_side": result.winning_side.value,
            "selected_side": trade.side,
            "win_loss": win_loss,
            "evidence_hash": result.evidence_hash,
            "real_order_submission": False,
        }
        self._paper.save_settlement_once(
            settlement_id=f"settlement:{trade.trade_id}:{result.evidence_hash}",
            trade_id=trade.trade_id,
            condition_id=trade.condition_id,
            official_winning_side=result.winning_side.value,
            selected_side=trade.side,
            settlement_source_kind=result.settlement_source_kind,
            settlement_source=result.source,
            official_resolved_at=result.resolved_at,
            settled_at=settled_at,
            filled_shares=shares,
            cost_basis_usdc=cost_basis,
            payout_usdc=payout,
            realized_paper_pnl=pnl,
            win_loss=win_loss,
            evidence_hash=result.evidence_hash,
            payload=payload,
        )

    def _label_corpus(self, result: OfficialSettlementResult, attached_at: datetime) -> None:
        if self._corpus is None or result.winning_side is None or result.resolved_at is None:
            return
        self._corpus.attach_verified_outcome_to_condition_once(
            condition_id=result.condition_id,
            official_resolved_at=result.resolved_at,
            attached_at=attached_at,
            outcome={
                "settlement_source_kind": "OFFICIAL",
                "source": result.source,
                "condition_id": result.condition_id,
                "market_id": result.market_id,
                "outcome_up": result.winning_side is OutcomeSide.UP,
                "winning_side": result.winning_side.value,
                "official_resolved_at": result.resolved_at.isoformat(),
                "attached_at": attached_at.isoformat(),
                "evidence_hash": result.evidence_hash,
            },
        )


def parse_gamma_official_settlement(
    raw: object,
    *,
    asset: Asset,
    horizon: Horizon,
    condition_id: str,
    expected_market_id: str | None,
    observed_at: datetime,
) -> OfficialSettlementResult:
    require_text("condition_id", condition_id)
    require_utc("observed_at", observed_at)
    if not isinstance(raw, dict):
        return _blocked(condition_id, asset, horizon, "GAMMA_MARKET_NOT_OBJECT", observed_at)
    market_id = str(raw.get("id", ""))
    if expected_market_id and market_id != expected_market_id:
        return _blocked(condition_id, asset, horizon, "MARKET_ID_MISMATCH", observed_at)
    if str(raw.get("conditionId", "")) != condition_id:
        return _blocked(condition_id, asset, horizon, "CONDITION_ID_MISMATCH", observed_at)
    evidence = _sanitized_evidence(raw)
    evidence_hash = _evidence_hash(evidence)
    if any(bool(raw.get(key)) for key in ("voided", "void", "cancelled", "canceled")):
        return OfficialSettlementResult(
            OfficialSettlementStatus.VOID,
            market_id,
            condition_id,
            asset,
            horizon,
            None,
            "OFFICIAL",
            "POLYMARKET_OFFICIAL_METADATA",
            _resolved_at(raw),
            observed_at,
            evidence_hash,
            "OFFICIAL_VOID",
            evidence,
        )
    closed = bool(raw.get("closed"))
    if not closed:
        return OfficialSettlementResult(
            OfficialSettlementStatus.SETTLEMENT_PENDING,
            market_id,
            condition_id,
            asset,
            horizon,
            None,
            "OFFICIAL",
            "POLYMARKET_OFFICIAL_METADATA",
            None,
            observed_at,
            evidence_hash,
            "MARKET_NOT_FINAL",
            evidence,
        )
    winner = _winner(raw)
    if winner is None:
        return _blocked(
            condition_id,
            asset,
            horizon,
            "OFFICIAL_WINNER_MISSING",
            observed_at,
            evidence,
        )
    return OfficialSettlementResult(
        OfficialSettlementStatus.SETTLED,
        market_id,
        condition_id,
        asset,
        horizon,
        winner,
        "OFFICIAL",
        "POLYMARKET_OFFICIAL_METADATA",
        _resolved_at(raw) or observed_at,
        observed_at,
        evidence_hash,
        "OFFICIAL_FINAL",
        evidence,
    )


def _winner(raw: Mapping[str, object]) -> OutcomeSide | None:
    for key in ("winningOutcome", "winning_outcome", "resolutionOutcome", "winner"):
        value = raw.get(key)
        if isinstance(value, str):
            normalized = value.strip().upper()
            if normalized in {"UP", "DOWN"}:
                return OutcomeSide(normalized)
    token = next(
        (
            str(raw[key])
            for key in ("winningClobTokenId", "winningTokenId", "winning_asset_id")
            if raw.get(key)
        ),
        None,
    )
    if token is None:
        return None
    outcomes = _json_array(raw.get("outcomes"))
    token_ids = _json_array(raw.get("clobTokenIds"))
    for outcome, token_id in zip(outcomes, token_ids, strict=False):
        if token_id == token and isinstance(outcome, str):
            normalized = outcome.strip().upper()
            if normalized in {"UP", "DOWN"}:
                return OutcomeSide(normalized)
    return None


def _json_array(value: object) -> tuple[object, ...]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return ()
    return tuple(value) if isinstance(value, list) else ()


def _sanitized_evidence(raw: Mapping[str, object]) -> dict[str, object]:
    keys = (
        "id",
        "conditionId",
        "outcomes",
        "clobTokenIds",
        "closed",
        "active",
        "archived",
        "winningOutcome",
        "winning_outcome",
        "resolutionOutcome",
        "winner",
        "winningClobTokenId",
        "winningTokenId",
        "winning_asset_id",
        "resolvedAt",
        "closedTime",
        "updatedAt",
    )
    return {key: raw[key] for key in keys if key in raw}


def _evidence_hash(evidence: Mapping[str, object]) -> str:
    encoded = json.dumps(evidence, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _resolved_at(raw: Mapping[str, object]) -> datetime | None:
    for key in ("resolvedAt", "closedTime", "updatedAt"):
        value = raw.get(key)
        if isinstance(value, str) and value:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.astimezone(UTC)
    return None


def _blocked(
    condition_id: str,
    asset: Asset,
    horizon: Horizon,
    reason: str,
    observed_at: datetime,
    evidence: Mapping[str, object] | None = None,
) -> OfficialSettlementResult:
    return OfficialSettlementResult(
        OfficialSettlementStatus.SETTLEMENT_BLOCKED,
        "",
        condition_id,
        asset,
        horizon,
        None,
        "OFFICIAL",
        "POLYMARKET_OFFICIAL_METADATA",
        None,
        observed_at,
        _evidence_hash(evidence or {"condition_id": condition_id, "reason": reason}),
        reason,
        evidence or {},
    )


def _decimal_from_payload(trade: PaperTradeSnapshot, key: str) -> Decimal:
    value = trade.payload.get(key)
    if value is None:
        raise RuntimeError(f"SETTLEMENT_BLOCKED:{key.upper()}_UNAVAILABLE")
    return Decimal(str(value))


def _cost_basis(trade: PaperTradeSnapshot) -> Decimal:
    value = trade.payload.get("cost_basis_usdc", trade.payload.get("stake"))
    if value is None:
        raise RuntimeError("SETTLEMENT_BLOCKED:COST_BASIS_UNAVAILABLE")
    return Decimal(str(value))


def _trade_window_end(trade: PaperTradeSnapshot) -> datetime | None:
    value = trade.payload.get("window_end")
    if not isinstance(value, str) or not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
