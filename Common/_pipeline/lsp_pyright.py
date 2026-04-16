"""Minimal pyright-langserver LSP client.

Used by run_aider.py to resolve symbol locations in installed packages
(PyQt6, pynvml, pyqtgraph, etc.) that ctags can't index because they're
compiled extensions or shipped as .pyi stubs. Pyright consults the
active venv's site-packages + typeshed, giving semantically accurate
answers to 'which module does X live in?'.

Lifecycle:
    client = PyrightClient(workspace_root)
    client.start()
    try:
        hits = client.resolve_symbols(['QShowEvent', 'QCloseEvent'])
    finally:
        client.shutdown()

Protocol: JSON-RPC 2.0 framed with `Content-Length: N\\r\\n\\r\\n{json}`
headers, sent over pyright-langserver's stdin/stdout.
"""
from __future__ import annotations

import json
import queue
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any


# LSP SymbolKind enum -> human-readable (pyright returns integer kinds).
_SYMBOL_KIND = {
    1: "file", 2: "module", 3: "namespace", 4: "package", 5: "class",
    6: "method", 7: "property", 8: "field", 9: "constructor", 10: "enum",
    11: "interface", 12: "function", 13: "variable", 14: "constant",
    15: "string", 16: "number", 17: "boolean", 18: "array", 19: "object",
    20: "key", 21: "null", 22: "enum-member", 23: "struct", 24: "event",
    25: "operator", 26: "type-parameter",
}


def pyright_available() -> bool:
    return shutil.which("pyright-langserver") is not None


class LSPError(RuntimeError):
    pass


