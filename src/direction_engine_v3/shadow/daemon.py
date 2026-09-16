"""Persistent SHADOW/PAPER runtime orchestration over existing V3 owners."""

import argparse
import asyncio
import json
import os
import signal
import subprocess
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from direction_engine_v3.adapters.binance import parse_aggregate_trade, parse_depth_top
from direction_engine_v3.adapters.polymarket import (
    CLOB_BOOK_URL,
    CLOB_MARKETS_URL,
    parse_clob_book,
    parse_fee_schedule,
    parse_gamma_market_discovery,
)
from direction_engine_v3.adapters.public_transport import PublicTransport
from direction_engine_v3.config import APP_MODE, LIVE_AUTO_ARM, LIVE_TRADING_ENABLED
from direction_engine_v3.domain import (
    Asset,
    DecisionAction,
    FeatureVector,
    Horizon,
    Market,
    OfficialReference,
    OrderSide,
    OutcomeSide,
    ProxyReference,
    RiskDecision,
    StrategyCandidate,
    StrategyKind,
    TradingMode,
)
from direction_engine_v3.execution import (
    PaperFillEvidence,
    PaperGateway,
    build_structural_buy_merge_paper_plan,
)
from direction_engine_v3.features import ExternalTemporalState, build_directional_features
from direction_engine_v3.market_data import (
    SUPPORTED_MARKET_BUCKETS,
    CryptoTopOfBook,
    CryptoTrade,
    FeeSchedule,
    MarketBucket,
    MarketDiscovery,
    PolymarketBook,
    PriceToBeatRecord,
    ReferenceFreshnessPolicy,
    SQLitePriceToBeatRepository,
    SystemClock,
    epoch_slug,
    window_containing,
)
from direction_engine_v3.market_data.official_runtime import (
    BinanceHourlyOfficialCollector,
    ChainlinkTwapCollector,
    OfficialPriceToBeatService,
    PriceToBeatResolution,
)
from direction_engine_v3.models import ShadowBucketModelStatus, ShadowModelState, empty_registry
from direction_engine_v3.pricing import (
    DepthSimulation,
    LiquidityRole,
    PricingPolicy,
    simulate_depth,
)
from direction_engine_v3.risk import (
    LiquidityEvidence,
    PortfolioState,
    RiskPolicy,
    assess_candidate,
)
from direction_engine_v3.router import (
    RouterPolicy,
    RouterSnapshot,
    RoutingOpportunity,
    route_opportunities,
)
from direction_engine_v3.shadow.evidence import (
    AWSIdentityEvidence,
    EvidenceFingerprint,
    EvidenceWindow,
)
from direction_engine_v3.shadow.reporting import build_shadow_summary, write_reports
from direction_engine_v3.shadow.storage import SQLiteShadowRepository
from direction_engine_v3.storage import SQLiteDirectionalCorpusRepository, SQLitePaperRepository
from direction_engine_v3.strategies.directional import DirectionalPolicy, assess_directional_edge
from direction_engine_v3.strategies.structural_arb import (
    StructuralAction,
    StructuralOpportunity,
    StructuralPolicy,
    scan_complete_set,
)

_EVENT_URL = "https://gamma-api.polymarket.com/events/slug/{slug}"
_BINANCE_DEPTH_URL = "https://api.binance.com/api/v3/depth"
_BINANCE_TRADES_URL = "https://api.binance.com/api/v3/aggTrades"
_PAPER_LABEL = "PAPER / SHADOW — NO REAL ORDER"
_ASSET_NAME = {
    Asset.BTC: "bitcoin",
    Asset.ETH: "ethereum",
    Asset.SOL: "solana",
    Asset.XRP: "xrp",
}
_MONTH_NAME = (
    "",
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
)


class ShadowDataClient(Protocol):
    async def collect_bucket(self, bucket: MarketBucket, *, now: datetime) -> "ShadowMarketState":
        """Return one current, real-data market state for a supported bucket."""


@dataclass(frozen=True, slots=True)
class ShadowMarketState:
    bucket: MarketBucket
    discovery: MarketDiscovery | None
    up_book: PolymarketBook | None
    down_book: PolymarketBook | None
    fee_schedule: FeeSchedule | None
    proxy_reference: ProxyReference | None
    official_reference: OfficialReference | None
    observed_at: datetime
    unavailable_reason: str | None = None
    price_to_beat: PriceToBeatRecord | None = None
    directional_features: FeatureVector | None = None
    ptb_status: str = "PTB_UNAVAILABLE"
    ptb_reason: str = "OFFICIAL_PTB_UNAVAILABLE"
    feature_status: str = "FEATURES_UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class ShadowCycleResult:
    cycle_id: str
    started_at: datetime
    markets_discovered: int
    proxy_observations: int
    official_observations: int
    book_observations: int
    strategy_evaluations: int
    abstain_records: int
    paper_trades: int

    def as_dict(self) -> dict[str, object]:
        return {
            "cycle_id": self.cycle_id,
            "started_at": self.started_at.isoformat(),
            "markets_discovered": self.markets_discovered,
            "proxy_observations": self.proxy_observations,
            "official_observations": self.official_observations,
            "book_observations": self.book_observations,
            "strategy_evaluations": self.strategy_evaluations,
            "abstain_records": self.abstain_records,
            "paper_trades": self.paper_trades,
            "label": _PAPER_LABEL,
        }


