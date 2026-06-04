from __future__ import annotations

"""Sync Feishu wiki-space documents into local RAG documents."""

from collections import deque
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
from typing import Any
from urllib.parse import urlencode

import requests

from agent.config import AppConfig, FeishuKnowledgeConfig
from agent.ingestion.sanitizer import Sanitizer
from agent.rag.models import KnowledgeDocument


class FeishuApiError(RuntimeError):
    """Safe, actionable Feishu OpenAPI error for operator logs."""

    def __init__(
        self,
        *,
        stage: str,
        method: str,
        path: str,
        status_code: int | None = None,
        feishu_code: int | None = None,
        feishu_msg: str = "",
        body: str = "",
        context: str = "",
    ) -> None:
        self.stage = stage
        self.method = method
        self.path = path
        self.status_code = status_code
        self.feishu_code = feishu_code
        self.feishu_msg = feishu_msg
        self.body = body
        self.context = context
        super().__init__(self._format())

    def _format(self) -> str:
        parts = [
            f"stage={self.stage}",
            f"method={self.method}",
            f"path={self.path}",
        ]
        if self.context:
            parts.append(f"context={self.context}")
        if self.status_code is not None:
            parts.append(f"status={self.status_code}")
        if self.feishu_code is not None:
            parts.append(f"feishu_code={self.feishu_code}")
        if self.feishu_msg:
            parts.append(f"feishu_msg={self.feishu_msg}")
        if self.body:
            parts.append(f"body={self.body}")
        return " ".join(parts)


@dataclass(slots=True)
class FeishuRawDocument:
    token: str
    title: str
    content: str
    uri: str = ""
    updated_at: str = ""
    token_type: str = "docx"
    project: str = ""
    module: str = ""
    doc_type: str = "design_doc"


@dataclass(slots=True)
class FeishuWikiNodeRef:
    obj_token: str
    obj_type: str
    title: str
    uri: str
    path_titles: list[str]
    updated_at: str = ""


