from __future__ import annotations

"""基于 tree-sitter 的 Java AST 符号导航。"""

from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from tree_sitter import Language, Parser

import tree_sitter_java


_SUPPORTED_EXTENSIONS = {".java"}
_SYMBOL_KIND_BY_NODE = {
    "class_declaration": "class",
    "interface_declaration": "interface",
    "enum_declaration": "enum",
    "record_declaration": "record",
    "method_declaration": "method",
    "constructor_declaration": "constructor",
}
_CONTAINER_KINDS = {"class", "interface", "enum", "record"}
_MEMBER_KINDS = {"method", "constructor"}


@dataclass(slots=True)
class JavaSymbol:
    symbolId: str
    name: str
    kind: str
    parent: str | None
    signature: str
    startLine: int
    endLine: int
    startColumn: int
    endColumn: int
    startByte: int
    endByte: int


class JavaAstSymbolExtractor:
    """提取 Java 文件中的 class/method/constructor 符号。"""

    def __init__(self, verbose: bool = False) -> None:
        self.verbose = verbose
        self._parser = Parser()
        self._parser.language = Language(tree_sitter_java.language())

    def extract(self, file_path: str) -> dict[str, Any]:
        path = Path(file_path)
        self._ensure_java_file(path)
        source = path.read_bytes()
        tree = self._parser.parse(source)
        symbols = self._collect_symbols(path, source, tree.root_node)
        return {
            "filePath": str(path),
            "language": "java",
            "hasSyntaxError": bool(tree.root_node.has_error),
            "symbols": [asdict(symbol) for symbol in symbols],
        }

    def find_symbol_at(self, file_path: str, line: int) -> dict[str, Any]:
        path = Path(file_path)
        self._ensure_java_file(path)
        data = self.extract(str(path))
        symbols = data["symbols"]
        if not symbols:
            raise ValueError(f"no symbols found in {path}")
        candidates = [symbol for symbol in symbols if symbol["startLine"] <= line <= symbol["endLine"]]
        member_candidates = [symbol for symbol in candidates if symbol["kind"] in _MEMBER_KINDS]
        target = self._smallest_symbol(member_candidates or [symbol for symbol in candidates if symbol["kind"] in _CONTAINER_KINDS])
        if target is None:
            raise ValueError(f"no symbol found at line {line} in {path}")
        source_lines = path.read_text(encoding="utf-8").splitlines()
        code_lines = [
            {"line": idx + 1, "text": source_lines[idx]}
            for idx in range(target["startLine"] - 1, min(target["endLine"], len(source_lines)))
        ]
        raw_code = "\n".join(item["text"] for item in code_lines)
        result = {
            "filePath": str(path),
            "symbol": target,
            "code": code_lines,
            "contentHash": self._content_hash(raw_code),
        }
        if self.verbose:
            print(f"[ast-symbol] file={path} line={line} target={target['name']} {target['startLine']}-{target['endLine']}")
        return result

    def _collect_symbols(self, path: Path, source: bytes, node: Any, parent: str | None = None) -> list[JavaSymbol]:
        symbols: list[JavaSymbol] = []
        initial_containers = [parent] if parent else []

        def walk(current: Any, containers: list[str]) -> None:
            kind = _SYMBOL_KIND_BY_NODE.get(current.type)
            next_containers = containers
            if kind:
                name = self._node_name(source, current)
                signature = self._node_signature(source, current, kind, name)
                symbol_parent = containers[-1] if containers else None
                symbol = JavaSymbol(
                    symbolId=f"{path}:{current.start_point[0] + 1}-{current.end_point[0] + 1}:{kind}:{signature}",
                    name=name,
                    kind=kind,
                    parent=symbol_parent,
                    signature=signature,
                    startLine=current.start_point[0] + 1,
                    endLine=current.end_point[0] + 1,
                    startColumn=current.start_point[1] + 1,
                    endColumn=current.end_point[1] + 1,
                    startByte=current.start_byte,
                    endByte=current.end_byte,
                )
                symbols.append(symbol)
                if kind in _CONTAINER_KINDS:
                    next_containers = [*containers, name]

            for child in current.children:
                walk(child, next_containers)

        walk(node, initial_containers)
        return symbols

    def _node_name(self, source: bytes, node: Any) -> str:
        for child in node.children:
            if child.type == "identifier":
                return source[child.start_byte:child.end_byte].decode("utf-8")
        return "unknown"

    def _node_signature(self, source: bytes, node: Any, kind: str, name: str) -> str:
        if kind in {"class", "interface", "enum", "record"}:
            return name
        params = []
        for child in node.children:
            if child.type == "formal_parameters":
                params = [self._node_text(source, param) for param in child.children if param.type not in {"(", ")", ","}]
                break
        params_text = ", ".join(filter(None, params))
        return f"{name}({params_text})"

    def _node_text(self, source: bytes, node: Any) -> str:
        return source[node.start_byte:node.end_byte].decode("utf-8").strip()

    def _smallest_symbol(self, symbols: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not symbols:
            return None
        return sorted(symbols, key=lambda s: (s["endLine"] - s["startLine"], s["startByte"], s["endByte"]))[0]

    def _content_hash(self, text: str) -> str:
        return f"sha256:{sha256(text.encode('utf-8')).hexdigest()}"

    def _ensure_java_file(self, path: Path) -> None:
        if path.suffix.lower() not in _SUPPORTED_EXTENSIONS:
            raise ValueError(f"unsupported file type: {path}")
        if not path.exists():
            raise FileNotFoundError(f"file not found: {path}")
