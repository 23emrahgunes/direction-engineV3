"""Official Polymarket settlement resolution for PAPER burn-in."""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from direction_engine_v3.adapters.polymarket import parse_gamma_market
from direction_engine_v3.adapters.polymarket.market_data import GAMMA_MARKETS_URL
from direction_engine_v3.domain import Asset, Horizon, OutcomeSide
from direction_engine_v3.domain._validation import require_text, require_utc
from direction_engine_v3.market_data import MarketDataSchemaError
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

    async def market_metadata(self, condition_id: str) -> Mapping[str, object] | None:
        require_text("condition_id", condition_id)
        payload = await self._transport.get_json(
            GAMMA_MARKETS_URL,
            params={"condition_ids": condition_id, "limit": "1"},
        )
        items = payload if isinstance(payload, list) else [payload]
        if not items or not isinstance(items[0], Mapping):
            return None
        return items[0]

    async def resolve(
        self,
        *,
        condition_id: str,
        asset: Asset,
        horizon: Horizon,
        expected_market_id: str | None = None,
    ) -> OfficialSettlementResult:
        require_text("condition_id", condition_id)
        item = await self.market_metadata(condition_id)
        if item is None:
            return _blocked(condition_id, asset, horizon, "MARKET_METADATA_NOT_FOUND", self._now())
        return parse_gamma_official_settlement(
            item,
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
        max_conditions_per_pass: int | None = None,
        max_trades_per_condition: int = 500,
    ) -> None:
        self._paper = paper_repository
        self._corpus = corpus_repository
        self._resolver = resolver
        self._clock = clock
        self._max_conditions = (
            max_trades_per_pass if max_conditions_per_pass is None else max_conditions_per_pass
        )
        self._max_trades_per_condition = max_trades_per_condition
        if self._max_conditions < 1:
            raise ValueError("max_conditions_per_pass must be positive")
        if self._max_trades_per_condition < 1:
            raise ValueError("max_trades_per_condition must be positive")

    async def run_once(self) -> dict[str, object]:
        now = self._clock.utc_now()
        checked = pending = completed = blocked = recovered = identity_blocked = 0
        errors: list[str] = []
        attempted_conditions: list[str] = []
        candidates: list[PaperTradeSnapshot] = []
        for item in self._paper.trades(
            strategy="DIRECTIONAL_EDGE", status="OPEN", limit=100_000
        ):
            window_end = _trade_window_end(item)
            if window_end is None or window_end <= now:
                candidates.append(item)
        grouped: dict[str, list[PaperTradeSnapshot]] = {}
        for item in candidates:
            grouped.setdefault(item.condition_id, []).append(item)
        due_conditions = self._paper.due_settlement_conditions(
            tuple(grouped.keys()), now=now
        )
        for condition_id in due_conditions:
            if len(attempted_conditions) >= self._max_conditions:
                break
            matching = tuple(grouped.get(condition_id, ()))[: self._max_trades_per_condition]
            if not matching:
                continue
            trade = matching[0]
            attempted_conditions.append(condition_id)
            checked += len(matching)
            identity = await self._effective_identity(trade, now)
            if identity.get("status") == "RECOVERED":
                recovered += 1
            else:
                identity_blocked += 1
                blocked += len(matching)
                reason = str(identity["reason"])
                errors.append(f"{condition_id}:{reason}")
                self._record_attempt(
                    condition_id=condition_id,
                    state=_attempt_state_for_reason(reason),
                    now=now,
                    reason=reason,
                    matching_count=len(matching),
                )
                continue
            raw_window_end = identity["window_end"]
            if not isinstance(raw_window_end, datetime) or raw_window_end > now:
                reason = "SETTLEMENT_PENDING:WINDOW_NOT_ENDED"
                pending += len(matching)
                self._record_attempt(
                    condition_id=condition_id,
                    state="PENDING",
                    now=now,
                    reason=reason,
                    matching_count=len(matching),
                )
                continue
            try:
                result = await self._resolver.resolve(
                    condition_id=condition_id,
                    asset=Asset(trade.asset),
                    horizon=Horizon(trade.horizon),
                    expected_market_id=str(identity["market_id"]),
                )
                if result.status is OfficialSettlementStatus.SETTLEMENT_PENDING:
                    pending += len(matching)
                    self._record_attempt(
                        condition_id=condition_id,
                        state="PENDING",
                        now=now,
                        reason=result.reason,
                        matching_count=len(matching),
                    )
                    continue
                if result.status not in {
                    OfficialSettlementStatus.SETTLED,
                    OfficialSettlementStatus.VOID,
                }:
                    blocked += len(matching)
                    errors.append(f"{condition_id}:{result.reason}")
                    self._record_attempt(
                        condition_id=condition_id,
                        state=_attempt_state_for_reason(result.reason),
                        now=now,
                        reason=result.reason,
                        matching_count=len(matching),
                    )
                    continue
                for item in matching:
                    self._settle_trade(item, result, now)
                    completed += 1
                self._label_corpus(result, now)
                self._record_attempt(
                    condition_id=condition_id,
                    state="SETTLED",
                    now=now,
                    reason=result.reason,
                    matching_count=len(matching),
                    successful_at=now,
                )
            except Exception as exc:
                blocked += len(matching)
                reason = f"SETTLEMENT_BLOCKED:{type(exc).__name__}"
                errors.append(f"{condition_id}:{type(exc).__name__}")
                self._record_attempt(
                    condition_id=condition_id,
                    state="BLOCKED_RETRYABLE",
                    now=now,
                    reason=reason,
                    error=_safe_error_text(exc),
                    matching_count=len(matching),
                )
        queue_summary = self._paper.settlement_queue_summary(
            condition_ids=tuple(grouped.keys()),
            now=now,
        )
        return {
            "settlement_checked": checked,
            "settlement_pending": pending,
            "settlement_completed": completed,
            "settlement_blocked": blocked,
            "identity_recovered": recovered,
            "identity_blocked": identity_blocked,
            "condition_cursor_processed": len(attempted_conditions),
            "conditions_attempted": tuple(attempted_conditions),
            "last_attempted_condition": attempted_conditions[-1]
            if attempted_conditions
            else None,
            "last_settlement_check_at": now.isoformat(),
            "last_settlement_error": errors[-1] if errors else None,
            **queue_summary,
        }

    def _record_attempt(
        self,
        *,
        condition_id: str,
        state: str,
        now: datetime,
        reason: str,
        matching_count: int,
        error: str | None = None,
        successful_at: datetime | None = None,
    ) -> None:
        self._paper.save_settlement_condition_attempt(
            condition_id=condition_id,
            state=state,
            attempted_at=now,
            next_attempt_at=_next_attempt_at(state, now),
            reason=reason,
            error=error,
            successful_at=successful_at,
            payload={
                "condition_id": condition_id,
                "state": state,
                "reason": reason,
                "matching_trade_count": matching_count,
                "real_order_submission": False,
            },
        )

    async def _effective_identity(
        self, trade: PaperTradeSnapshot, now: datetime
    ) -> Mapping[str, object]:
        overlay = self._paper.identity_overlay(trade.condition_id)
        if overlay is not None and overlay.status == "RECOVERED":
            return {
                "status": "RECOVERED",
                "market_id": overlay.market_id,
                "window_start": overlay.window_start,
                "window_end": overlay.window_end,
                "source": overlay.source,
            }
        if overlay is not None and overlay.status == "BLOCKED":
            return {
                "status": "BLOCKED",
                "reason": overlay.blocker_reason
                or "SETTLEMENT_BLOCKED:LEGACY_MARKET_IDENTITY_INCOMPLETE",
            }
        window_end = _trade_window_end(trade)
        market_id = str(trade.payload.get("market_id", ""))
        window_start = _trade_window_start(trade)
        if market_id and window_start is not None and window_end is not None:
            return {
                "status": "RECOVERED",
                "market_id": market_id,
                "window_start": window_start,
                "window_end": window_end,
                "source": "PAPER_TRADE_SNAPSHOT",
            }
        recovered = self._recover_identity_from_corpus(trade, now)
        if recovered is not None:
            return recovered
        recovered = await self._recover_identity_from_official_metadata(trade, now)
        if recovered is not None:
            return recovered
        return {
            "status": "BLOCKED",
            "reason": "SETTLEMENT_BLOCKED:LEGACY_MARKET_IDENTITY_INCOMPLETE",
        }

    def _recover_identity_from_corpus(
        self, trade: PaperTradeSnapshot, now: datetime
    ) -> Mapping[str, object] | None:
        if self._corpus is None:
            return None
        for record in self._corpus.records_for_condition(trade.condition_id):
            payload = record.payload
            try:
                market_id = _payload_text(payload.get("market_id"))
                window_start = _datetime_from_payload(payload.get("window_start"))
                window_end = _datetime_from_payload(payload.get("window_end"))
                _validate_identity(
                    condition_id=trade.condition_id,
                    asset=Asset(trade.asset),
                    horizon=Horizon(trade.horizon),
                    market_id=market_id,
                    window_start=window_start,
                    window_end=window_end,
                )
            except (ValueError, TypeError):
                continue
            return self._save_recovered_identity(
                trade,
                market_id=market_id,
                window_start=window_start,
                window_end=window_end,
                outcome_tokens={},
                source_kind="LOCAL_CORPUS",
                source=f"directional_corpus:{record.record_id}",
                retrieved_at=record.observed_at,
                verified_at=now,
                evidence={"record_id": record.record_id, "market_id": market_id},
            )
        return None

    async def _recover_identity_from_official_metadata(
        self, trade: PaperTradeSnapshot, now: datetime
    ) -> Mapping[str, object] | None:
        raw = await self._resolver.market_metadata(trade.condition_id)
        if raw is None:
            return None
        try:
            market = parse_gamma_market(
                raw,
                asset=Asset(trade.asset),
                horizon=Horizon(trade.horizon),
            )
        except (MarketDataSchemaError, ValueError, TypeError) as exc:
            self._paper.save_identity_overlay_once(
                condition_id=trade.condition_id,
                status="BLOCKED",
                asset=trade.asset,
                horizon=trade.horizon,
                source_kind="POLYMARKET_OFFICIAL_METADATA",
                source="GAMMA_MARKETS_BY_CONDITION",
                retrieved_at=now,
                verified_at=now,
                evidence_hash=_evidence_hash(
                    {"condition_id": trade.condition_id, "reason": type(exc).__name__}
                ),
                blocker_reason=f"SETTLEMENT_BLOCKED:{type(exc).__name__}",
                payload={"reason": _safe_error_text(exc)},
            )
            return None
        if market.condition_id != trade.condition_id:
            return None
        outcome_tokens = {token.outcome.value: token.token_id for token in market.tokens}
        return self._save_recovered_identity(
            trade,
            market_id=market.market_id,
            window_start=market.window_start,
            window_end=market.window_end,
            outcome_tokens=outcome_tokens,
            source_kind="POLYMARKET_OFFICIAL_METADATA",
            source="GAMMA_MARKETS_BY_CONDITION",
            retrieved_at=now,
            verified_at=now,
            evidence=_sanitized_evidence(raw),
        )

    def _save_recovered_identity(
        self,
        trade: PaperTradeSnapshot,
        *,
        market_id: str,
        window_start: datetime,
        window_end: datetime,
        outcome_tokens: Mapping[str, object],
        source_kind: str,
        source: str,
        retrieved_at: datetime,
        verified_at: datetime,
        evidence: Mapping[str, object],
    ) -> Mapping[str, object]:
        overlay = self._paper.save_identity_overlay_once(
            condition_id=trade.condition_id,
            status="RECOVERED",
            market_id=market_id,
            asset=trade.asset,
            horizon=trade.horizon,
            window_start=window_start,
            window_end=window_end,
            outcome_tokens=outcome_tokens,
            source_kind=source_kind,
            source=source,
            retrieved_at=retrieved_at,
            verified_at=verified_at,
            evidence_hash=_evidence_hash(evidence),
            payload={"evidence": dict(evidence), "real_order_submission": False},
        )
        return {
            "status": "RECOVERED",
            "market_id": overlay.market_id,
            "window_start": overlay.window_start,
            "window_end": overlay.window_end,
            "source": overlay.source,
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
    resolved_at = _resolved_at(raw)
    if resolved_at is None:
        return _blocked(
            condition_id,
            asset,
            horizon,
            "OFFICIAL_RESOLVED_AT_MISSING",
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
        resolved_at,
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
    for key in ("resolvedAt", "closedTime"):
        value = raw.get(key)
        if isinstance(value, str) and value:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.astimezone(UTC)
    return None


def _attempt_state_for_reason(reason: str) -> str:
    if reason.startswith("SETTLEMENT_PENDING") or reason == "MARKET_NOT_FINAL":
        return "PENDING"
    permanent_markers = (
        "LEGACY_MARKET_IDENTITY_INCOMPLETE",
        "CONDITION_ID_MISMATCH",
        "MARKET_ID_MISMATCH",
        "official",
        "conflicting",
    )
    if any(marker in reason for marker in permanent_markers):
        return "BLOCKED_PERMANENT"
    if reason in {"OFFICIAL_WINNER_MISSING", "OFFICIAL_RESOLVED_AT_MISSING"}:
        return "BLOCKED_RETRYABLE"
    return "BLOCKED_RETRYABLE"


def _next_attempt_at(state: str, now: datetime) -> datetime | None:
    if state == "PENDING":
        return now + timedelta(minutes=10)
    if state == "BLOCKED_RETRYABLE":
        return now + timedelta(minutes=5)
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


def _trade_window_start(trade: PaperTradeSnapshot) -> datetime | None:
    value = trade.payload.get("window_start")
    if not isinstance(value, str) or not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _trade_window_end(trade: PaperTradeSnapshot) -> datetime | None:
    value = trade.payload.get("window_end")
    if not isinstance(value, str) or not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _payload_text(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("payload value was not text")
    return value


def _datetime_from_payload(value: object) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("payload value was not datetime text")
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _validate_identity(
    *,
    condition_id: str,
    asset: Asset,
    horizon: Horizon,
    market_id: str,
    window_start: datetime,
    window_end: datetime,
) -> None:
    require_text("condition_id", condition_id)
    require_text("market_id", market_id)
    require_utc("window_start", window_start)
    require_utc("window_end", window_end)
    if window_end <= window_start:
        raise ValueError("window_end must be after window_start")
    expected = {
        Horizon.FIVE_MINUTES: 300,
        Horizon.FIFTEEN_MINUTES: 900,
        Horizon.ONE_HOUR: 3600,
    }[horizon]
    if int((window_end - window_start).total_seconds()) != expected:
        raise ValueError("window duration does not match horizon")
    if int(window_start.timestamp()) % expected != 0:
        raise ValueError("window start is not canonical")
    if asset not in {Asset.BTC, Asset.ETH, Asset.SOL, Asset.XRP}:
        raise ValueError("unsupported settlement asset")


def _safe_error_text(exc: Exception) -> str:
    return str(exc).replace("\r", " ").replace("\n", " ").strip() or type(exc).__name__