class PyrightClient:
    """Thin LSP client tailored to pyright-langserver's symbol lookup.

    Not a general-purpose LSP client: only the methods we need for
    resolve_symbols() are implemented."""

    def __init__(self, workspace_root: Path, timeout: float = 30.0) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self.timeout = timeout
        self._proc: subprocess.Popen | None = None
        self._reader_thread: threading.Thread | None = None
        self._responses: dict[int, dict] = {}
        self._responses_lock = threading.Lock()
        self._response_arrived = threading.Event()
        self._shutdown = threading.Event()
        self._next_id = 1
        self._next_id_lock = threading.Lock()

    # ── framing ──────────────────────────────────────────────────

    def _send(self, method: str, params: dict, notification: bool = False) -> int | None:
        if self._proc is None or self._proc.stdin is None:
            raise LSPError("pyright not started")
        msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "params": params}
        if not notification:
            with self._next_id_lock:
                msg_id = self._next_id
                self._next_id += 1
            msg["id"] = msg_id
        else:
            msg_id = None
        data = json.dumps(msg).encode("utf-8")
        header = f"Content-Length: {len(data)}\r\n\r\n".encode("ascii")
        self._proc.stdin.write(header + data)
        self._proc.stdin.flush()
        return msg_id

    def _reader_loop(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        stream = self._proc.stdout
        while not self._shutdown.is_set():
            # Read headers.
            length = None
            while True:
                line = stream.readline()
                if not line:
                    return  # EOF: pyright exited
                line = line.decode("ascii", errors="replace").strip()
                if not line:
                    break
                if line.lower().startswith("content-length:"):
                    length = int(line.split(":", 1)[1].strip())
            if length is None:
                continue
            body = stream.read(length)
            if not body:
                return
            try:
                msg = json.loads(body.decode("utf-8"))
            except json.JSONDecodeError:
                continue
            # Only interested in responses (have 'id' and 'result' or 'error').
            if "id" in msg and ("result" in msg or "error" in msg):
                with self._responses_lock:
                    self._responses[msg["id"]] = msg
                self._response_arrived.set()
            # Notifications from server (diagnostics, progress) are ignored.

    def _await_response(self, msg_id: int) -> dict:
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            with self._responses_lock:
                if msg_id in self._responses:
                    return self._responses.pop(msg_id)
            self._response_arrived.wait(timeout=0.1)
            self._response_arrived.clear()
        raise LSPError(f"LSP request id={msg_id} timed out after {self.timeout}s")

    def _request(self, method: str, params: dict) -> Any:
        msg_id = self._send(method, params)
        assert msg_id is not None
        resp = self._await_response(msg_id)
        if "error" in resp:
            raise LSPError(f"{method}: {resp['error']}")
        return resp.get("result")

    # ── lifecycle ────────────────────────────────────────────────

    def start(self) -> None:
        if not pyright_available():
            raise LSPError("pyright-langserver not found on PATH. "
                           "Install with: pip install pyright")
        self._proc = subprocess.Popen(
            ["pyright-langserver", "--stdio"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread.start()

        root_uri = self.workspace_root.as_uri()
        init_params = {
            "processId": None,
            "rootUri": root_uri,
            "capabilities": {
                "workspace": {"symbol": {"dynamicRegistration": False}},
                "textDocument": {"documentSymbol": {"dynamicRegistration": False}},
            },
            "workspaceFolders": [{"uri": root_uri, "name": self.workspace_root.name}],
            "initializationOptions": {},
        }
        self._request("initialize", init_params)
        self._send("initialized", {}, notification=True)

    def shutdown(self) -> None:
        if self._proc is None:
            return
        try:
            self._request("shutdown", {})
        except Exception:  # noqa: BLE001
            pass
        try:
            self._send("exit", {}, notification=True)
        except Exception:  # noqa: BLE001
            pass
        self._shutdown.set()
        try:
            self._proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self._proc.kill()
        self._proc = None

    # ── symbol queries ───────────────────────────────────────────

    def workspace_symbol(self, query: str) -> list[dict]:
        """Query the workspace for symbols matching `query` (substring match).

        Returns raw LSP SymbolInformation[] entries. Pyright also returns
        symbols from installed third-party packages since they're on the
        import path — which is exactly what we want."""
        result = self._request("workspace/symbol", {"query": query})
        return result or []

    def resolve_symbols(self, names: list[str]) -> dict[str, list[tuple[str, str]]]:
        """For each name, return [(container_or_file, kind), ...].

        Filters to exact-name matches (LSP workspace/symbol does substring
        matching, which would flood the result with near-misses).
        """
        out: dict[str, list[tuple[str, str]]] = {}
        for name in names:
            if not name or len(name) < 2:
                continue
            try:
                hits = self.workspace_symbol(name)
            except LSPError:
                continue
            matches: list[tuple[str, str]] = []
            seen: set[tuple[str, str]] = set()
            for hit in hits:
                if hit.get("name") != name:
                    continue  # substring noise
                kind_num = hit.get("kind", 0)
                kind = _SYMBOL_KIND.get(kind_num, f"k{kind_num}")
                container = hit.get("containerName") or ""
                if not container:
                    # Derive from URI if no containerName.
                    uri = hit.get("location", {}).get("uri", "")
                    container = uri.rsplit("/", 1)[-1].removesuffix(".py").removesuffix(".pyi")
                key = (container, kind)
                if key in seen:
                    continue
                seen.add(key)
                matches.append(key)
            if matches:
                out[name] = matches
        return out


def build_external_symbol_block(names: list[str], workspace_root: Path) -> str:
    """Convenience wrapper: start pyright, resolve names, format a prompt
    block, shut down. Intended for one-shot use; for multi-step runs reuse
    a PyrightClient across steps to avoid the ~3s startup cost."""
    if not pyright_available():
        return ""
    client = PyrightClient(workspace_root)
    try:
        client.start()
        hits = client.resolve_symbols(names)
    finally:
        client.shutdown()
    return format_resolved(hits)


def format_resolved(hits: dict[str, list[tuple[str, str]]]) -> str:
    if not hits:
        return ""
    lines = [
        "## Verified Symbol Locations (from pyright)",
        "",
        "These symbols exist in installed packages / the current workspace.",
        "Use the exact container shown when writing imports:",
        "",
    ]
    for name in sorted(hits):
        for container, kind in hits[name]:
            lines.append(f"- `{name}` ({kind}) -> {container}")
    return "\n".join(lines) + "\n"
