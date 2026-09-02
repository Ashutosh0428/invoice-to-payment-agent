from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph
from loguru import logger

from invoice_agent.agents.nodes.decide import (
    exception_node,
    match_node,
    route_after_approval,
    route_after_match,
)
from invoice_agent.agents.nodes.enrich import (
    duplicate_check_node,
    fetch_po_node,
    persist_invoice_node,
    resolve_vendor_node,
)
from invoice_agent.agents.nodes.extract import (
    extract_invoice_node,
    extract_remittance_node,
    parse_node,
)
from invoice_agent.agents.nodes.post import failed_node, post_journal_node, rejected_node
from invoice_agent.agents.nodes.receivable import apply_cash_node, fetch_ar_items_node
from invoice_agent.agents.state import InvoiceState
from invoice_agent.core.config import get_config
from invoice_agent.schemas.common import RunStatus


def _halt_on_failure(state: InvoiceState) -> str:
    return "failed" if state.get("status") == RunStatus.FAILED or state.get("error") else "continue"


def build_ap_graph(checkpointer: Any = None) -> Any:
    graph = StateGraph(InvoiceState)

    graph.add_node("parse", parse_node)
    graph.add_node("extract_invoice", extract_invoice_node)
    graph.add_node("persist_invoice", persist_invoice_node)
    graph.add_node("duplicate_check", duplicate_check_node)
    graph.add_node("resolve_vendor", resolve_vendor_node)
    graph.add_node("fetch_po", fetch_po_node)
    graph.add_node("match", match_node)
    graph.add_node("raise_exception", exception_node)
    graph.add_node("post_journal", post_journal_node)
    graph.add_node("rejected", rejected_node)
    graph.add_node("failed", failed_node)

    graph.add_edge(START, "parse")
    graph.add_conditional_edges(
        "parse", _halt_on_failure, {"continue": "extract_invoice", "failed": "failed"}
    )
    graph.add_conditional_edges(
        "extract_invoice", _halt_on_failure, {"continue": "persist_invoice", "failed": "failed"}
    )
    graph.add_edge("persist_invoice", "duplicate_check")
    graph.add_edge("duplicate_check", "resolve_vendor")
    graph.add_edge("resolve_vendor", "fetch_po")
    graph.add_edge("fetch_po", "match")
    graph.add_conditional_edges(
        "match",
        route_after_match,
        {"post_journal": "post_journal", "raise_exception": "raise_exception", "failed": "failed"},
    )
    graph.add_conditional_edges(
        "raise_exception",
        route_after_approval,
        {"post_journal": "post_journal", "rejected": "rejected"},
    )
    graph.add_edge("post_journal", END)
    graph.add_edge("rejected", END)
    graph.add_edge("failed", END)

    return graph.compile(checkpointer=checkpointer)


def build_ar_graph(checkpointer: Any = None) -> Any:
    graph = StateGraph(InvoiceState)

    graph.add_node("parse", parse_node)
    graph.add_node("extract_remittance", extract_remittance_node)
    graph.add_node("fetch_ar_items", fetch_ar_items_node)
    graph.add_node("apply_cash", apply_cash_node)
    graph.add_node("failed", failed_node)

    graph.add_edge(START, "parse")
    graph.add_conditional_edges(
        "parse", _halt_on_failure, {"continue": "extract_remittance", "failed": "failed"}
    )
    graph.add_conditional_edges(
        "extract_remittance",
        _halt_on_failure,
        {"continue": "fetch_ar_items", "failed": "failed"},
    )
    graph.add_edge("fetch_ar_items", "apply_cash")
    graph.add_edge("apply_cash", END)
    graph.add_edge("failed", END)

    return graph.compile(checkpointer=checkpointer)


_checkpointer: Any = None
_checkpointer_cm: Any = None
_ap_graph: Any = None
_ar_graph: Any = None


async def get_checkpointer() -> Any:
    """Postgres-backed so an interrupted run survives a restart. Falls back to memory only so
    that unit tests and a database-less smoke run still execute the graph."""
    global _checkpointer, _checkpointer_cm
    if _checkpointer is not None:
        return _checkpointer

    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        _checkpointer_cm = AsyncPostgresSaver.from_conn_string(get_config().db.checkpoint_dsn)
        _checkpointer = await _checkpointer_cm.__aenter__()
        await _checkpointer.setup()
        logger.info("LangGraph checkpointer using Postgres")
    except Exception as exc:
        from langgraph.checkpoint.memory import MemorySaver

        logger.warning("Postgres checkpointer unavailable ({}), using in-memory saver", exc)
        _checkpointer = MemorySaver()

    return _checkpointer


async def get_ap_graph() -> Any:
    global _ap_graph
    if _ap_graph is None:
        _ap_graph = build_ap_graph(await get_checkpointer())
    return _ap_graph


async def get_ar_graph() -> Any:
    global _ar_graph
    if _ar_graph is None:
        _ar_graph = build_ar_graph(await get_checkpointer())
    return _ar_graph


async def close_checkpointer() -> None:
    global _checkpointer, _checkpointer_cm, _ap_graph, _ar_graph
    if _checkpointer_cm is not None:
        await _checkpointer_cm.__aexit__(None, None, None)
    _checkpointer = None
    _checkpointer_cm = None
    _ap_graph = None
    _ar_graph = None
