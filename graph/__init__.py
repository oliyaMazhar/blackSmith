"""Graph package for BlackSmith.

Exports the graph builder, state type, initial state factory, and pipeline runner.
"""

from graph.state import WorkflowState, make_initial_state
from graph.workflow import build_support_graph
from graph.runner import run_support_ticket, TicketRunResult

__all__ = [
    "WorkflowState",
    "make_initial_state",
    "build_support_graph",
    "run_support_ticket",
    "TicketRunResult",
]