class PublicShadowDataClient:
    """Credential-free public polling client used by the persistent VPS daemon."""

    def __init__(
        self,
        transport: PublicTransport,
        clock: SystemClock,
        *,
        official_ptb: OfficialPriceToBeatService | None = None,
        feature_state: ExternalTemporalState | None = None,
    ) -> None:
        self._transport = transport
        self._clock = clock
        self._official_ptb = official_ptb
        self._feature_state = feature_state

    async def collect_bucket(self, bucket: MarketBucket, *, now: datetime) -> ShadowMarketState:
        window = window_containing(bucket, now)
        slug = _slug(bucket, window.start)
        try:
            raw_event = _object(
                await self._transport.get_json(_EVENT_URL.format(slug=slug)),
                "Gamma event",
            )
            markets = _sequence(raw_event.get("markets"), "Gamma event markets")
            if len(markets) != 1:
                raise RuntimeError("canonical Gamma event must contain exactly one market")
            recv_ts = self._clock.utc_now()
            discovery = parse_gamma_market_discovery(
                markets[0],
                event_id=_text(raw_event.get("id"), "Gamma event ID"),
                asset=bucket.asset,
                horizon=bucket.horizon,
                recv_ts=recv_ts,
                normalized_ts=self._clock.utc_now(),
                recv_monotonic_ns=self._clock.monotonic_ns(),
            )
            token_ids = {token.outcome: token.token_id for token in discovery.market.tokens}
            up_book, down_book, raw_fee, proxy_observation = await asyncio.gather(
                self._fetch_book(token_ids[OutcomeSide.UP]),
                self._fetch_book(token_ids[OutcomeSide.DOWN]),
                self._transport.get_json(f"{CLOB_MARKETS_URL}/{discovery.market.condition_id}"),
                self._fetch_proxy_observation(discovery.market),
            )
            proxy, external_book, external_trade = proxy_observation
            if self._feature_state is not None:
                self._feature_state.add_reference(proxy)
                self._feature_state.add_book(external_book)
                if external_trade is not None:
                    self._feature_state.add_trade(external_trade)
            fee_recv = self._clock.utc_now()
            fee = parse_fee_schedule(
                raw_fee,
                condition_id=discovery.market.condition_id,
                recv_ts=fee_recv,
                normalized_ts=self._clock.utc_now(),
                recv_monotonic_ns=self._clock.monotonic_ns(),
            )
            observed_at = self._clock.utc_now()
            ptb_resolution = await self._resolve_ptb(discovery, observed_at=observed_at)
            features, feature_status = self._build_directional_features(
                discovery,
                proxy,
                ptb_resolution.price_to_beat,
                observed_at=observed_at,
            )
            return ShadowMarketState(
                bucket=bucket,
                discovery=discovery,
                up_book=up_book,
                down_book=down_book,
                fee_schedule=fee,
                proxy_reference=proxy,
                official_reference=ptb_resolution.official_reference,
                observed_at=observed_at,
                price_to_beat=ptb_resolution.price_to_beat,
                directional_features=features,
                ptb_status=ptb_resolution.ptb_status,
                ptb_reason=ptb_resolution.reason,
                feature_status=feature_status,
            )
        except Exception as exc:
            return ShadowMarketState(
                bucket=bucket,
                discovery=None,
                up_book=None,
                down_book=None,
                fee_schedule=None,
                proxy_reference=None,
                official_reference=None,
                observed_at=self._clock.utc_now(),
                unavailable_reason=type(exc).__name__,
                ptb_status="PTB_UNAVAILABLE",
                ptb_reason=type(exc).__name__,
                feature_status="FEATURES_UNAVAILABLE",
            )

    async def _fetch_book(self, token_id: str) -> PolymarketBook:
        raw = await self._transport.get_json(CLOB_BOOK_URL, params={"token_id": token_id})
        received = self._clock.utc_now()
        return parse_clob_book(
            raw,
            recv_ts=received,
            normalized_ts=self._clock.utc_now(),
            recv_monotonic_ns=self._clock.monotonic_ns(),
        )

    async def _fetch_proxy_observation(
        self, market: Market
    ) -> tuple[ProxyReference, CryptoTopOfBook, CryptoTrade | None]:
        raw = await self._transport.get_json(
            _BINANCE_DEPTH_URL,
            params={"symbol": f"{market.asset.value}USDT", "limit": "5"},
        )
        payload = _object(raw, "Binance depth")
        now = self._clock.utc_now()
        depth_event = {
            "e": "depthUpdate",
            "E": int(now.timestamp() * 1000),
            "s": f"{market.asset.value}USDT",
            "U": _integer(payload.get("lastUpdateId"), "lastUpdateId"),
            "u": _integer(payload.get("lastUpdateId"), "lastUpdateId"),
            "b": payload["bids"],
            "a": payload["asks"],
        }
        top, _first_update = parse_depth_top(
            depth_event,
            asset=market.asset,
            recv_ts=now,
            normalized_ts=self._clock.utc_now(),
            recv_monotonic_ns=self._clock.monotonic_ns(),
        )
        mid = (top.bid_price + top.ask_price) / Decimal("2")
        proxy = ProxyReference(
            f"proxy:{market.condition_id}:{int(now.timestamp())}",
            market.market_id,
            market.asset,
            mid,
            "BINANCE_PUBLIC_DEPTH",
            top.lineage.source_ts or now,
            top.lineage.recv_ts,
            top.lineage.normalized_ts,
        )
        return proxy, top, await self._fetch_latest_trade(market)

    async def _fetch_latest_trade(self, market: Market) -> CryptoTrade | None:
        raw = await self._transport.get_json(
            _BINANCE_TRADES_URL,
            params={"symbol": f"{market.asset.value}USDT", "limit": "1"},
        )
        rows = _sequence(raw, "Binance aggregate trades")
        if not rows:
            return None
        row = _object(rows[-1], "Binance aggregate trade")
        return parse_aggregate_trade(
            row,
            asset=market.asset,
            recv_ts=self._clock.utc_now(),
            normalized_ts=self._clock.utc_now(),
            recv_monotonic_ns=self._clock.monotonic_ns(),
        )

    async def _resolve_ptb(
        self,
        discovery: MarketDiscovery,
        *,
        observed_at: datetime,
    ) -> PriceToBeatResolution:
        if self._official_ptb is None:
            return PriceToBeatResolution(None, None, "PTB_UNAVAILABLE", "OFFICIAL_SERVICE_ABSENT")
        return await self._official_ptb.resolve(discovery, observed_at=observed_at)

    def _build_directional_features(
        self,
        discovery: MarketDiscovery,
        proxy: ProxyReference,
        price_to_beat: PriceToBeatRecord | None,
        *,
        observed_at: datetime,
    ) -> tuple[FeatureVector | None, str]:
        if price_to_beat is None:
            return None, "OFFICIAL_PTB_UNAVAILABLE"
        if self._feature_state is None:
            return None, "FEATURE_STATE_UNAVAILABLE"
        result = self._feature_state.build_snapshot(
            asset=discovery.market.asset,
            current_reference=proxy,
            observed_at=observed_at,
        )
        if result.snapshot is None:
            return None, result.reason
        return (
            build_directional_features(
                discovery.market,
                price_to_beat,
                result.snapshot,
                generated_at=observed_at,
                feature_set_version="v3.15.3-directional-official-ptb",
            ),
            result.reason,
        )


