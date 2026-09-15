"""V3.8 proof that no bucket is promoted without historical evidence."""

import json

from direction_engine_v3.models import empty_registry


def main() -> None:
    registry = empty_registry()
    rows = [
        {
            "asset": state.bucket.asset.value,
            "calibration_version": state.readiness.calibration_version,
            "horizon": state.bucket.horizon.value,
            "ready": state.readiness.ready,
            "sample_count": state.readiness.sample_count,
        }
        for state in registry.states
    ]
    if len(rows) != 12 or any(row["ready"] for row in rows):
        raise RuntimeError("unpromoted model registry did not fail closed")
    print(json.dumps({"bucket_count": len(rows), "buckets": rows}, indent=2))


if __name__ == "__main__":
    main()
