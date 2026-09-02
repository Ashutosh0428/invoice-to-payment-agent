from __future__ import annotations

from typing import Any

from loguru import logger

from invoice_agent.core.config import get_config
from invoice_agent.schemas.erp import Vendor

VENDOR_TABLE = "kb_vendor_master"
POLICY_TABLE = "kb_ap_policy"


class KnowledgeBase:
    """LlamaIndex over pgvector, split into two tables.

    Vendor master and AP policy are indexed separately because they answer different questions
    and a single blended top-k lets policy prose outrank the vendor row you actually need.
    """

    def __init__(self) -> None:
        self._cfg = get_config()
        self._indexes: dict[str, Any] = {}
        self._available = True

    def _vector_store(self, table: str) -> Any:
        from llama_index.vector_stores.postgres import PGVectorStore

        db = self._cfg.db
        return PGVectorStore.from_params(
            database=db.name,
            host=db.host,
            password=db.password.get_secret_value(),
            port=str(db.port),
            user=db.user,
            table_name=table,
            embed_dim=self._cfg.llm.embedding_dim,
            hybrid_search=True,
            text_search_config="english",
        )

    def _index(self, table: str) -> Any:
        if table in self._indexes:
            return self._indexes[table]

        from llama_index.core import StorageContext, VectorStoreIndex

        from invoice_agent.llm.provider import get_embedding_model

        store = self._vector_store(table)
        storage = StorageContext.from_defaults(vector_store=store)
        index = VectorStoreIndex.from_vector_store(
            vector_store=store,
            storage_context=storage,
            embed_model=get_embedding_model(),
        )
        self._indexes[table] = index
        return index

    def index_vendors(self, vendors: list[Vendor]) -> int:
        from llama_index.core import Document

        documents = [
            Document(
                text=(
                    f"Vendor {vendor.name} (id {vendor.vendor_id}). "
                    f"Also known as: {', '.join(vendor.aliases) or 'none'}. "
                    f"Tax id {vendor.tax_id or 'unknown'}. IBAN {vendor.iban or 'unknown'}. "
                    f"Payment terms {vendor.payment_terms or 'unknown'}, "
                    f"currency {vendor.currency}."
                ),
                metadata={
                    "vendor_id": vendor.vendor_id,
                    "name": vendor.name,
                    "tax_id": vendor.tax_id or "",
                    "iban": vendor.iban or "",
                    "kind": "vendor",
                },
            )
            for vendor in vendors
        ]
        return self._ingest(VENDOR_TABLE, documents)

    def index_policies(self, policies: list[dict[str, str]]) -> int:
        from llama_index.core import Document

        documents = [
            Document(
                text=policy["text"],
                metadata={"title": policy.get("title", ""), "kind": "policy"},
            )
            for policy in policies
        ]
        return self._ingest(POLICY_TABLE, documents)

    def _ingest(self, table: str, documents: list[Any]) -> int:
        if not documents:
            return 0
        try:
            index = self._index(table)
            for document in documents:
                index.insert(document)
            logger.info("Indexed {} documents into {}", len(documents), table)
            return len(documents)
        except Exception as exc:
            self._available = False
            logger.warning("Knowledge base ingest into {} failed: {}", table, exc)
            return 0

    def search_vendors(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        return self._search(VENDOR_TABLE, query, top_k)

    def search_policy(self, query: str, top_k: int = 3) -> list[dict[str, Any]]:
        return self._search(POLICY_TABLE, query, top_k)

    def _search(self, table: str, query: str, top_k: int) -> list[dict[str, Any]]:
        if not self._available:
            return []
        try:
            retriever = self._index(table).as_retriever(similarity_top_k=top_k)
            nodes = retriever.retrieve(query)
        except Exception as exc:
            logger.warning("Knowledge base search on {} failed: {}", table, exc)
            return []

        return [
            {
                "text": node.node.get_content(),
                "score": float(node.score or 0.0),
                "metadata": dict(node.node.metadata or {}),
            }
            for node in nodes
        ]


_kb: KnowledgeBase | None = None


def get_knowledge_base() -> KnowledgeBase:
    global _kb
    if _kb is None:
        _kb = KnowledgeBase()
    return _kb
