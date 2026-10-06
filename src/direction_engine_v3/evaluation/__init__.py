"""Model and strategy evaluation."""

from direction_engine_v3.evaluation.metrics import (
    EvaluationMetrics,
    EvaluationObservation,
    evaluate,
)
from direction_engine_v3.evaluation.sol5m_go_no_go import (
    P23FrozenChallenger,
    P23Result,
    load_sol5m_dataset,
    materialize_sol5m_frozen_challenger,
    render_markdown_report,
    run_sol5m_go_no_go,
)
from direction_engine_v3.evaluation.sol5m_prospective import (
    Sol5mProspectiveResult,
    run_sol5m_prospective_evaluation,
)
from direction_engine_v3.evaluation.walk_forward import (
    PromotionPolicy,
    WalkForwardFold,
    promotion_reasons,
    walk_forward_folds,
)

__all__ = [
    "EvaluationMetrics",
    "EvaluationObservation",
    "P23FrozenChallenger",
    "P23Result",
    "PromotionPolicy",
    "Sol5mProspectiveResult",
    "WalkForwardFold",
    "evaluate",
    "load_sol5m_dataset",
    "materialize_sol5m_frozen_challenger",
    "promotion_reasons",
    "render_markdown_report",
    "run_sol5m_go_no_go",
    "run_sol5m_prospective_evaluation",
    "walk_forward_folds",
]
