from __future__ import annotations

from loguru import logger

from invoice_agent.core.config import get_config

_initialised = False


def setup_tracing() -> None:
    """Wire OpenInference -> OTLP -> Arize Phoenix. Never fatal: tracing is not the product."""
    global _initialised
    cfg = get_config().observability
    if _initialised or not cfg.enabled:
        return

    try:
        from openinference.instrumentation.langchain import LangChainInstrumentor
        from openinference.instrumentation.llama_index import LlamaIndexInstrumentor
        from phoenix.otel import register

        provider = register(
            project_name=cfg.project_name,
            endpoint=cfg.phoenix_endpoint,
            batch=True,
            set_global_tracer_provider=True,
        )
        LangChainInstrumentor().instrument(tracer_provider=provider, skip_dep_check=True)
        LlamaIndexInstrumentor().instrument(tracer_provider=provider, skip_dep_check=True)
        _initialised = True
        logger.info("Phoenix tracing enabled at {}", cfg.phoenix_endpoint)
    except Exception as exc:
        logger.warning("Phoenix tracing disabled: {}", exc)


def get_tracer(name: str):  # type: ignore[no-untyped-def]
    from opentelemetry import trace

    return trace.get_tracer(name)