class ShadowDaemon:
    """Persistent PAPER/SHADOW orchestrator; strategy modules remain side-effect free."""

    def __init__(
        self,
        *,
        data_client: ShadowDataClient,
        paper_repository: SQLitePaperRepository,
        shadow_repository: SQLiteShadowRepository,
        evidence_window: EvidenceWindow,
        report_dir: Path,
        directional_corpus_repository: SQLiteDirectionalCorpusRepository | None = None,
        clock: SystemClock | None = None,
        poll_seconds: float = 30.0,
    ) -> None:
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be positive")
        self._data_client = data_client
        self._paper_repository = paper_repository
        self._shadow_repository = shadow_repository
        self._evidence_window = evidence_window
        self._report_dir = report_dir
        self._directional_corpus_repository = directional_corpus_repository
        self._clock = clock or SystemClock()
        self._poll_seconds = poll_seconds
        self._registry = empty_registry()

    async def run_once(self) -> ShadowCycleResult:
        started_at = self._clock.utc_now()
        cycle_id = f"shadow-cycle:{int(started_at.timestamp() * 1000)}"
        states = await asyncio.gather(
            *(
                self._data_client.collect_bucket(bucket, now=started_at)
                for bucket in SUPPORTED_MARKET_BUCKETS
            )
        )
        result = self._evaluate_cycle(cycle_id, started_at, states)
        self._shadow_repository.append_event(
            event_id=f"{self._evidence_window.window_id}:{cycle_id}",
            window_id=self._evidence_window.window_id,
            event_type="REAL_SHADOW_CYCLE",
            bucket_key=None,
            payload=result.as_dict(),
            observed_at=started_at,
        )
        write_reports(
            build_shadow_summary(evidence_window=self._evidence_window, generated_at=started_at),
            self._report_dir,
        )
        return result

    async def run_forever(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            await self.run_once()
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self._poll_seconds)
            except TimeoutError:
                continue

    def _evaluate_cycle(
        self,
        cycle_id: str,
        started_at: datetime,
        states: Sequence[ShadowMarketState],
    ) -> ShadowCycleResult:
        markets_discovered = 0
        proxy_observations = 0
        official_observations = 0
        book_observations = 0
        strategy_evaluations = 0
        abstain_records = 0
        paper_trades = 0
        for state in states:
            bucket_key = f"{state.bucket.asset.value}-{state.bucket.horizon.value}"
            if state.discovery is None:
                abstain_records += self._record_abstain(
                    state,
                    cycle_id=cycle_id,
                    strategy=StrategyKind.DIRECTIONAL_EDGE,
                    reason=state.unavailable_reason or "MARKET_NOT_AVAILABLE",
                    payload={"bucket": bucket_key},
                )
                continue
            markets_discovered += 1
            if state.proxy_reference is not None:
                proxy_observations += 1
            if state.official_reference is not None:
                official_observations += 1
            if state.up_book is not None and state.down_book is not None:
                book_observations += 2
            strategy_evaluations += 1
            abstain_records += self._evaluate_directional(state, cycle_id=cycle_id)
            structural_trade = self._evaluate_structural(state, cycle_id=cycle_id)
            strategy_evaluations += 2
            if structural_trade:
                paper_trades += 1
            else:
                abstain_records += 1
        return ShadowCycleResult(
            cycle_id,
            started_at,
            markets_discovered,
            proxy_observations,
            official_observations,
            book_observations,
            strategy_evaluations,
            abstain_records,
            paper_trades,
        )

    def _evaluate_directional(self, state: ShadowMarketState, *, cycle_id: str) -> int:
        assert state.discovery is not None
        model_status = self._shadow_model_status(state)
        corpus_count = 0
        if self._directional_corpus_repository is not None:
            corpus_count = self._directional_corpus_repository.count(
                asset=state.bucket.asset, horizon=state.bucket.horizon
            )
        ptb_status = state.ptb_status
        up_pricing, down_pricing, pricing_status = self._directional_pricing(state)
        assessment = assess_directional_edge(
            state.discovery.market,
            state.price_to_beat,
            state.directional_features,
            None,
            self._registry.state_for(state.bucket).readiness,
            up_pricing,
            down_pricing,
            observed_at=state.observed_at,
            policy=DirectionalPolicy(
                minimum_net_edge=Decimal("0.03"),
                uncertainty_buffer=Decimal("0.01"),
                minimum_signal_stability=Decimal("0.70"),
                maximum_flip_rate=Decimal("0.20"),
                max_forecast_age=timedelta(seconds=5),
            ),
        )
        self._shadow_repository.append_event(
            event_id=f"{cycle_id}:{state.discovery.market.condition_id}:directional",
            window_id=self._evidence_window.window_id,
            event_type="STRATEGY_EVALUATION",
            bucket_key=f"{state.bucket.asset.value}-{state.bucket.horizon.value}",
            payload={
                "strategy": StrategyKind.DIRECTIONAL_EDGE.value,
                "action": assessment.action.value,
                "reason": assessment.reason,
                "stage": "DIRECTIONAL_RUNTIME",
                "bucket": f"{state.bucket.asset.value}-{state.bucket.horizon.value}",
                "official_status": "OFFICIAL_REFERENCE_UNAVAILABLE"
                if state.official_reference is None
                else "OFFICIAL_REFERENCE_READY",
                "proxy_status": (
                    "PROXY_READY" if state.proxy_reference is not None else "PROXY_UNAVAILABLE"
                ),
                "ptb_status": ptb_status,
                "ptb_reason": state.ptb_reason,
                "ptb_value": str(state.price_to_beat.value)
                if state.price_to_beat is not None
                else None,
                "ptb_effective_time": state.price_to_beat.reference.effective_ts.isoformat()
                if state.price_to_beat is not None
                else None,
                "feature_status": state.feature_status,
                "model_state": model_status.state.value,
                "model_reason": model_status.reason,
                "calibration_state": "CALIBRATION_NOT_READY",
                "pricing_status": pricing_status,
                "corpus_sample_count": corpus_count,
                "real_order_submission": False,
            },
            observed_at=state.observed_at,
        )
        self._save_directional_corpus_placeholder(
            state,
            cycle_id=cycle_id,
            reason=assessment.reason,
            model_status=model_status,
        )
        if assessment.action is DecisionAction.ABSTAIN:
            return self._record_abstain(
                state,
                cycle_id=cycle_id,
                strategy=StrategyKind.DIRECTIONAL_EDGE,
                reason=assessment.reason,
                payload={
                    "market_id": assessment.market_id,
                    "label": _PAPER_LABEL,
                    "ptb_status": ptb_status,
                    "ptb_reason": state.ptb_reason,
                    "feature_status": state.feature_status,
                    "model_state": model_status.state.value,
                    "pricing_status": pricing_status,
                    "corpus_sample_count": corpus_count,
                },
            )
        return 0

    def _directional_pricing(
        self, state: ShadowMarketState
    ) -> tuple[DepthSimulation | None, DepthSimulation | None, str]:
        if state.discovery is None or state.up_book is None or state.down_book is None:
            return None, None, "EXECUTABLE_PRICE_UNAVAILABLE"
        if state.fee_schedule is None:
            return None, None, "FEE_SCHEDULE_UNAVAILABLE"
        try:
            requested_quantity = max(
                state.up_book.minimum_order_size,
                state.down_book.minimum_order_size,
                Decimal("1"),
            )
            policy = PricingPolicy(
                max_book_age=timedelta(seconds=30),
                max_fee_age=timedelta(seconds=30),
                slippage_buffer_bps=Decimal("10"),
                fee_buffer_bps=Decimal("500"),
            )
            up = simulate_depth(
                state.up_book,
                state.fee_schedule,
                side=OrderSide.BUY,
                requested_quantity=requested_quantity,
                limit_price=Decimal("1"),
                role=LiquidityRole.TAKER,
                observed_at=state.observed_at,
                policy=policy,
            )
            down = simulate_depth(
                state.down_book,
                state.fee_schedule,
                side=OrderSide.BUY,
                requested_quantity=requested_quantity,
                limit_price=Decimal("1"),
                role=LiquidityRole.TAKER,
                observed_at=state.observed_at,
                policy=policy,
            )
            return up, down, "EXECUTABLE_PRICE_READY"
        except Exception as exc:
            return None, None, f"EXECUTABLE_PRICE_UNAVAILABLE:{type(exc).__name__}"

    def _shadow_model_status(self, state: ShadowMarketState) -> ShadowBucketModelStatus:
        readiness = self._registry.state_for(state.bucket).readiness
        corpus_count = 0
        if self._directional_corpus_repository is not None:
            corpus_count = self._directional_corpus_repository.count(
                asset=state.bucket.asset, horizon=state.bucket.horizon
            )
        if readiness.ready:
            return ShadowBucketModelStatus(
                state.bucket.asset,
                state.bucket.horizon,
                ShadowModelState.SHADOW_CANDIDATE,
                readiness,
                "SHADOW_MODEL_READY",
                calibration_version=readiness.calibration_version,
                corpus_sample_count=corpus_count,
            )
        return ShadowBucketModelStatus(
            state.bucket.asset,
            state.bucket.horizon,
            ShadowModelState.TRAINING_CORPUS_REQUIRED
            if corpus_count == 0
            else ShadowModelState.INSUFFICIENT_SAMPLE,
            readiness,
            "TRAINING_CORPUS_REQUIRED" if corpus_count == 0 else "INSUFFICIENT_TRAINING_SAMPLE",
            corpus_sample_count=corpus_count,
        )

    def _save_directional_corpus_placeholder(
        self,
        state: ShadowMarketState,
        *,
        cycle_id: str,
        reason: str,
        model_status: ShadowBucketModelStatus,
    ) -> None:
        if self._directional_corpus_repository is None or state.discovery is None:
            return
        market = state.discovery.market
        self._directional_corpus_repository.save_pre_outcome(
            record_id=(
                f"{cycle_id}:directional-corpus:{state.bucket.asset.value}:"
                f"{state.bucket.horizon.value}:{market.condition_id}"
            ),
            asset=state.bucket.asset,
            horizon=state.bucket.horizon,
            condition_id=market.condition_id,
            observed_at=state.observed_at,
            payload={
                "market_id": market.market_id,
                "window_start": market.window_start,
                "window_end": market.window_end,
                "official_reference_ready": state.official_reference is not None,
                "proxy_reference_ready": state.proxy_reference is not None,
                "book_ready": state.up_book is not None and state.down_book is not None,
                "directional_reason": reason,
                "model_state": model_status.state.value,
                "label": _PAPER_LABEL,
            },
        )

    def _evaluate_structural(self, state: ShadowMarketState, *, cycle_id: str) -> bool:
        if (
            state.discovery is None
            or state.up_book is None
            or state.down_book is None
            or state.fee_schedule is None
        ):
            self._record_abstain(
                state,
                cycle_id=cycle_id,
                strategy=StrategyKind.STRUCTURAL_ARBITRAGE,
                reason="STRUCTURAL_INPUTS_UNAVAILABLE",
                payload={"label": _PAPER_LABEL},
            )
            return False
        policy = StructuralPolicy(
            target_shares=max(state.up_book.minimum_order_size, state.down_book.minimum_order_size),
            minimum_net_profit=Decimal("0.01"),
            cycle_buffer_bps=Decimal("10"),
            max_pair_skew=timedelta(seconds=5),
            pricing=PricingPolicy(
                max_book_age=timedelta(seconds=30),
                max_fee_age=timedelta(seconds=30),
                slippage_buffer_bps=Decimal("10"),
                fee_buffer_bps=Decimal("500"),
            ),
        )
        scans = tuple(
            scan_complete_set(
                state.discovery.market,
                state.up_book,
                state.down_book,
                state.fee_schedule,
                state.fee_schedule,
                action=action,
                observed_at=state.observed_at,
                policy=policy,
            )
            for action in (StructuralAction.BUY_MERGE, StructuralAction.SPLIT_SELL)
        )
        executable = next((scan for scan in scans if scan.opportunity is not None), None)
        self._shadow_repository.append_event(
            event_id=f"{cycle_id}:{state.discovery.market.condition_id}:structural",
            window_id=self._evidence_window.window_id,
            event_type="STRUCTURAL_EVALUATION",
            bucket_key=f"{state.bucket.asset.value}-{state.bucket.horizon.value}",
            payload={scan.action.value: scan.reason for scan in scans},
            observed_at=state.observed_at,
        )
        if executable is None or executable.opportunity is None:
            self._record_abstain(
                state,
                cycle_id=cycle_id,
                strategy=StrategyKind.STRUCTURAL_ARBITRAGE,
                reason=";".join(scan.reason for scan in scans),
                payload={"label": _PAPER_LABEL},
            )
            return False
        if executable.opportunity.action is not StructuralAction.BUY_MERGE:
            self._record_abstain(
                state,
                cycle_id=cycle_id,
                strategy=StrategyKind.STRUCTURAL_ARBITRAGE,
                reason="SPLIT_SELL_RESEARCH_ONLY",
                payload={"label": _PAPER_LABEL},
            )
            return False
        self._execute_structural_paper(state, cycle_id=cycle_id, opportunity=executable.opportunity)
        return True

    def _execute_structural_paper(
        self,
        state: ShadowMarketState,
        *,
        cycle_id: str,
        opportunity: StructuralOpportunity,
    ) -> None:
        assert state.discovery is not None
        market = state.discovery.market
        candidate = StrategyCandidate(
            opportunity.opportunity_id,
            StrategyKind.STRUCTURAL_ARBITRAGE,
            market.market_id,
            opportunity.shares * opportunity.combined_price_per_share,
            opportunity.expected_net_profit,
            Decimal("1"),
            state.observed_at,
            min(market.window_end, state.observed_at + timedelta(seconds=5)),
        )
        token_ids = tuple(token.token_id for token in market.tokens)
        routing = route_opportunities(
            (
                RoutingOpportunity(
                    candidate,
                    market.condition_id,
                    token_ids,
                    Decimal("1"),
                    Decimal("1"),
                ),
            ),
            snapshot=RouterSnapshot(0, (), ()),
            available_capital=Decimal("1000"),
            policy=RouterPolicy(
                "v3.15.1-shadow-router",
                (
                    StrategyKind.STRUCTURAL_ARBITRAGE,
                    StrategyKind.DIRECTIONAL_EDGE,
                    StrategyKind.DUAL40,
                ),
                False,
            ),
            now=state.observed_at,
        )
        if routing.selected is None:
            raise RuntimeError("fresh structural candidate was not routed")
        risk = _risk_decision(
            candidate,
            market,
            state,
            approved_id=f"risk:{candidate.candidate_id}",
        )
        if not risk.approved:
            self._record_abstain(
                state,
                cycle_id=cycle_id,
                strategy=StrategyKind.STRUCTURAL_ARBITRAGE,
                reason=";".join(risk.reason_codes),
                payload={"candidate_id": candidate.candidate_id, "label": _PAPER_LABEL},
            )
            return
        plan = build_structural_buy_merge_paper_plan(
            candidate,
            market,
            opportunity,
            risk,
            decision_id=f"decision:{candidate.candidate_id}",
            created_at=state.observed_at,
        )
        gateway = PaperGateway(self._paper_repository)
        result = gateway.execute(
            plan,
            (
                PaperFillEvidence(
                    plan.intents[0].client_order_id,
                    opportunity.up.filled_quantity,
                    opportunity.up.vwap,
                    opportunity.up.total_fee_usdc,
                    state.observed_at,
                    state.observed_at,
                ),
                PaperFillEvidence(
                    plan.intents[1].client_order_id,
                    opportunity.down.filled_quantity,
                    opportunity.down.vwap,
                    opportunity.down.total_fee_usdc,
                    state.observed_at,
                    state.observed_at,
                ),
            ),
            now=state.observed_at,
            kill_switch_active=False,
            ledger_reconciled=True,
        )
        self._paper_repository.save_trade_snapshot(
            trade_id=plan.idempotency_key,
            decision_id=plan.decision_id,
            strategy=StrategyKind.STRUCTURAL_ARBITRAGE.value,
            asset=market.asset.value,
            horizon=market.horizon.value,
            condition_id=market.condition_id,
            side="BUY_MERGE",
            status="OPEN" if result.fills else "ACKNOWLEDGED",
            payload={
                "condition_id": market.condition_id,
                "shares": str(opportunity.shares),
                "entry_vwap": str(opportunity.combined_price_per_share),
                "fee": str(opportunity.up.total_fee_usdc + opportunity.down.total_fee_usdc),
                "slippage": str(
                    opportunity.up.slippage_buffer_usdc + opportunity.down.slippage_buffer_usdc
                ),
                "net_edge": str(opportunity.expected_net_profit),
                "stake": str(candidate.required_capital),
                "fill_status": "FILLED" if result.fills else "ACKNOWLEDGED",
                "position_status": "OPEN",
                "model_version": "MODEL_FREE",
                "label": _PAPER_LABEL,
            },
            observed_at=state.observed_at,
        )

    def _record_abstain(
        self,
        state: ShadowMarketState,
        *,
        cycle_id: str,
        strategy: StrategyKind,
        reason: str,
        payload: Mapping[str, object],
    ) -> int:
        market = state.discovery.market if state.discovery is not None else None
        condition_id = market.condition_id if market is not None else "UNAVAILABLE"
        self._paper_repository.save_abstain(
            abstain_id=(
                f"{cycle_id}:{strategy.value}:{state.bucket.asset.value}:"
                f"{state.bucket.horizon.value}:{condition_id}:{reason}"
            ),
            strategy=strategy.value,
            asset=state.bucket.asset.value,
            horizon=state.bucket.horizon.value,
            condition_id=condition_id,
            reason=reason,
            payload=dict(payload),
            observed_at=state.observed_at,
        )
        return 1


