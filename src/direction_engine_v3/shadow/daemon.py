"""Persistent SHADOW/PAPER runtime orchestration over existing V3 owners."""

import argparse
import asyncio
import json
import os
import signal
import subprocess
import sys
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import aiohttp

from direction_engine_v3.adapters.binance import parse_depth_top, parse_rest_aggregate_trade
from direction_engine_v3.adapters.polymarket import (
    CLOB_BOOK_URL,
    CLOB_FEE_RATE_URL,
    parse_clob_book,
    parse_fee_rate,
    parse_gamma_market_discovery,
)
from direction_engine_v3.adapters.public_transport import PublicTransport
from direction_engine_v3.config import (
    APP_MODE,
    LIVE_AUTO_ARM,
    LIVE_TRADING_ENABLED,
    PAPER_INITIAL_EQUITY_USDC,
)
from direction_engine_v3.domain import (
    Asset,
    DecisionAction,
    FeatureVector,
    Horizon,
    Market,
    OfficialReference,
    OrderSide,
    OutcomeSide,
    ProbabilityForecast,
    ProxyReference,
    RiskDecision,
    StrategyCandidate,
    StrategyKind,
    TradingMode,
)
from direction_engine_v3.domain._validation import require_utc
from direction_engine_v3.execution import (
    PaperFillEvidence,
    PaperGateway,
    build_directional_paper_plan,
    build_structural_buy_merge_paper_plan,
)
from direction_engine_v3.features import ExternalTemporalState, build_directional_features
from direction_engine_v3.market_data import (
    SUPPORTED_MARKET_BUCKETS,
    CryptoTopOfBook,
    CryptoTrade,
    FeeSchedule,
    MarketBucket,
    MarketDataError,
    MarketDataSchemaError,
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
from direction_engine_v3.models import (
    PAPER_RESEARCH_BASELINE_MODEL_VERSION,
    CalibrationReadiness,
    ShadowBucketModelStatus,
    ShadowModelState,
    load_paper_registry_from_corpus,
    paper_research_baseline_forecast,
)
from direction_engine_v3.pricing import (
    DepthSimulation,
    LiquidityRole,
    PricingPolicy,
    PricingUnavailableError,
    simulate_depth,
)
from direction_engine_v3.risk import (
    LiquidityEvidence,
    OpenExposure,
    PortfolioState,
    RiskPolicy,
    assess_candidate,
)
from direction_engine_v3.router import (
    ClaimStatus,
    RouterClaim,
    RouterPolicy,
    RouterSnapshot,
    RoutingOpportunity,
    route_opportunities,
)
from direction_engine_v3.settlement import GammaOfficialSettlementResolver, PaperSettlementService
from direction_engine_v3.shadow.evidence import (
    AWSIdentityEvidence,
    EvidenceFingerprint,
    EvidenceWindow,
)
from direction_engine_v3.shadow.reporting import build_shadow_summary, write_reports
from direction_engine_v3.shadow.storage import (
    ShadowStartupStorageDiagnostic,
    ShadowStorageBusy,
    SQLiteShadowRepository,
)
from direction_engine_v3.storage import SQLiteDirectionalCorpusRepository, SQLitePaperRepository
from direction_engine_v3.strategies.directional import (
    DirectionalAssessment,
    DirectionalPolicy,
    assess_directional_edge,
)
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
_DIRECTIONAL_CONSECUTIVE_LOSS_LIMIT = 10
_PAPER_CONSECUTIVE_LOSS_COOLDOWN = timedelta(hours=1)
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
class BucketPipelineStage:
    stage: str
    status: str
    observed_at: datetime
    error_type: str | None = None
    reason: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "stage": self.stage,
            "status": self.status,
            "observed_at": self.observed_at.isoformat(),
            "error_type": self.error_type,
            "reason": self.reason,
        }


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
    chainlink_status: Mapping[str, object] | None = None
    binance_hourly_status: Mapping[str, object] | None = None
    pipeline_stages: tuple[BucketPipelineStage, ...] = ()
    up_fee_schedule: FeeSchedule | None = None
    down_fee_schedule: FeeSchedule | None = None


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
        stages: list[BucketPipelineStage] = []
        raw_event = await self._stage(
            stages,
            "GAMMA_FETCH",
            bucket,
            slug=slug,
            operation=lambda: self._transport.get_json(_EVENT_URL.format(slug=slug)),
        )
        if raw_event is None:
            return self._unavailable_state(bucket, stages, "MARKET_NOT_AVAILABLE")
        raw_event_obj = self._stage_sync(
            stages,
            "GAMMA_PARSE",
            bucket,
            slug=slug,
            operation=lambda: _object(raw_event, "Gamma event"),
        )
        if raw_event_obj is None:
            return self._unavailable_state(bucket, stages, "MARKET_NOT_AVAILABLE")
        discovery = self._stage_sync(
            stages,
            "GAMMA_PARSE",
            bucket,
            slug=slug,
            operation=lambda: self._parse_discovery(raw_event_obj, bucket),
        )
        if discovery is None:
            return self._unavailable_state(bucket, stages, "MARKET_NOT_AVAILABLE")

        token_ids = {token.outcome: token.token_id for token in discovery.market.tokens}
        ptb_observed_at = self._clock.utc_now()
        ptb_resolution = await self._stage(
            stages,
            "OFFICIAL_PTB_RESOLVE",
            bucket,
            slug=slug,
            discovery=discovery,
            operation=lambda: self._resolve_ptb(
                discovery, observed_at=ptb_observed_at
            ),
        )
        if ptb_resolution is None:
            ptb_resolution = PriceToBeatResolution(
                None,
                None,
                "PTB_UNAVAILABLE",
                _latest_stage_reason(stages, "OFFICIAL_PTB_RESOLVE") or "OFFICIAL_PTB_UNAVAILABLE",
            )
        chainlink_status = None
        binance_hourly_status = None
        if self._official_ptb is not None:
            chainlink_status = self._official_ptb.chainlink_status().as_dict()
            binance_hourly_status = self._official_ptb.binance_hourly_status().as_dict()

        raw_up_book = await self._stage(
            stages,
            "CLOB_UP_BOOK_FETCH",
            bucket,
            slug=slug,
            discovery=discovery,
            operation=lambda: self._transport.get_json(
                CLOB_BOOK_URL, params={"token_id": token_ids[OutcomeSide.UP]}
            ),
        )
        up_book = None
        if raw_up_book is not None:
            up_book = self._stage_sync(
                stages,
                "CLOB_UP_BOOK_PARSE",
                bucket,
                slug=slug,
                discovery=discovery,
                operation=lambda: self._parse_book(raw_up_book),
            )
        raw_down_book = await self._stage(
            stages,
            "CLOB_DOWN_BOOK_FETCH",
            bucket,
            slug=slug,
            discovery=discovery,
            operation=lambda: self._transport.get_json(
                CLOB_BOOK_URL, params={"token_id": token_ids[OutcomeSide.DOWN]}
            ),
        )
        down_book = None
        if raw_down_book is not None:
            down_book = self._stage_sync(
                stages,
                "CLOB_DOWN_BOOK_PARSE",
                bucket,
                slug=slug,
                discovery=discovery,
                operation=lambda: self._parse_book(raw_down_book),
            )
        raw_up_fee = await self._stage(
            stages,
            "FEE_UP_FETCH",
            bucket,
            slug=slug,
            discovery=discovery,
            operation=lambda: self._transport.get_json(
                CLOB_FEE_RATE_URL, params={"token_id": token_ids[OutcomeSide.UP]}
            ),
        )
        up_fee = None
        if raw_up_fee is not None:
            up_fee = self._stage_sync(
                stages,
                "FEE_UP_PARSE",
                bucket,
                slug=slug,
                discovery=discovery,
                operation=lambda: self._parse_fee_rate(
                    raw_up_fee, discovery, OutcomeSide.UP
                ),
            )
        raw_down_fee = await self._stage(
            stages,
            "FEE_DOWN_FETCH",
            bucket,
            slug=slug,
            discovery=discovery,
            operation=lambda: self._transport.get_json(
                CLOB_FEE_RATE_URL, params={"token_id": token_ids[OutcomeSide.DOWN]}
            ),
        )
        down_fee = None
        if raw_down_fee is not None:
            down_fee = self._stage_sync(
                stages,
                "FEE_DOWN_PARSE",
                bucket,
                slug=slug,
                discovery=discovery,
                operation=lambda: self._parse_fee_rate(
                    raw_down_fee, discovery, OutcomeSide.DOWN
                ),
            )
        raw_depth = await self._stage(
            stages,
            "BINANCE_DEPTH_FETCH",
            bucket,
            slug=slug,
            discovery=discovery,
            operation=lambda: self._transport.get_json(
                _BINANCE_DEPTH_URL,
                params={"symbol": f"{discovery.market.asset.value}USDT", "limit": "5"},
            ),
        )
        external_book = None
        proxy = None
        if raw_depth is not None:
            depth_observation = self._stage_sync(
                stages,
                "BINANCE_DEPTH_PARSE",
                bucket,
                slug=slug,
                discovery=discovery,
                operation=lambda: self._parse_proxy_depth(discovery.market, raw_depth),
            )
            if depth_observation is not None:
                proxy, external_book = depth_observation
        raw_trade = await self._stage(
            stages,
            "BINANCE_TRADE_FETCH",
            bucket,
            slug=slug,
            discovery=discovery,
            operation=lambda: self._transport.get_json(
                _BINANCE_TRADES_URL,
                params={"symbol": f"{discovery.market.asset.value}USDT", "limit": "1"},
            ),
        )
        external_trade = None
        if raw_trade is not None:
            external_trade = self._stage_sync(
                stages,
                "BINANCE_TRADE_PARSE",
                bucket,
                slug=slug,
                discovery=discovery,
                operation=lambda: self._parse_latest_trade(discovery.market, raw_trade),
            )
        if proxy is not None and external_book is not None and self._feature_state is not None:
            self._feature_state.add_reference(proxy)
            self._feature_state.add_book(external_book)
            if external_trade is not None:
                self._feature_state.add_trade(external_trade)
        evaluation_at = self._clock.utc_now()
        market_lifecycle_reason = _market_lifecycle_reason(discovery.market, evaluation_at)
        if market_lifecycle_reason is not None:
            stages.append(
                _stage_record(
                    "FEATURE_BUILD",
                    "SKIPPED",
                    evaluation_at,
                    reason=market_lifecycle_reason,
                )
            )
            fee = _shared_fee_schedule(up_fee, down_fee)
            return ShadowMarketState(
                bucket=bucket,
                discovery=discovery,
                up_book=up_book,
                down_book=down_book,
                fee_schedule=fee,
                proxy_reference=proxy,
                official_reference=ptb_resolution.official_reference,
                observed_at=evaluation_at,
                unavailable_reason=market_lifecycle_reason,
                price_to_beat=ptb_resolution.price_to_beat,
                directional_features=None,
                ptb_status=ptb_resolution.ptb_status,
                ptb_reason=ptb_resolution.reason,
                feature_status=market_lifecycle_reason,
                chainlink_status=chainlink_status,
                binance_hourly_status=binance_hourly_status,
                pipeline_stages=tuple(stages),
                up_fee_schedule=up_fee,
                down_fee_schedule=down_fee,
            )
        features = None
        feature_status = "FEATURES_UNAVAILABLE"
        if proxy is not None:
            feature_result = self._stage_sync(
                stages,
                "FEATURE_BUILD",
                bucket,
                slug=slug,
                discovery=discovery,
                operation=lambda: self._build_directional_features(
                    discovery,
                    proxy,
                    ptb_resolution.price_to_beat,
                    observed_at=evaluation_at,
                ),
            )
            if feature_result is not None:
                features, feature_status = feature_result
        else:
            stages.append(
                _stage_record(
                    "FEATURE_BUILD",
                    "SKIPPED",
                    self._clock.utc_now(),
                    reason="PROXY_UNAVAILABLE",
                )
            )
        fee = _shared_fee_schedule(up_fee, down_fee)
        return ShadowMarketState(
            bucket=bucket,
            discovery=discovery,
            up_book=up_book,
            down_book=down_book,
            fee_schedule=fee,
            proxy_reference=proxy,
            official_reference=ptb_resolution.official_reference,
            observed_at=evaluation_at,
            unavailable_reason=_first_failure_reason(stages),
            price_to_beat=ptb_resolution.price_to_beat,
            directional_features=features,
            ptb_status=ptb_resolution.ptb_status,
            ptb_reason=ptb_resolution.reason,
            feature_status=feature_status,
            chainlink_status=chainlink_status,
            binance_hourly_status=binance_hourly_status,
            pipeline_stages=tuple(stages),
            up_fee_schedule=up_fee,
            down_fee_schedule=down_fee,
        )

    async def _stage(
        self,
        stages: list[BucketPipelineStage],
        stage: str,
        bucket: MarketBucket,
        *,
        slug: str,
        operation: Callable[[], Awaitable[Any]],
        discovery: MarketDiscovery | None = None,
    ) -> Any:
        del bucket, slug, discovery
        try:
            result = await operation()
        except Exception as exc:
            if not _is_expected_market_data_failure(exc):
                raise
            stages.append(_stage_record(stage, "FAIL", self._clock.utc_now(), error=exc))
            return None
        stages.append(_stage_record(stage, "PASS", self._clock.utc_now()))
        return result

    def _stage_sync(
        self,
        stages: list[BucketPipelineStage],
        stage: str,
        bucket: MarketBucket,
        *,
        slug: str,
        operation: Callable[[], Any],
        discovery: MarketDiscovery | None = None,
    ) -> Any:
        del bucket, slug, discovery
        try:
            result = operation()
        except Exception as exc:
            if not _is_expected_market_data_failure(exc):
                raise
            stages.append(_stage_record(stage, "FAIL", self._clock.utc_now(), error=exc))
            return None
        stages.append(_stage_record(stage, "PASS", self._clock.utc_now()))
        return result

    def _parse_discovery(
        self, raw_event: Mapping[str, object], bucket: MarketBucket
    ) -> MarketDiscovery:
        markets = _sequence(raw_event.get("markets"), "Gamma event markets")
        if len(markets) != 1:
            raise MarketDataSchemaError("canonical Gamma event must contain exactly one market")
        recv_ts = self._clock.utc_now()
        return parse_gamma_market_discovery(
            markets[0],
            event_id=_text(raw_event.get("id"), "Gamma event ID"),
            asset=bucket.asset,
            horizon=bucket.horizon,
            recv_ts=recv_ts,
            normalized_ts=self._clock.utc_now(),
            recv_monotonic_ns=self._clock.monotonic_ns(),
        )

    def _unavailable_state(
        self, bucket: MarketBucket, stages: Sequence[BucketPipelineStage], reason: str
    ) -> ShadowMarketState:
        failure = _first_failure_reason(stages) or reason
        return ShadowMarketState(
            bucket=bucket,
            discovery=None,
            up_book=None,
            down_book=None,
            fee_schedule=None,
            proxy_reference=None,
            official_reference=None,
            observed_at=self._clock.utc_now(),
            unavailable_reason=failure,
            ptb_status="PTB_UNAVAILABLE",
            ptb_reason=failure,
            feature_status="FEATURES_UNAVAILABLE",
            pipeline_stages=tuple(stages),
        )

    def _parse_book(self, raw: object) -> PolymarketBook:
        received = self._clock.utc_now()
        return parse_clob_book(
            raw,
            recv_ts=received,
            normalized_ts=self._clock.utc_now(),
            recv_monotonic_ns=self._clock.monotonic_ns(),
        )

    def _parse_fee_rate(
        self, raw: object, discovery: MarketDiscovery, side: OutcomeSide
    ) -> FeeSchedule:
        token_id = {token.outcome: token.token_id for token in discovery.market.tokens}[side]
        return parse_fee_rate(
            raw,
            condition_id=discovery.market.condition_id,
            token_id=token_id,
            expected_token_id=token_id,
            recv_ts=self._clock.utc_now(),
            normalized_ts=self._clock.utc_now(),
            recv_monotonic_ns=self._clock.monotonic_ns(),
        )

    def _parse_proxy_depth(
        self, market: Market, raw: object
    ) -> tuple[ProxyReference, CryptoTopOfBook]:
        payload = _object(raw, "Binance depth")
        now = self._clock.utc_now()
        depth_event = {
            "e": "depthUpdate",
            "E": int(now.timestamp() * 1000),
            "s": f"{market.asset.value}USDT",
            "U": _integer(payload.get("lastUpdateId"), "lastUpdateId"),
            "u": _integer(payload.get("lastUpdateId"), "lastUpdateId"),
            "b": _sequence(payload.get("bids"), "Binance bids"),
            "a": _sequence(payload.get("asks"), "Binance asks"),
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
        return proxy, top

    def _parse_latest_trade(self, market: Market, raw: object) -> CryptoTrade | None:
        rows = _sequence(raw, "Binance aggregate trades")
        if not rows:
            return None
        row = _object(rows[-1], "Binance aggregate trade")
        return parse_rest_aggregate_trade(
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
        settlement_service: PaperSettlementService | None = None,
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
        self._settlement_service = settlement_service
        self._clock = clock or SystemClock()
        self._poll_seconds = poll_seconds
        self._paper_model_result = load_paper_registry_from_corpus(
            directional_corpus_repository
        )
        self._registry = self._paper_model_result.registry

    async def run_once(self) -> ShadowCycleResult:
        cycle_started_at = self._clock.utc_now()
        cycle_id = f"shadow-cycle:{int(cycle_started_at.timestamp() * 1000)}"
        settlement_payload: dict[str, object] = {}
        if self._settlement_service is not None:
            settlement_payload = await self._settlement_service.run_once()
            self._append_shadow_event(
                event_id=f"{self._evidence_window.window_id}:{cycle_id}:settlement",
                window_id=self._evidence_window.window_id,
                event_type="PAPER_SETTLEMENT_SCAN",
                bucket_key=None,
                payload=settlement_payload,
                observed_at=self._clock.utc_now(),
            )
        market_selection_at = self._clock.utc_now()
        states = await asyncio.gather(
            *(
                self._data_client.collect_bucket(bucket, now=market_selection_at)
                for bucket in SUPPORTED_MARKET_BUCKETS
            )
        )
        result = self._evaluate_cycle(cycle_id, cycle_started_at, states)
        completed_at = self._clock.utc_now()
        self._append_shadow_event(
            event_id=f"{self._evidence_window.window_id}:{cycle_id}",
            window_id=self._evidence_window.window_id,
            event_type="REAL_SHADOW_CYCLE",
            bucket_key=None,
            payload=result.as_dict()
            | {
                "settlement": settlement_payload,
                "cycle_started_at": cycle_started_at.isoformat(),
                "market_selection_at": market_selection_at.isoformat(),
                "completed_at": completed_at.isoformat(),
            },
            observed_at=completed_at,
        )
        write_reports(
            build_shadow_summary(
                evidence_window=self._evidence_window, generated_at=completed_at
            ),
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
            pipeline_recorded = self._record_pipeline_state(state, cycle_id=cycle_id)
            if not pipeline_recorded:
                abstain_records += self._record_abstain(
                    state,
                    cycle_id=cycle_id,
                    strategy=StrategyKind.DIRECTIONAL_EDGE,
                    reason="SHADOW_STORAGE_DEGRADED",
                    payload={"bucket": bucket_key, "storage_status": "STORAGE_BUSY"},
                )
                continue
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
            if state.unavailable_reason in {
                "MARKET_WINDOW_NOT_STARTED",
                "MARKET_WINDOW_EXPIRED",
            }:
                abstain_payload = {
                    "bucket": bucket_key,
                    "market_id": state.discovery.market.market_id,
                    "condition_id": state.discovery.market.condition_id,
                    "window_start": state.discovery.market.window_start.isoformat(),
                    "window_end": state.discovery.market.window_end.isoformat(),
                    "evaluation_at": state.observed_at.isoformat(),
                    "label": _PAPER_LABEL,
                }
                abstain_records += self._record_abstain(
                    state,
                    cycle_id=cycle_id,
                    strategy=StrategyKind.DIRECTIONAL_EDGE,
                    reason=state.unavailable_reason,
                    payload=abstain_payload,
                )
                abstain_records += self._record_abstain(
                    state,
                    cycle_id=cycle_id,
                    strategy=StrategyKind.STRUCTURAL_ARBITRAGE,
                    reason=state.unavailable_reason,
                    payload=abstain_payload,
                )
                continue
            strategy_evaluations += 1
            directional_abstains, directional_trade = self._evaluate_directional(
                state, cycle_id=cycle_id
            )
            abstain_records += directional_abstains
            if directional_trade:
                paper_trades += 1
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

    def _record_pipeline_state(self, state: ShadowMarketState, *, cycle_id: str) -> bool:
        market = state.discovery.market if state.discovery is not None else None
        payload = _pipeline_payload(state)
        condition_id = market.condition_id if market is not None else "UNAVAILABLE"
        return self._append_shadow_event(
            event_id=(
                f"{cycle_id}:{state.bucket.asset.value}:{state.bucket.horizon.value}:"
                f"{condition_id}:pipeline"
            ),
            window_id=self._evidence_window.window_id,
            event_type="MARKET_DATA_PIPELINE",
            bucket_key=f"{state.bucket.asset.value}-{state.bucket.horizon.value}",
            payload=payload,
            observed_at=state.observed_at,
        )

    def _append_shadow_event(
        self,
        *,
        event_id: str,
        window_id: str,
        event_type: str,
        bucket_key: str | None,
        payload: Mapping[str, object],
        observed_at: datetime,
    ) -> bool:
        try:
            self._shadow_repository.append_event(
                event_id=event_id,
                window_id=window_id,
                event_type=event_type,
                bucket_key=bucket_key,
                payload=payload,
                observed_at=observed_at,
            )
        except ShadowStorageBusy:
            return False
        return True

    def _evaluate_directional(self, state: ShadowMarketState, *, cycle_id: str) -> tuple[int, bool]:
        assert state.discovery is not None
        model_status = self._shadow_model_status(state)
        corpus_count = 0
        if self._directional_corpus_repository is not None:
            corpus_count = self._directional_corpus_repository.count(
                asset=state.bucket.asset, horizon=state.bucket.horizon
            )
        ptb_status = state.ptb_status
        up_pricing, down_pricing, pricing_status = self._directional_pricing(state)
        forecast, calibration, forecast_status = self._directional_forecast(
            state, model_status=model_status
        )
        assessment = assess_directional_edge(
            state.discovery.market,
            state.price_to_beat,
            state.directional_features,
            forecast,
            calibration,
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
        trade_recorded = False
        directional_execution: dict[str, object] = {}
        execution_abstains = 0
        if assessment.action is DecisionAction.TRADE:
            trade_recorded, directional_execution, execution_abstains = (
                self._execute_directional_paper(
                    state,
                    cycle_id=cycle_id,
                    assessment=assessment,
                    up_pricing=up_pricing,
                    down_pricing=down_pricing,
                    forecast=forecast,
                    calibration_version=calibration.calibration_version
                    if calibration is not None
                    else None,
                )
            )
        self._append_shadow_event(
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
                "model_state": forecast_status["model_state"],
                "model_reason": forecast_status["model_reason"],
                "model_version": forecast.model_version if forecast is not None else None,
                "calibration_state": forecast_status["calibration_state"],
                "calibration_version": calibration.calibration_version
                if calibration is not None
                else None,
                "p_up": str(forecast.p_up) if forecast is not None else None,
                "p_down": str(forecast.p_down) if forecast is not None else None,
                "selected_side": assessment.selected_side.value
                if assessment.selected_side is not None
                else None,
                "selected_probability": str(assessment.selected_probability)
                if assessment.selected_probability is not None
                else None,
                "executable_cost": str(assessment.executable_cost)
                if assessment.executable_cost is not None
                else None,
                "net_edge": str(assessment.net_edge) if assessment.net_edge is not None else None,
                "pricing_status": pricing_status,
                "directional_execution": directional_execution,
                "chainlink": dict(state.chainlink_status or {}),
                "binance_hourly": dict(state.binance_hourly_status or {}),
                "corpus_sample_count": corpus_count,
                "training_report": self._paper_model_result.report_for(state.bucket).as_dict(),
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
            abstains = self._record_abstain(
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
                    "model_state": forecast_status["model_state"],
                    "model_version": forecast.model_version if forecast is not None else None,
                    "calibration_state": forecast_status["calibration_state"],
                    "pricing_status": pricing_status,
                    "corpus_sample_count": corpus_count,
                    "net_edge": str(assessment.net_edge)
                    if assessment.net_edge is not None
                    else None,
                },
            )
            return abstains, False
        return execution_abstains, trade_recorded

    def _directional_forecast(
        self,
        state: ShadowMarketState,
        *,
        model_status: ShadowBucketModelStatus,
    ) -> tuple[ProbabilityForecast | None, CalibrationReadiness | None, dict[str, object]]:
        readiness = self._registry.state_for(state.bucket).readiness
        report = self._paper_model_result.report_for(state.bucket)
        if state.directional_features is None:
            return (
                None,
                readiness,
                {
                    "model_state": model_status.state.value,
                    "model_reason": "FEATURES_UNAVAILABLE",
                    "calibration_state": "CALIBRATION_NOT_READY",
                },
            )
        if readiness.ready:
            try:
                forecast = self._registry.forecast(
                    state.directional_features, generated_at=state.observed_at
                )
                return (
                    forecast,
                    readiness,
                    {
                        "model_state": ShadowModelState.SHADOW_CANDIDATE.value,
                        "model_reason": "PAPER_BUCKET_MODEL_READY",
                        "calibration_state": "CALIBRATION_READY",
                    },
                )
            except Exception as exc:
                return (
                    None,
                    readiness,
                    {
                        "model_state": ShadowModelState.REJECTED.value,
                        "model_reason": f"PAPER_MODEL_FORECAST_FAILED:{type(exc).__name__}",
                        "calibration_state": "CALIBRATION_NOT_READY",
                    },
                )
        if APP_MODE == "PAPER":
            forecast, baseline_readiness = paper_research_baseline_forecast(
                state.directional_features,
                generated_at=state.observed_at,
            )
            return (
                forecast,
                baseline_readiness,
                {
                    "model_state": PAPER_RESEARCH_BASELINE_MODEL_VERSION,
                    "model_reason": report.reason,
                    "calibration_state": "PAPER_RESEARCH_BASELINE_UNPROMOTABLE",
                },
            )
        return (
            None,
            readiness,
            {
                "model_state": model_status.state.value,
                "model_reason": model_status.reason,
                "calibration_state": "CALIBRATION_NOT_READY",
            },
        )

    def _execute_directional_paper(
        self,
        state: ShadowMarketState,
        *,
        cycle_id: str,
        assessment: DirectionalAssessment,
        up_pricing: DepthSimulation | None,
        down_pricing: DepthSimulation | None,
        forecast: ProbabilityForecast | None,
        calibration_version: str | None,
    ) -> tuple[bool, dict[str, object], int]:
        assert state.discovery is not None
        market = state.discovery.market
        entry_checked_at = self._clock.utc_now()
        market_lifecycle_reason = _market_lifecycle_reason(market, entry_checked_at)
        if market_lifecycle_reason is not None:
            abstains = self._record_abstain(
                state,
                cycle_id=cycle_id,
                strategy=StrategyKind.DIRECTIONAL_EDGE,
                reason=market_lifecycle_reason,
                payload={
                    "condition_id": market.condition_id,
                    "entry_checked_at": entry_checked_at.isoformat(),
                    "label": _PAPER_LABEL,
                },
            )
            return False, {"router_status": market_lifecycle_reason}, abstains
        candidate = assessment.candidate
        selected_side = assessment.selected_side
        if not isinstance(candidate, StrategyCandidate) or not isinstance(
            selected_side, OutcomeSide
        ):
            raise RuntimeError("directional TRADE assessment did not carry a candidate")
        pricing = up_pricing if selected_side is OutcomeSide.UP else down_pricing
        if pricing is None or pricing.vwap is None or pricing.worst_price is None:
            raise RuntimeError("directional TRADE assessment did not carry executable pricing")
        token_ids = tuple(token.token_id for token in market.tokens)
        if market.condition_id in self._active_directional_conditions(state.observed_at):
            abstains = self._record_abstain(
                state,
                cycle_id=cycle_id,
                strategy=StrategyKind.DIRECTIONAL_EDGE,
                reason="DIRECTIONAL_POSITION_ALREADY_OPEN",
                payload={"condition_id": market.condition_id, "label": _PAPER_LABEL},
            )
            return (
                False,
                {"router_status": "DIRECTIONAL_POSITION_ALREADY_OPEN"},
                abstains,
            )
        paper_summary = self._paper_repository.summary(now=state.observed_at)
        capital = _paper_capital_gate(paper_summary, candidate.required_capital)
        if capital.status != "PAPER_CAPITAL_OK":
            payload = {
                "candidate_id": candidate.candidate_id,
                "label": _PAPER_LABEL,
                "raw_available_capital": capital.raw_available_capital,
                "spendable_capital": capital.spendable_capital,
                "required_capital": str(candidate.required_capital),
                "open_cost_basis": paper_summary.get("open_cost_basis"),
                "expired_but_unsettled_cost_basis": paper_summary.get(
                    "expired_but_unsettled_cost_basis"
                ),
                "unfilled_reservations": paper_summary.get("unfilled_reservations"),
                "open_trade_count": paper_summary.get("open_trade_count"),
                "open_unique_condition_count": paper_summary.get(
                    "open_unique_condition_count"
                ),
                "legacy_missing_window_end_count": paper_summary.get(
                    "legacy_missing_window_end_count"
                ),
            }
            abstains = self._record_abstain(
                state,
                cycle_id=cycle_id,
                strategy=StrategyKind.DIRECTIONAL_EDGE,
                reason=capital.status,
                payload=payload,
            )
            return False, {"router_status": capital.status, **payload}, abstains
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
            snapshot=self._router_snapshot_from_paper(state.observed_at),
            available_capital=capital.spendable_decimal,
            policy=RouterPolicy(
                "v3.15.4-directional-router",
                (
                    StrategyKind.DIRECTIONAL_EDGE,
                    StrategyKind.STRUCTURAL_ARBITRAGE,
                    StrategyKind.DUAL40,
                ),
                False,
            ),
            now=state.observed_at,
        )
        if routing.selected is None:
            abstains = self._record_abstain(
                state,
                cycle_id=cycle_id,
                strategy=StrategyKind.DIRECTIONAL_EDGE,
                reason="ROUTER_REJECTED_DIRECTIONAL",
                payload={"candidate_id": candidate.candidate_id, "label": _PAPER_LABEL},
            )
            return False, {"router_status": "ROUTER_REJECTED_DIRECTIONAL"}, abstains
        portfolio_state = self._portfolio_state_from_paper(state.observed_at)
        risk = _risk_decision(
            candidate,
            market,
            state,
            portfolio_state=portfolio_state,
            approved_id=f"risk:{candidate.candidate_id}",
        )
        if not risk.approved:
            risk_diagnostics = _portfolio_risk_diagnostics(
                portfolio_state,
                risk.reason_codes,
                state.observed_at,
            )
            abstains = self._record_abstain(
                state,
                cycle_id=cycle_id,
                strategy=StrategyKind.DIRECTIONAL_EDGE,
                reason=";".join(risk.reason_codes),
                payload={
                    "candidate_id": candidate.candidate_id,
                    "risk_decision_id": risk.risk_decision_id,
                    **risk_diagnostics,
                    "label": _PAPER_LABEL,
                },
            )
            return (
                False,
                {
                    "router_status": "ROUTED",
                    "risk_decision_id": risk.risk_decision_id,
                    "risk_approved": False,
                    "risk_reasons": list(risk.reason_codes),
                    **risk_diagnostics,
                },
                abstains,
            )
        plan = build_directional_paper_plan(
            candidate,
            market,
            risk,
            decision_id=f"decision:{candidate.candidate_id}",
            quantity=pricing.filled_quantity,
            limit_price=pricing.worst_price,
            created_at=state.observed_at,
        )
        gateway = PaperGateway(self._paper_repository)
        result = gateway.execute(
            plan,
            (
                PaperFillEvidence(
                    plan.intents[0].client_order_id,
                    pricing.filled_quantity,
                    pricing.vwap,
                    pricing.total_fee_usdc,
                    state.observed_at,
                    state.observed_at,
                ),
            ),
            now=state.observed_at,
            kill_switch_active=False,
            ledger_reconciled=True,
        )
        trade = self._paper_repository.save_trade_snapshot(
            trade_id=plan.idempotency_key,
            decision_id=plan.decision_id,
            strategy=StrategyKind.DIRECTIONAL_EDGE.value,
            asset=market.asset.value,
            horizon=market.horizon.value,
            condition_id=market.condition_id,
            side=selected_side.value,
            status="OPEN" if result.fills else "ACKNOWLEDGED",
            payload={
                "asset": market.asset.value,
                "horizon": market.horizon.value,
                "condition_id": market.condition_id,
                "market_id": market.market_id,
                "window_start": market.window_start.isoformat(),
                "window_end": market.window_end.isoformat(),
                "side": selected_side.value,
                "ptb": str(state.price_to_beat.value)
                if state.price_to_beat is not None
                else None,
                "p_up": str(forecast.p_up) if forecast is not None else None,
                "p_down": str(forecast.p_down) if forecast is not None else None,
                "executable_cost": str(assessment.executable_cost),
                "entry_vwap": str(pricing.vwap),
                "fee": str(pricing.total_fee_usdc),
                "net_edge": str(assessment.net_edge),
                "stake": str(candidate.required_capital),
                "cost_basis_usdc": str(candidate.required_capital),
                "shares": str(pricing.filled_quantity),
                "model_version": forecast.model_version if forecast is not None else None,
                "calibration_version": calibration_version,
                "risk_decision_id": risk.risk_decision_id,
                "risk_approved": True,
                "fill_status": "FILLED" if result.fills else "ACKNOWLEDGED",
                "position_status": "OPEN",
                "real_order_submission": False,
                "label": _PAPER_LABEL,
            },
            observed_at=state.observed_at,
        )
        return (
            True,
            {
                "router_status": "ROUTED",
                "risk_decision_id": risk.risk_decision_id,
                "risk_approved": True,
                "paper_trade_id": trade.trade_id,
                "paper_fill_status": "FILLED" if result.fills else "ACKNOWLEDGED",
            },
            0,
        )

    def _active_directional_conditions(self, now: datetime) -> set[str]:
        active = set()
        for trade in self._paper_repository.trades(
            strategy=StrategyKind.DIRECTIONAL_EDGE.value,
            status="OPEN",
            limit=100_000,
        ):
            active.add(trade.condition_id)
        return active

    def _router_snapshot_from_paper(self, now: datetime) -> RouterSnapshot:
        claims: list[RouterClaim] = []
        for trade in self._paper_repository.trades(
            strategy=StrategyKind.DIRECTIONAL_EDGE.value,
            status="OPEN",
            limit=100_000,
        ):
            window_end = _payload_datetime(trade.payload.get("window_end")) or (
                now + timedelta(minutes=5)
            )
            if window_end <= now:
                continue
            if str(trade.payload.get("fill_status", "")).upper() == "FILLED":
                continue
            stake = _payload_decimal(trade.payload, "cost_basis_usdc", "stake")
            if stake <= Decimal("0"):
                continue
            claims.append(
                RouterClaim(
                    claim_id=f"paper-open:{trade.trade_id}",
                    idempotency_key=f"paper-open:{trade.trade_id}",
                    candidate_id=trade.decision_id,
                    condition_id=trade.condition_id,
                    token_ids=(str(trade.payload.get("token_id", trade.trade_id)),),
                    strategy=StrategyKind.DIRECTIONAL_EDGE,
                    reserved_capital=stake,
                    created_at=trade.observed_at,
                    expires_at=window_end,
                    status=ClaimStatus.ACTIVE,
                )
            )
        return RouterSnapshot(0, tuple(claims), ())

    def _portfolio_state_from_paper(self, now: datetime) -> PortfolioState:
        summary = self._paper_repository.summary(now=now)
        consecutive_losses = int(str(summary["current_losing_streak"]))
        cooldown_until = _paper_cooldown_until(summary, consecutive_losses)
        exposures = []
        for trade in self._paper_repository.trades(
            strategy=StrategyKind.DIRECTIONAL_EDGE.value,
            status="OPEN",
            limit=100_000,
        ):
            window_end = _payload_datetime(trade.payload.get("window_end")) or (
                now + timedelta(minutes=5)
            )
            stake = _payload_decimal(trade.payload, "cost_basis_usdc", "stake")
            if stake <= Decimal("0"):
                continue
            exposure_window_end = window_end
            if exposure_window_end <= now:
                exposure_window_end = now + timedelta(seconds=1)
            exposures.append(
                OpenExposure(
                    trade.trade_id,
                    trade.condition_id,
                    Asset(trade.asset),
                    Horizon(trade.horizon),
                    stake,
                    exposure_window_end,
                )
            )
        return PortfolioState(
            Decimal(str(summary["spendable_capital"])),
            tuple(exposures),
            Decimal(str(summary["realized_pnl"])),
            Decimal(str(summary["maximum_drawdown"])),
            consecutive_losses,
            cooldown_until,
            False,
            True,
            now,
        )

    def _directional_pricing(
        self, state: ShadowMarketState
    ) -> tuple[DepthSimulation | None, DepthSimulation | None, str]:
        if state.discovery is None or state.up_book is None or state.down_book is None:
            return None, None, "EXECUTABLE_PRICE_UNAVAILABLE"
        up_fee = state.up_fee_schedule or state.fee_schedule
        down_fee = state.down_fee_schedule or state.fee_schedule
        if up_fee is None or down_fee is None:
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
                up_fee,
                side=OrderSide.BUY,
                requested_quantity=requested_quantity,
                limit_price=Decimal("1"),
                role=LiquidityRole.TAKER,
                observed_at=state.observed_at,
                policy=policy,
            )
            down = simulate_depth(
                state.down_book,
                down_fee,
                side=OrderSide.BUY,
                requested_quantity=requested_quantity,
                limit_price=Decimal("1"),
                role=LiquidityRole.TAKER,
                observed_at=state.observed_at,
                policy=policy,
            )
            return up, down, "EXECUTABLE_PRICE_READY"
        except Exception as exc:
            return None, None, f"EXECUTABLE_PRICE_UNAVAILABLE:{_safe_error_text(exc)}"

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
                "feature_vector": _feature_vector_payload(state.directional_features),
                "price_to_beat": _price_to_beat_payload(state.price_to_beat),
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
            or (state.fee_schedule is None and state.up_fee_schedule is None)
            or (state.fee_schedule is None and state.down_fee_schedule is None)
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
        up_fee = state.up_fee_schedule or state.fee_schedule
        down_fee = state.down_fee_schedule or state.fee_schedule
        if up_fee is None or down_fee is None:
            raise RuntimeError("structural fee schedules disappeared after validation")
        scans_list = []
        for action in (StructuralAction.BUY_MERGE, StructuralAction.SPLIT_SELL):
            try:
                scans_list.append(
                    scan_complete_set(
                        state.discovery.market,
                        state.up_book,
                        state.down_book,
                        up_fee,
                        down_fee,
                        action=action,
                        observed_at=state.observed_at,
                        policy=policy,
                    )
                )
            except Exception as exc:
                reason = _structural_market_quality_reason(exc)
                if reason is None:
                    raise
                self._record_abstain(
                    state,
                    cycle_id=cycle_id,
                    strategy=StrategyKind.STRUCTURAL_ARBITRAGE,
                    reason=reason,
                    payload={
                        "label": _PAPER_LABEL,
                        "stage": "STRUCTURAL_RUNTIME",
                        "error_type": type(exc).__name__,
                    },
                )
                self._append_shadow_event(
                    event_id=(
                        f"{cycle_id}:{state.discovery.market.condition_id}:"
                        f"structural:{action.value}:{reason}"
                    ),
                    window_id=self._evidence_window.window_id,
                    event_type="STRUCTURAL_EVALUATION",
                    bucket_key=f"{state.bucket.asset.value}-{state.bucket.horizon.value}",
                    payload={
                        "strategy": StrategyKind.STRUCTURAL_ARBITRAGE.value,
                        "action": action.value,
                        "reason": reason,
                        "stage": "STRUCTURAL_RUNTIME",
                        "label": _PAPER_LABEL,
                    },
                    observed_at=state.observed_at,
                )
                return False
        scans = tuple(scans_list)
        executable = next((scan for scan in scans if scan.opportunity is not None), None)
        self._append_shadow_event(
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
        entry_checked_at = self._clock.utc_now()
        market_lifecycle_reason = _market_lifecycle_reason(market, entry_checked_at)
        if market_lifecycle_reason is not None:
            self._record_abstain(
                state,
                cycle_id=cycle_id,
                strategy=StrategyKind.STRUCTURAL_ARBITRAGE,
                reason=market_lifecycle_reason,
                payload={
                    "condition_id": market.condition_id,
                    "entry_checked_at": entry_checked_at.isoformat(),
                    "label": _PAPER_LABEL,
                },
            )
            return
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
        paper_summary = self._paper_repository.summary(now=state.observed_at)
        capital = _paper_capital_gate(paper_summary, candidate.required_capital)
        if capital.status != "PAPER_CAPITAL_OK":
            self._record_abstain(
                state,
                cycle_id=cycle_id,
                strategy=StrategyKind.STRUCTURAL_ARBITRAGE,
                reason=capital.status,
                payload={
                    "candidate_id": candidate.candidate_id,
                    "label": _PAPER_LABEL,
                    "raw_available_capital": capital.raw_available_capital,
                    "spendable_capital": capital.spendable_capital,
                    "required_capital": str(candidate.required_capital),
                    "open_cost_basis": paper_summary.get("open_cost_basis"),
                    "known_expired_unsettled_cost_basis": paper_summary.get(
                        "known_expired_unsettled_cost_basis"
                    ),
                    "unknown_window_open_cost_basis": paper_summary.get(
                        "unknown_window_open_cost_basis"
                    ),
                },
            )
            return
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
            snapshot=self._router_snapshot_from_paper(state.observed_at),
            available_capital=capital.spendable_decimal,
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
            self._record_abstain(
                state,
                cycle_id=cycle_id,
                strategy=StrategyKind.STRUCTURAL_ARBITRAGE,
                reason="ROUTER_REJECTED_STRUCTURAL",
                payload={"candidate_id": candidate.candidate_id, "label": _PAPER_LABEL},
            )
            return
        risk = _risk_decision(
            candidate,
            market,
            state,
            portfolio_state=self._portfolio_state_from_paper(state.observed_at),
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


def _structural_market_quality_reason(exc: Exception) -> str | None:
    """Map expected structural data-quality failures to fail-closed ABSTAIN reasons."""

    message = str(exc).lower()
    if isinstance(exc, PricingUnavailableError):
        if "fee" in message and "stale" in message:
            return "FEE_STALE"
        if "book" in message and "source timestamp is stale" in message:
            return "BOOK_STALE"
        if "depth" in message or "liquidity" in message:
            return "INSUFFICIENT_PAIRED_DEPTH"
        return "PRICING_UNAVAILABLE"
    if isinstance(exc, ValueError):
        if "paired books exceed maximum source-time skew" in message:
            return "PAIRED_BOOK_SOURCE_SKEW"
        if "paired books exceed maximum receive-time skew" in message:
            return "PAIRED_BOOK_RECEIVE_SKEW"
        if "paired books require source timestamps" in message:
            return "PAIRED_BOOK_SOURCE_TIMESTAMP_MISSING"
    return None


def _market_lifecycle_reason(market: Market, observed_at: datetime) -> str | None:
    require_utc("observed_at", observed_at)
    if observed_at < market.window_start:
        return "MARKET_WINDOW_NOT_STARTED"
    if observed_at >= market.window_end:
        return "MARKET_WINDOW_EXPIRED"
    return None


def _stage_record(
    stage: str,
    status: str,
    observed_at: datetime,
    *,
    error: Exception | None = None,
    reason: str | None = None,
) -> BucketPipelineStage:
    if error is not None:
        reason = _stage_failure_reason(stage, error)
    return BucketPipelineStage(
        stage=stage,
        status=status,
        observed_at=observed_at,
        error_type=type(error).__name__ if error is not None else None,
        reason=reason,
    )


def _stage_failure_reason(stage: str, exc: Exception) -> str:
    reason_stage = {
        "FEE_UP_FETCH": "FEE_FETCH",
        "FEE_DOWN_FETCH": "FEE_FETCH",
        "FEE_UP_PARSE": "FEE_PARSE",
        "FEE_DOWN_PARSE": "FEE_PARSE",
    }.get(stage, stage)
    if isinstance(exc, MarketDataSchemaError):
        return f"MARKET_DATA_SCHEMA_ERROR:{reason_stage}"
    if isinstance(exc, MarketDataError):
        return f"MARKET_DATA_ERROR:{reason_stage}"
    if isinstance(exc, PricingUnavailableError):
        return f"PRICING_UNAVAILABLE:{reason_stage}"
    if isinstance(exc, TimeoutError):
        return f"TRANSPORT_TIMEOUT:{reason_stage}"
    return f"PUBLIC_DATA_UNAVAILABLE:{reason_stage}"


def _is_expected_market_data_failure(exc: Exception) -> bool:
    return isinstance(
        exc,
        (
            MarketDataError,
            PricingUnavailableError,
            TimeoutError,
            aiohttp.ClientError,
        ),
    )


def _first_failure_reason(stages: Sequence[BucketPipelineStage]) -> str | None:
    for stage in stages:
        if stage.status == "FAIL":
            return stage.reason or f"STAGE_FAILED:{stage.stage}"
    return None


def _latest_stage_reason(stages: Sequence[BucketPipelineStage], stage_name: str) -> str | None:
    for stage in reversed(stages):
        if stage.stage == stage_name and stage.status == "FAIL":
            return stage.reason
    return None


def _shared_fee_schedule(
    up_fee: FeeSchedule | None, down_fee: FeeSchedule | None
) -> FeeSchedule | None:
    if up_fee is None or down_fee is None:
        return None
    if up_fee.taker_base_bps != down_fee.taker_base_bps:
        return None
    return up_fee


def _pipeline_payload(state: ShadowMarketState) -> dict[str, object]:
    market = state.discovery.market if state.discovery is not None else None
    by_stage = {stage.stage: stage for stage in state.pipeline_stages}
    return {
        "label": _PAPER_LABEL,
        "asset": state.bucket.asset.value,
        "horizon": state.bucket.horizon.value,
        "latest_observed_at": state.observed_at.isoformat(),
        "market_id": market.market_id if market is not None else None,
        "condition_id": market.condition_id if market is not None else None,
        "discovery_status": _combined_status(by_stage, ("GAMMA_FETCH", "GAMMA_PARSE")),
        "discovery_error": _combined_error(by_stage, ("GAMMA_FETCH", "GAMMA_PARSE")),
        "book_status": _combined_status(
            by_stage,
            (
                "CLOB_UP_BOOK_FETCH",
                "CLOB_UP_BOOK_PARSE",
                "CLOB_DOWN_BOOK_FETCH",
                "CLOB_DOWN_BOOK_PARSE",
            ),
        ),
        "book_error": _combined_error(
            by_stage,
            (
                "CLOB_UP_BOOK_FETCH",
                "CLOB_UP_BOOK_PARSE",
                "CLOB_DOWN_BOOK_FETCH",
                "CLOB_DOWN_BOOK_PARSE",
            ),
        ),
        "fee_status": _combined_status(
            by_stage,
            ("FEE_UP_FETCH", "FEE_UP_PARSE", "FEE_DOWN_FETCH", "FEE_DOWN_PARSE"),
        ),
        "fee_error": _combined_error(
            by_stage,
            ("FEE_UP_FETCH", "FEE_UP_PARSE", "FEE_DOWN_FETCH", "FEE_DOWN_PARSE"),
        ),
        "proxy_status": _combined_status(
            by_stage,
            (
                "BINANCE_DEPTH_FETCH",
                "BINANCE_DEPTH_PARSE",
                "BINANCE_TRADE_FETCH",
                "BINANCE_TRADE_PARSE",
            ),
        ),
        "proxy_error": _combined_error(
            by_stage,
            (
                "BINANCE_DEPTH_FETCH",
                "BINANCE_DEPTH_PARSE",
                "BINANCE_TRADE_FETCH",
                "BINANCE_TRADE_PARSE",
            ),
        ),
        "official_status": "OFFICIAL_REFERENCE_READY"
        if state.official_reference is not None
        else "OFFICIAL_REFERENCE_UNAVAILABLE",
        "ptb_status": state.ptb_status,
        "ptb_reason": state.ptb_reason,
        "ptb_value": str(state.price_to_beat.value) if state.price_to_beat is not None else None,
        "ptb_effective_time": state.price_to_beat.reference.effective_ts.isoformat()
        if state.price_to_beat is not None
        else None,
        "feature_status": state.feature_status,
        "feature_error": _combined_error(by_stage, ("FEATURE_BUILD",)),
        "chainlink": dict(state.chainlink_status or {}),
        "binance_hourly": dict(state.binance_hourly_status or {}),
        "pipeline_stages": [stage.as_dict() for stage in state.pipeline_stages],
        "real_order_submission": False,
    }


def _combined_status(
    by_stage: Mapping[str, BucketPipelineStage], stage_names: Sequence[str]
) -> str:
    selected = [by_stage[name] for name in stage_names if name in by_stage]
    if not selected:
        return "NOT_RUN"
    if any(stage.status == "FAIL" for stage in selected):
        return "FAILED"
    if any(stage.status == "SKIPPED" for stage in selected):
        return "SKIPPED"
    return "READY"


def _combined_error(
    by_stage: Mapping[str, BucketPipelineStage], stage_names: Sequence[str]
) -> str | None:
    for name in stage_names:
        stage = by_stage.get(name)
        if stage is not None and stage.status in {"FAIL", "SKIPPED"}:
            return stage.reason
    return None


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


def _shadow_startup_log(
    *, status: str, diagnostic: ShadowStartupStorageDiagnostic, process_name: str
) -> None:
    payload = diagnostic.as_dict() | {
        "status": status,
        "process_name": process_name,
        "pid": os.getpid(),
    }
    print(
        f"shadow_startup_storage={json.dumps(payload, sort_keys=True)}",
        file=sys.stderr,
        flush=True,
    )


def _prepare_shadow_evidence_startup(
    *,
    shadow: SQLiteShadowRepository,
    evidence_window: EvidenceWindow,
    payload: Mapping[str, object],
    deadline_seconds: float = 30.0,
    process_name: str = "shadow-daemon",
) -> None:
    schema_diagnostic = shadow.initialize_for_startup(deadline_seconds=deadline_seconds)
    _shadow_startup_log(
        status="STORAGE_SCHEMA_READY",
        diagnostic=schema_diagnostic,
        process_name=process_name,
    )
    window_diagnostic = shadow.save_window_once_for_startup(
        window_id=evidence_window.window_id,
        payload=payload,
        started_at=evidence_window.started_at,
        deadline_seconds=deadline_seconds,
    )
    _shadow_startup_log(
        status="STORAGE_READY",
        diagnostic=window_diagnostic,
        process_name=process_name,
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
    directional_corpus.initialize()
    ptb_repository.initialize()
    _prepare_shadow_evidence_startup(
        shadow=shadow,
        evidence_window=evidence_window,
        payload=evidence_window.as_dict()
        | {"previous_report_only_window_invalid_for_strategy_burn_in": True},
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
        settlement_service = PaperSettlementService(
            paper_repository=paper,
            corpus_repository=directional_corpus,
            resolver=GammaOfficialSettlementResolver(transport, clock),
            clock=clock,
            max_trades_per_pass=1,
        )
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
            settlement_service=settlement_service,
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
    portfolio_state: PortfolioState | None = None,
    approved_id: str,
) -> RiskDecision:
    paper_equity = Decimal(PAPER_INITIAL_EQUITY_USDC)
    liquidity = None
    if (
        state.up_book is not None
        and state.down_book is not None
        and (state.fee_schedule is not None or state.up_fee_schedule is not None)
    ):
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
        state=portfolio_state
        or PortfolioState(
            paper_equity, (), Decimal("0"), Decimal("0"), 0, None, False, True, state.observed_at
        ),
        policy=RiskPolicy(
            20,
            paper_equity,
            paper_equity,
            paper_equity,
            paper_equity,
            20,
            Decimal("5"),
            paper_equity,
            paper_equity,
            _DIRECTIONAL_CONSECUTIVE_LOSS_LIMIT,
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


def _feature_vector_payload(features: FeatureVector | None) -> dict[str, object] | None:
    if features is None:
        return None
    return {
        "market_id": features.market_id,
        "asset": features.asset.value,
        "horizon": features.horizon.value,
        "feature_set_version": features.feature_set_version,
        "generated_at": features.generated_at.isoformat(),
        "features": [
            {
                "name": item.name,
                "value": str(item.value),
                "source": item.source,
                "source_ts": item.source_ts.isoformat(),
            }
            for item in features.features
        ],
    }


def _price_to_beat_payload(record: PriceToBeatRecord | None) -> dict[str, object] | None:
    if record is None:
        return None
    return {
        "condition_id": record.condition_id,
        "persistence_id": record.persistence_id,
        "value": str(record.value),
        "reference_id": record.reference.reference_id,
        "reference_source": record.reference.source,
        "effective_ts": record.reference.effective_ts.isoformat(),
        "established_at": record.established_at.isoformat(),
    }


def _safe_error_text(exc: Exception) -> str:
    text = str(exc).replace("\r", " ").replace("\n", " ").strip()
    return text or type(exc).__name__


def _payload_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.astimezone(UTC)


def _paper_cooldown_until(
    summary: Mapping[str, object], consecutive_losses: int
) -> datetime | None:
    if consecutive_losses < _DIRECTIONAL_CONSECUTIVE_LOSS_LIMIT:
        return None
    last_successful_settlement_at = _payload_datetime(
        summary.get("last_successful_settlement_at")
    )
    if last_successful_settlement_at is None:
        return None
    return last_successful_settlement_at + _PAPER_CONSECUTIVE_LOSS_COOLDOWN


def _portfolio_risk_diagnostics(
    portfolio_state: PortfolioState,
    risk_reasons: tuple[str, ...],
    observed_at: datetime,
) -> dict[str, object]:
    cooldown_until = portfolio_state.cooldown_until
    cooldown_remaining_seconds = 0
    last_successful_settlement_at = None
    if cooldown_until is not None:
        last_successful_settlement_at = cooldown_until - _PAPER_CONSECUTIVE_LOSS_COOLDOWN
        if cooldown_until > observed_at:
            cooldown_remaining_seconds = max(
                0, int((cooldown_until - observed_at).total_seconds())
            )
    return {
        "current_losing_streak": portfolio_state.consecutive_losses,
        "cooldown_active": "CONSECUTIVE_LOSS_COOLDOWN_ACTIVE" in risk_reasons,
        "cooldown_until": cooldown_until.isoformat() if cooldown_until is not None else None,
        "cooldown_remaining_seconds": cooldown_remaining_seconds,
        "last_successful_settlement_at": (
            last_successful_settlement_at.isoformat()
            if last_successful_settlement_at is not None
            else None
        ),
        "risk_reasons": list(risk_reasons),
    }


def _payload_decimal(payload: Mapping[str, object], primary: str, fallback: str) -> Decimal:
    return Decimal(str(payload.get(primary, payload.get(fallback, "0"))))


@dataclass(frozen=True, slots=True)
class _PaperCapitalGate:
    status: str
    raw_available_decimal: Decimal | None
    spendable_decimal: Decimal

    @property
    def raw_available_capital(self) -> str | None:
        if self.raw_available_decimal is None:
            return None
        return str(self.raw_available_decimal)

    @property
    def spendable_capital(self) -> str:
        return str(self.spendable_decimal)


def _paper_capital_gate(
    paper_summary: Mapping[str, object], required_capital: Decimal
) -> _PaperCapitalGate:
    try:
        raw_value = paper_summary.get(
            "raw_available_capital", paper_summary.get("available_capital")
        )
        if raw_value is None:
            raise ValueError("raw_available_capital missing")
        raw_available = Decimal(str(raw_value))
    except Exception:
        return _PaperCapitalGate("PAPER_CAPITAL_STATE_INVALID", None, Decimal("0"))
    if not raw_available.is_finite():
        return _PaperCapitalGate("PAPER_CAPITAL_STATE_INVALID", None, Decimal("0"))
    if raw_available < Decimal("0"):
        return _PaperCapitalGate("PAPER_CAPITAL_DEFICIT", raw_available, Decimal("0"))
    if raw_available <= Decimal("0") or raw_available < required_capital:
        return _PaperCapitalGate(
            "INSUFFICIENT_PAPER_CAPITAL",
            raw_available,
            max(Decimal("0"), raw_available),
        )
    return _PaperCapitalGate("PAPER_CAPITAL_OK", raw_available, raw_available)


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
        raise MarketDataSchemaError(f"{name} was not an object")
    return value


def _sequence(value: object, name: str) -> Sequence[object]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise MarketDataSchemaError(f"{name} was not an array")
    return value


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise MarketDataSchemaError(f"{name} was not a non-empty string")
    return value


def _integer(value: object, name: str) -> int:
    if isinstance(value, bool):
        raise MarketDataSchemaError(f"{name} was not an integer")
    try:
        return int(str(value))
    except (TypeError, ValueError) as exc:
        raise MarketDataSchemaError(f"{name} was not an integer") from exc


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
