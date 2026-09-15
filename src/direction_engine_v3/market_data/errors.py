"""Market-data boundary failures."""


class MarketDataError(RuntimeError):
    """Base class for market-data failures."""


class MarketDataSchemaError(MarketDataError):
    """A source payload is missing or violates its documented schema."""


class MarketDataStaleError(MarketDataError):
    """Trading-critical data is not demonstrably fresh."""


class SequenceGapError(MarketDataError):
    """A sequence gap requires snapshot restoration before further use."""


class TransportExhaustedError(MarketDataError):
    """A bounded transport retry policy was exhausted."""


class BufferOverflowError(MarketDataError):
    """A fail-closed bounded consumer buffer overflowed."""
