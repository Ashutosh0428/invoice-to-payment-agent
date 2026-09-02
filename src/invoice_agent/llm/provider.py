from __future__ import annotations

from functools import lru_cache
from typing import Any

from invoice_agent.core.config import get_config


@lru_cache
def get_chat_model(json_mode: bool = False) -> Any:
    from langchain_ollama import ChatOllama

    cfg = get_config().llm
    return ChatOllama(
        base_url=cfg.base_url,
        model=cfg.model,
        temperature=cfg.temperature,
        num_ctx=cfg.num_ctx,
        format="json" if json_mode else None,
        client_kwargs={"timeout": cfg.request_timeout},
    )


@lru_cache
def get_llama_index_llm() -> Any:
    from llama_index.llms.ollama import Ollama

    cfg = get_config().llm
    return Ollama(
        base_url=cfg.base_url,
        model=cfg.model,
        temperature=cfg.temperature,
        request_timeout=cfg.request_timeout,
        context_window=cfg.num_ctx,
    )


@lru_cache
def get_embedding_model() -> Any:
    from llama_index.embeddings.ollama import OllamaEmbedding

    cfg = get_config().llm
    return OllamaEmbedding(
        base_url=cfg.base_url,
        model_name=cfg.embedding_model,
    )