class FeishuKnowledgeClient:
    """Small, mockable Feishu OpenAPI client for wiki-space knowledge sync."""

    def __init__(self, config: FeishuKnowledgeConfig, session: Any | None = None) -> None:
        self.config = config
        self.session = session or requests.Session()
        self.base_url = config.base_url.rstrip("/")

    def fetch_documents(self) -> list[FeishuRawDocument]:
        if not self.config.enabled:
            return []
        tenant_access_token = self.get_tenant_access_token()
        documents: list[FeishuRawDocument] = []
        seen_tokens: set[str] = set()
        for space_id in self.config.wiki_space_ids:
            for node_ref in self._iter_wiki_document_refs(space_id, tenant_access_token):
                if node_ref.obj_token in seen_tokens:
                    continue
                seen_tokens.add(node_ref.obj_token)
                documents.append(self._fetch_wiki_document(node_ref, tenant_access_token))
        return documents

    def get_tenant_access_token(self) -> str:
        if not self.config.app_id.strip() or not self.config.app_secret.strip():
            raise RuntimeError("missing feishu knowledge app credentials")
        data = self._post_json(
            "/auth/v3/tenant_access_token/internal",
            json_body={"app_id": self.config.app_id, "app_secret": self.config.app_secret},
            headers={},
            stage="tenant_access_token",
        )
        token = str(data.get("tenant_access_token") or data.get("data", {}).get("tenant_access_token") or "")
        if not token:
            raise RuntimeError("missing tenant_access_token in feishu response")
        return token

    def _fetch_wiki_document(self, node_ref: FeishuWikiNodeRef, tenant_access_token: str) -> FeishuRawDocument:
        content_payload = self._fetch_document_content(node_ref.obj_token, tenant_access_token, token_type=node_ref.obj_type)
        content = self._extract_content(content_payload)
        if not content.strip():
            content = json.dumps(content_payload, ensure_ascii=False, indent=2)
        return FeishuRawDocument(
            token=node_ref.obj_token,
            title=node_ref.title,
            content=content,
            uri=node_ref.uri,
            updated_at=node_ref.updated_at or datetime.utcnow().isoformat(),
            token_type=node_ref.obj_type,
            module=self._infer_module(node_ref.path_titles),
            doc_type=self._infer_doc_type(node_ref.path_titles),
        )

    def _fetch_document_content(self, token: str, tenant_access_token: str, *, token_type: str) -> dict[str, Any]:
        context = f"token_hash={self._safe_hash(token)} token_type={token_type}"
        if token_type == "doc":
            return self._get_json(f"/doc/v2/{token}/raw_content", tenant_access_token, stage="fetch_doc_raw_content", context=context)
        return self._get_json(
            f"/docx/v1/documents/{token}/raw_content",
            tenant_access_token,
            stage="fetch_doc_raw_content",
            context=context,
        )

    def _iter_wiki_document_refs(self, space_id: str, tenant_access_token: str) -> list[FeishuWikiNodeRef]:
        refs: list[FeishuWikiNodeRef] = []
        queue: deque[tuple[str, list[str]]] = deque([("", [])])
        visited_parents: set[str] = set()
        while queue:
            parent_node_token, parent_path = queue.popleft()
            parent_key = parent_node_token or "<root>"
            if parent_key in visited_parents:
                continue
            visited_parents.add(parent_key)
            for node in self._list_wiki_space_child_nodes(space_id, tenant_access_token, parent_node_token=parent_node_token):
                title = str(node.get("title") or "")
                path_titles = [*parent_path, title] if title else parent_path[:]
                node_token = str(node.get("node_token") or "")
                if bool(node.get("has_child")) and node_token:
                    queue.append((node_token, path_titles))
                node_ref = self._node_ref(space_id, node, path_titles)
                if node_ref is not None:
                    refs.append(node_ref)
        return refs

    def _list_wiki_space_child_nodes(self, space_id: str, tenant_access_token: str, *, parent_node_token: str = "") -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page_token = ""
        while True:
            params: dict[str, Any] = {"page_size": 50}
            if parent_node_token:
                params["parent_node_token"] = parent_node_token
            if page_token:
                params["page_token"] = page_token
            context = f"space_hash={self._safe_hash(space_id)} parent={self._safe_parent(parent_node_token)}"
            payload = self._get_json(
                f"/wiki/v2/spaces/{space_id}/nodes",
                tenant_access_token,
                params=params,
                stage="list_wiki_nodes",
                context=context,
            )
            data = payload.get("data", {}) if isinstance(payload.get("data"), dict) else {}
            raw_items = data.get("items") or payload.get("items") or []
            items.extend(item for item in raw_items if isinstance(item, dict))
            has_more = bool(data.get("has_more") or payload.get("has_more"))
            page_token = str(data.get("page_token") or payload.get("page_token") or "")
            if not has_more or not page_token:
                break
        return items

    def _node_ref(self, space_id: str, node: dict[str, Any], path_titles: list[str]) -> FeishuWikiNodeRef | None:
        obj_type = str(node.get("obj_type") or "").strip().lower()
        if obj_type not in {"docx", "doc"}:
            return None
        obj_token = str(node.get("obj_token") or "").strip()
        if not obj_token:
            return None
        title = str(node.get("title") or obj_token)
        node_token = str(node.get("node_token") or "")
        uri = f"feishu://wiki/{space_id}/{node_token}" if node_token else f"feishu://wiki/{space_id}"
        updated_at = str(node.get("updated_time") or node.get("update_time") or "")
        return FeishuWikiNodeRef(obj_token=obj_token, obj_type=obj_type, title=title, uri=uri, path_titles=path_titles, updated_at=updated_at)

    def _post_json(self, path: str, *, json_body: dict[str, Any], headers: dict[str, str], stage: str) -> dict[str, Any]:
        response = self.session.post(f"{self.base_url}{path}", json=json_body, headers=headers, timeout=20)
        return self._decode_response(response, stage=stage, method="POST", path=path)

    def _get_json(
        self,
        path: str,
        tenant_access_token: str,
        *,
        params: dict[str, Any] | None = None,
        stage: str,
        context: str = "",
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        if params:
            url = f"{url}?{urlencode({key: value for key, value in params.items() if value != ''})}"
        response = self.session.get(url, headers={"Authorization": f"Bearer {tenant_access_token}"}, timeout=20)
        safe_context = self._append_safe_params(context, params)
        return self._decode_response(response, stage=stage, method="GET", path=path, context=safe_context)

    def _decode_response(self, response: Any, *, stage: str, method: str, path: str, context: str = "") -> dict[str, Any]:
        safe_path = self._safe_path(path)
        status_code = int(getattr(response, "status_code", 0) or 0)
        data: Any = {}
        body = self._response_text(response)
        try:
            data = response.json()
        except Exception:
            data = {}

        feishu_code = None
        feishu_msg = ""
        if isinstance(data, dict):
            raw_code = data.get("code")
            if raw_code is not None:
                try:
                    feishu_code = int(raw_code)
                except (TypeError, ValueError):
                    feishu_code = None
            feishu_msg = str(data.get("msg") or data.get("message") or "")
            if not body:
                body = json.dumps(data, ensure_ascii=False)

        if status_code >= 400:
            raise FeishuApiError(
                stage=stage,
                method=method,
                path=safe_path,
                status_code=status_code,
                feishu_code=feishu_code,
                feishu_msg=feishu_msg,
                body=self._truncate(body),
                context=context,
            )
        if feishu_code not in (None, 0):
            raise FeishuApiError(
                stage=stage,
                method=method,
                path=safe_path,
                status_code=status_code or None,
                feishu_code=feishu_code,
                feishu_msg=feishu_msg or "feishu api error",
                body=self._truncate(body),
                context=context,
            )
        return data if isinstance(data, dict) else {}

    def _response_text(self, response: Any) -> str:
        text = getattr(response, "text", "")
        if isinstance(text, bytes):
            return text.decode("utf-8", errors="replace")
        return str(text or "")

    def _truncate(self, text: str, limit: int = 600) -> str:
        compact = " ".join(str(text).split())
        return compact if len(compact) <= limit else f"{compact[:limit]}..."

    def _safe_path(self, path: str) -> str:
        parts = [part for part in path.split("/") if part]
        if len(parts) >= 4 and parts[:3] == ["docx", "v1", "documents"]:
            parts[3] = "[TOKEN]"
        elif len(parts) >= 3 and parts[:2] == ["doc", "v2"]:
            parts[2] = "[TOKEN]"
        elif len(parts) >= 4 and parts[:3] == ["wiki", "v2", "spaces"]:
            parts[3] = "[SPACE]"
        return "/" + "/".join(parts)

    def _append_safe_params(self, context: str, params: dict[str, Any] | None) -> str:
        if not params:
            return context
        safe_params: list[str] = []
        for key, value in params.items():
            if value == "":
                continue
            if "token" in key.lower():
                safe_value = self._safe_hash(str(value))
            else:
                safe_value = str(value)
            safe_params.append(f"{key}={safe_value}")
        params_text = ",".join(safe_params)
        if not params_text:
            return context
        return f"{context} params={params_text}".strip()

    def _safe_hash(self, value: str) -> str:
        return sha256(value.encode("utf-8")).hexdigest()[:12] if value else "root"

    def _safe_parent(self, parent_node_token: str) -> str:
        return "root" if not parent_node_token else self._safe_hash(parent_node_token)

    def _extract_content(self, payload: dict[str, Any]) -> str:
        data = payload.get("data", {}) if isinstance(payload.get("data"), dict) else {}
        for source in [data, payload]:
            for key in ["content", "raw_content", "text", "markdown"]:
                value = source.get(key) if isinstance(source, dict) else None
                if isinstance(value, str) and value.strip():
                    return value
        return ""

    def _infer_doc_type(self, path_titles: list[str]) -> str:
        text = " / ".join(path_titles).lower()
        checks = [
            ("error_code_doc", ["error-code", "error code", "errorcode", "\u9519\u8bef\u7801", "\u5f02\u5e38\u7801"]),
            ("api_doc", ["api", "openapi", "interface", "\u63a5\u53e3"]),
            ("db_doc", ["db", "database", "schema", "\u6570\u636e\u5e93", "\u8868\u7ed3\u6784"]),
            ("product_doc", ["product", "prd", "\u4ea7\u54c1", "\u9700\u6c42"]),
            ("module_doc", ["module", "\u6a21\u5757", "\u804c\u8d23"]),
            ("design_doc", ["design", "\u8bbe\u8ba1", "\u65b9\u6848", "\u67b6\u6784"]),
        ]
        for doc_type, keywords in checks:
            if any(keyword in text for keyword in keywords):
                return doc_type
        return "design_doc"

    def _infer_module(self, path_titles: list[str]) -> str:
        text = " / ".join(path_titles).lower()
        module_keywords = {
            "order": ["order", "\u8ba2\u5355"],
            "payment": ["payment", "\u652f\u4ed8"],
            "user": ["user", "\u7528\u6237"],
            "common": ["common", "\u516c\u5171", "\u901a\u7528", "\u9519\u8bef\u7801", "\u5f02\u5e38\u7801"],
        }
        for module, keywords in module_keywords.items():
            if any(keyword in text for keyword in keywords):
                return module
        return ""


class FeishuLoader:
    """Convert Feishu wiki-space documents into sanitized KnowledgeDocument objects."""

    def __init__(
        self,
        *,
        config: AppConfig,
        client: FeishuKnowledgeClient | None = None,
        sanitizer: Sanitizer | None = None,
    ) -> None:
        self.config = config
        self.feishu_config = config.feishu_knowledge
        self.client = client or FeishuKnowledgeClient(self.feishu_config)
        self.sanitizer = sanitizer or Sanitizer()

    def load(self) -> list[KnowledgeDocument]:
        if not self.feishu_config.enabled:
            return []
        return [self._to_document(raw) for raw in self.client.fetch_documents()]

    def _to_document(self, raw: FeishuRawDocument) -> KnowledgeDocument:
        content = self.sanitizer.sanitize(raw.content).strip()
        project = raw.project or self.config.project.name
        module = raw.module or ""
        doc_type = raw.doc_type or "design_doc"
        token_hash = sha256(raw.token.encode("utf-8")).hexdigest()[:16]
        content_hash = f"sha256:{sha256(content.encode('utf-8')).hexdigest()}"
        return KnowledgeDocument(
            doc_id=f"feishu:{token_hash}",
            source="feishu",
            doc_type=doc_type,
            project=project,
            module=module,
            title=raw.title,
            content=content,
            uri=raw.uri,
            updated_at=raw.updated_at or datetime.utcnow().isoformat(),
            content_hash=content_hash,
            metadata={
                "source": "feishu",
                "doc_type": doc_type,
                "project": project,
                "module": module,
                "title": raw.title,
                "uri": raw.uri,
                "updated_at": raw.updated_at,
                "token_hash": token_hash,
                "token_type": raw.token_type,
                "authority": "inferred",
            },
        )
