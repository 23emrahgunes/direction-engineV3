from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = REPOSITORY_ROOT / "src" / "direction_engine_v3"
AUDIT = REPOSITORY_ROOT / "docs" / "02-WHALESIGNAL-MIGRATION-AUDIT.md"


def test_v3_has_no_runtime_dependency_on_whalesignal_or_legacy_modules() -> None:
    production = "\n".join(
        path.read_text(encoding="utf-8")
        for path in SOURCE_ROOT.rglob("*.py")
    ).lower()
    assert "import whalesignal" not in production
    assert "from whalesignal" not in production
    assert "import p26_" not in production
    assert "from p26_" not in production
    assert "import p3_" not in production
    assert "from p3_" not in production


def test_no_legacy_phase_named_python_modules_were_bulk_copied() -> None:
    names = {path.name.lower() for path in SOURCE_ROOT.rglob("*.py")}
    assert not any(name.startswith(("p25_", "p26_", "p3_")) for name in names)
    assert not any("final" in name or "fixed" in name for name in names)


def test_directional_namespace_forbids_recovery_sizing() -> None:
    directional = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (SOURCE_ROOT / "strategies" / "directional").rglob("*.py")
    ).lower()
    for forbidden in ("martingale", "loss_recovery", "recovery_ladder"):
        assert forbidden not in directional


def test_structural_namespace_has_no_directional_model_dependency() -> None:
    structural = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (SOURCE_ROOT / "strategies" / "structural_arb").rglob("*.py")
    ).lower()
    assert "probabilityforecast" not in structural.replace("_", "")
    assert "directional" not in structural
    assert "direction_engine_v3.models" not in structural


def test_audit_records_required_capabilities_and_decisions() -> None:
    text = AUDIT.read_text(encoding="utf-8")
    required = {
        "Binance trades/depth",
        "Chainlink/reference",
        "Market discovery and identity",
        "CLOB book persistence",
        "Dynamic CLOB fees",
        "Depth execution simulation",
        "External-only fair value",
        "Calibration",
        "Liquidity guard",
        "Portfolio risk",
        "Complete-set parity",
        "P3 latency and one-leg replay",
        "LIVE preflight",
        "Reconciliation",
        "Ledger",
        "Directional Edge V2",
        "DUAL40",
    }
    documented = {
        line.split("|")[1].strip() for line in text.splitlines() if line.startswith("|")
    }
    assert required <= documented
    for decision in ("PORT", "REWRITE", "REJECT", "DEFER", "QUARANTINE"):
        assert decision in text
