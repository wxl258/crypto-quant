"""
Trading Workflow Orchestrator — Manages the full trading pipeline.

Coordinates: data → agents → decision → execution → review → feedback loop.
Replaces simple sequential execution with a robust pipeline.
"""
import logging
from datetime import datetime
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class WorkflowState:
    """State passed between workflow stages."""
    symbol: str = ""
    timestamp: datetime = None
    price: float = 0.0
    technical: Dict = field(default_factory=dict)
    risk: Dict = field(default_factory=dict)
    decision: Dict = field(default_factory=dict)
    order: Optional[Dict] = None
    review: Dict = field(default_factory=dict)
    feedback: Dict = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)


class WorkflowOrchestrator:
    """Orchestrates the full trading pipeline with feedback loop.
    
    Pipeline: Data → TechnicalAgent → RiskAgent → DecisionAgent → Execution → ReviewAgent → Feedback
    """

    def __init__(self):
        self._stages = [
            ('data', self._stage_data),
            ('technical', self._stage_technical),
            ('risk', self._stage_risk),
            ('decision', self._stage_decision),
            ('review', self._stage_review),
            ('feedback', self._stage_feedback),
        ]
        self._trade_history: List[Dict] = []
        self._performance_metrics: Dict[str, Any] = {}
        self._workflow_count = 0
        self._error_count = 0

    def run(self, state: WorkflowState) -> WorkflowState:
        """Execute the full pipeline."""
        self._workflow_count += 1

        for stage_name, stage_fn in self._stages:
            try:
                state = stage_fn(state)
            except Exception as e:
                state.errors.append(f"{stage_name}: {e}")
                self._error_count += 1
                logger.error(f"Workflow stage '{stage_name}' failed: {e}")
                # Continue to next stage even on error (graceful degradation)

        return state

    def _stage_data(self, state: WorkflowState) -> WorkflowState:
        """Stage 1: Data validation and enrichment."""
        if state.price <= 0:
            state.errors.append("data: invalid price")
        state.timestamp = state.timestamp or datetime.now()
        return state

    def _stage_technical(self, state: WorkflowState) -> WorkflowState:
        """Stage 2: Technical analysis (handled by TechnicalAgent externally)."""
        # This stage is filled by the strategy calling TechnicalAgent.analyze()
        return state

    def _stage_risk(self, state: WorkflowState) -> WorkflowState:
        """Stage 3: Risk evaluation."""
        if state.risk.get('approved') is False:
            state.decision = {'action': 'HOLD', 'reason': state.risk.get('reason', 'Risk rejected')}
        return state

    def _stage_decision(self, state: WorkflowState) -> WorkflowState:
        """Stage 4: Final decision — already computed by DecisionAgent."""
        return state

    def _stage_review(self, state: WorkflowState) -> WorkflowState:
        """Stage 5: Post-trade review."""
        if state.order:
            self._trade_history.append({
                'timestamp': state.timestamp,
                'symbol': state.symbol,
                'action': state.decision.get('action', 'HOLD'),
                'price': state.price,
                'confidence': state.decision.get('confidence', 0),
                'technical_signal': state.technical.get('signal', 'N/A'),
                'risk_approved': state.risk.get('approved', False),
                'pnl': state.order.get('pnl'),
            })
        return state

    def _stage_feedback(self, state: WorkflowState) -> WorkflowState:
        """Stage 6: Feedback loop — adjust weights based on performance."""
        if len(self._trade_history) >= 10:
            recent = self._trade_history[-20:]
            wins = [t for t in recent if (t.get('pnl') or 0) > 0]
            losses = [t for t in recent if (t.get('pnl') or 0) < 0]

            total = len(wins) + len(losses)
            if total > 0:
                win_rate = len(wins) / total
                avg_win = sum(t.get('pnl', 0) for t in wins) / len(wins) if wins else 0
                avg_loss = abs(sum(t.get('pnl', 0) for t in losses) / len(losses)) if losses else 0

                state.feedback = {
                    'win_rate': round(win_rate, 3),
                    'avg_win': round(avg_win, 2),
                    'avg_loss': round(avg_loss, 2),
                    'profit_factor': round(
                        sum(t.get('pnl', 0) for t in wins) / abs(sum(t.get('pnl', 0) for t in losses)), 2
                    ) if losses else None,
                    'total_trades': total,
                    'suggestion': self._generate_suggestion(win_rate, avg_win, avg_loss),
                }

        return state

    def _generate_suggestion(self, win_rate: float, avg_win: float, avg_loss: float) -> str:
        """Generate improvement suggestions based on performance."""
        suggestions = []
        if win_rate < 0.35:
            suggestions.append("胜率过低，建议提高入场信号阈值")
        if avg_loss > avg_win * 2:
            suggestions.append("均亏远大于均盈，建议收紧止损或放宽止盈")
        if win_rate > 0.6 and avg_win < avg_loss:
            suggestions.append("高胜率但盈亏比倒挂，建议让利润奔跑(放宽止盈)")
        return "; ".join(suggestions) if suggestions else "策略运行正常"

    def get_status(self) -> Dict:
        """Get workflow status summary."""
        return {
            'workflow_count': self._workflow_count,
            'error_count': self._error_count,
            'trade_count': len(self._trade_history),
            'recent_trades': self._trade_history[-5:],
            'latest_feedback': self._trade_history[-1].get('feedback', {}) if self._trade_history else {},
        }