def new_evidence_window(
    *,
    aws_user_id: str,
    aws_account: str,
    aws_arn: str,
    started_at: datetime,
    commit: str,
) -> EvidenceWindow:
    fingerprint = EvidenceFingerprint(
        code_commit=commit,
        strategy_version="v3.15.1-real-shadow",
        model_version="v3.8-unpromoted",
        calibration_version="v3.8-unpromoted",
        feature_schema_version="v3-directional-features",
        risk_policy_version="v3.9-risk",
        router_policy_version="v3.10-router",
        execution_policy_version="v3.11-paper",
        config_hash="paper-real-shadow-v3.15.1",
        artifact_hashes=(),
    )
    return EvidenceWindow(
        window_id=f"real-shadow:{commit[:12]}:{int(started_at.timestamp())}",
        started_at=started_at,
        fingerprint=fingerprint,
        aws_identity=AWSIdentityEvidence(aws_user_id, aws_account, aws_arn, started_at),
    )


async def run_daemon(
    *,
    data_dir: Path,
    report_dir: Path,
    aws_user_id: str,
    aws_account: str,
    aws_arn: str,
    commit: str,
    once: bool,
    poll_seconds: float,
) -> ShadowCycleResult | None:
    if TradingMode.PAPER.value != APP_MODE or LIVE_TRADING_ENABLED or LIVE_AUTO_ARM:
        raise RuntimeError("shadow daemon requires PAPER defaults and LIVE disabled/unarmed")
    data_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    if commit == "unknown":
        commit = _git_commit()
    clock = SystemClock()
    started_at = clock.utc_now()
    evidence_window = new_evidence_window(
        aws_user_id=aws_user_id,
        aws_account=aws_account,
        aws_arn=aws_arn,
        started_at=started_at,
        commit=commit,
    )
    paper = SQLitePaperRepository(data_dir / "paper.sqlite3")
    shadow = SQLiteShadowRepository(data_dir / "shadow_evidence.sqlite3")
    directional_corpus = SQLiteDirectionalCorpusRepository(data_dir / "directional_corpus.sqlite3")
    ptb_repository = SQLitePriceToBeatRepository(data_dir / "price_to_beat.sqlite3")
    paper.initialize()
    shadow.initialize()
    directional_corpus.initialize()
    ptb_repository.initialize()
    shadow.save_window_once(
        window_id=evidence_window.window_id,
        payload=evidence_window.as_dict()
        | {"previous_report_only_window_invalid_for_strategy_burn_in": True},
        started_at=evidence_window.started_at,
    )
    async with PublicTransport(timeout_seconds=15) as transport:
        chainlink = ChainlinkTwapCollector(transport, clock)
        binance_hourly = BinanceHourlyOfficialCollector(transport, clock)
        official_ptb = OfficialPriceToBeatService(
            repository=ptb_repository,
            chainlink=chainlink,
            binance_hourly=binance_hourly,
            policy=ReferenceFreshnessPolicy(
                max_source_age=timedelta(seconds=120),
                max_receive_latency=timedelta(seconds=120),
                boundary_tolerance=timedelta(seconds=90),
            ),
        )
        feature_state = ExternalTemporalState(max_age=timedelta(seconds=120), minimum_points=3)
        daemon = ShadowDaemon(
            data_client=PublicShadowDataClient(
                transport,
                clock,
                official_ptb=official_ptb,
                feature_state=feature_state,
            ),
            paper_repository=paper,
            shadow_repository=shadow,
            evidence_window=evidence_window,
            report_dir=report_dir,
            directional_corpus_repository=directional_corpus,
            clock=clock,
            poll_seconds=poll_seconds,
        )
        collector_stop = asyncio.Event()
        collector_task = asyncio.create_task(chainlink.run(collector_stop))
        if once:
            try:
                return await daemon.run_once()
            finally:
                collector_stop.set()
                collector_task.cancel()
                with suppress(asyncio.CancelledError):
                    await collector_task
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for item in (signal.SIGINT, signal.SIGTERM):
            with suppress(NotImplementedError):
                loop.add_signal_handler(item, stop.set)
        try:
            await daemon.run_forever(stop)
        finally:
            collector_stop.set()
            collector_task.cancel()
            with suppress(asyncio.CancelledError):
                await collector_task
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default=os.environ.get("RUNTIME_DATA_DIR", "runtime/data"))
    parser.add_argument(
        "--report-dir", default=os.environ.get("RUNTIME_REPORT_DIR", "runtime/reports")
    )
    parser.add_argument("--aws-user-id", required=True)
    parser.add_argument("--aws-account", required=True)
    parser.add_argument("--aws-arn", required=True)
    parser.add_argument("--commit", default=os.environ.get("GIT_COMMIT", "unknown"))
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=30.0)
    args = parser.parse_args()
    result = asyncio.run(
        run_daemon(
            data_dir=Path(args.data_dir),
            report_dir=Path(args.report_dir),
            aws_user_id=args.aws_user_id,
            aws_account=args.aws_account,
            aws_arn=args.aws_arn,
            commit=args.commit,
            once=args.once,
            poll_seconds=args.poll_seconds,
        )
    )
    if result is not None:
        print(json.dumps(result.as_dict(), sort_keys=True))


