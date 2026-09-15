"""Deterministic V3.15 shadow report generation."""

import json
from datetime import UTC, datetime
from pathlib import Path

from direction_engine_v3.config import LIVE_AUTO_ARM, LIVE_TRADING_ENABLED
from direction_engine_v3.market_data import SUPPORTED_MARKET_BUCKETS
from direction_engine_v3.shadow.evidence import (
    BucketEvidence,
    EvidenceWindow,
    ShadowSummary,
    StructuralEvidence,
)


def build_shadow_summary(
    *, evidence_window: EvidenceWindow, generated_at: datetime | None = None
) -> ShadowSummary:
    generated = generated_at or datetime.now(UTC)
    buckets = tuple(BucketEvidence(bucket=bucket) for bucket in SUPPORTED_MARKET_BUCKETS)
    return ShadowSummary(
        generated_at=generated,
        evidence_window=evidence_window,
        buckets=buckets,
        structural=StructuralEvidence(),
        live_trading_enabled=LIVE_TRADING_ENABLED,
        live_auto_arm=LIVE_AUTO_ARM,
        real_order_submission=False,
        status="V3.15_INFRA_ACCEPTED_EVIDENCE_ACCUMULATING",
    )


def write_reports(summary: ShadowSummary, report_dir: Path) -> tuple[Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    payload = summary.as_dict()
    json_path = report_dir / "shadow_summary.json"
    md_path = report_dir / "shadow_summary.md"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    lines = [
        "# V3.15 Shadow Summary",
        "",
        f"Status: `{summary.status}`",
        f"Generated: `{summary.generated_at.isoformat()}`",
        f"Window: `{summary.evidence_window.window_id}`",
        f"AWS: `{summary.evidence_window.aws_identity.arn}`",
        f"AWS security debt: `{summary.evidence_window.aws_identity.security_debt}`",
        "",
        "| Bucket | Forecasts | Verified | Trades | State |",
        "|---|---:|---:|---:|---|",
    ]
    for bucket in summary.buckets:
        lines.append(
            "| "
            f"{bucket.bucket.asset.value}-{bucket.bucket.horizon.value} | "
            f"{bucket.forecast_count} | {bucket.verified_resolved_count} | "
            f"{bucket.paper_trade_count} | {bucket.promotion_state.value} |"
        )
    lines.extend(
        [
            "",
            f"Structural state: `{summary.structural.promotion_state.value}`",
            "",
            "LIVE remains disabled, auto-arm remains disabled, and real order "
            "submission is absent.",
        ]
    )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path