def _risk_decision(
    candidate: StrategyCandidate,
    market: Market,
    state: ShadowMarketState,
    *,
    approved_id: str,
) -> RiskDecision:
    liquidity = None
    if state.up_book is not None and state.down_book is not None and state.fee_schedule is not None:
        book = state.up_book
        if book.asks and book.bids:
            liquidity = LiquidityEvidence(
                state.observed_at,
                state.observed_at - (book.lineage.source_ts or book.lineage.recv_ts),
                book.asks[0].price - book.bids[0].price,
                max(book.asks[0].quantity, book.bids[0].quantity),
                Decimal("1"),
                Decimal("0"),
                Decimal("1"),
                1,
                True,
                True,
                True,
            )
    return assess_candidate(
        candidate,
        asset=market.asset,
        horizon=market.horizon,
        liquidity=liquidity,
        state=PortfolioState(
            Decimal("1000"),
            (),
            Decimal("0"),
            Decimal("0"),
            0,
            None,
            False,
            True,
            state.observed_at,
        ),
        policy=RiskPolicy(
            20,
            Decimal("1000"),
            Decimal("1000"),
            Decimal("1000"),
            Decimal("1000"),
            20,
            Decimal("5"),
            Decimal("1000"),
            Decimal("1000"),
            10,
            timedelta(seconds=60),
            timedelta(seconds=60),
            Decimal("1"),
            Decimal("1"),
            Decimal("0"),
            1,
        ),
        assessed_at=state.observed_at,
        risk_decision_id=approved_id,
    )


def _slug(bucket: MarketBucket, start: datetime) -> str:
    if bucket.horizon is not Horizon.ONE_HOUR:
        return epoch_slug(window_containing(bucket, start))
    try:
        eastern = start.astimezone(ZoneInfo("America/New_York"))
    except ZoneInfoNotFoundError:
        eastern = start.astimezone(timezone(timedelta(hours=-4), "America/New_York"))
    hour = eastern.hour % 12 or 12
    meridiem = "am" if eastern.hour < 12 else "pm"
    return (
        f"{_ASSET_NAME[bucket.asset]}-up-or-down-{_MONTH_NAME[eastern.month]}-"
        f"{eastern.day}-{eastern.year}-{hour}{meridiem}-et"
    )


def _object(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{name} was not an object")
    return value


def _sequence(value: object, name: str) -> Sequence[object]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise RuntimeError(f"{name} was not an array")
    return value


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise RuntimeError(f"{name} was not a non-empty string")
    return value


def _integer(value: object, name: str) -> int:
    if isinstance(value, bool):
        raise RuntimeError(f"{name} was not an integer")
    try:
        return int(str(value))
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{name} was not an integer") from exc


def _git_commit() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


if __name__ == "__main__":
    main()
