#!/usr/bin/env python3
"""LSP rename client for Haskell Language Server.

Starts HLS, performs a textDocument/rename at the given position,
applies the resulting WorkspaceEdit to disk, and reports what changed.

Usage:
    lsp-rename.py <file> <line> <character> <new-name> [--root DIR] [--timeout SECS]

Arguments:
    file        Path to the Haskell source file
    line        1-based line number of the symbol to rename
    character   1-based column number of the symbol to rename
    new-name    The new name for the symbol

Options:
    --root DIR       Project root (auto-detected from .cabal file if omitted)
    --timeout SECS   Max seconds to wait for HLS readiness (default: 120)
"""

import argparse
import json
import os
import select
import subprocess
import sys
import time
from pathlib import Path


# ---------------------------------------------------------------------------
# LSP message framing
# ---------------------------------------------------------------------------

def encode_message(obj: dict) -> bytes:
    body = json.dumps(obj).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    return header + body


def read_message(stdout) -> dict | None:
    """Read one LSP message. Returns None on EOF."""
    # Read headers
    headers: dict[str, str] = {}
    while True:
        raw = stdout.readline()
        if not raw:
            return None
        line = raw.decode("utf-8")
        if line == "\r\n":
            break
        if ": " in line:
            k, v = line.strip().split(": ", 1)
            headers[k] = v

    length = int(headers.get("Content-Length", 0))
    if length == 0:
        return None
    body = b""
    while len(body) < length:
        chunk = stdout.read(length - len(body))
        if not chunk:
            return None
        body += chunk
    return json.loads(body.decode("utf-8"))


# ---------------------------------------------------------------------------
# LSP client
# ---------------------------------------------------------------------------

class LSPClient:
    def __init__(self, root_path: str, timeout: int):
        self.root_path = root_path
        self.root_uri = Path(root_path).as_uri()
        self.timeout = timeout
        self._next_id = 0
        self._pending: dict[int, dict] = {}
        self._active_progress: set[str] = set()  # track in-flight progress tokens
        self.proc: subprocess.Popen | None = None

    def _id(self) -> int:
        self._next_id += 1
        return self._next_id

    # -- process management --

    def start(self):
        self.proc = subprocess.Popen(
            ["haskell-language-server-wrapper", "--lsp"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd=self.root_path,
        )

    def stop(self):
        if self.proc is None:
            return
        try:
            sid = self._id()
            self._send({"jsonrpc": "2.0", "id": sid, "method": "shutdown", "params": None})
            self._drain_until(sid, timeout=5)
        except Exception:
            pass
        try:
            self._send({"jsonrpc": "2.0", "method": "exit", "params": None})
        except Exception:
            pass
        try:
            self.proc.terminate()
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()

    # -- low-level I/O --

    def _send(self, msg: dict):
        assert self.proc and self.proc.stdin
        self.proc.stdin.write(encode_message(msg))
        self.proc.stdin.flush()

    def _read_one(self, timeout: float = 30) -> dict | None:
        """Read a single message with timeout."""
        assert self.proc and self.proc.stdout
        fd = self.proc.stdout.fileno()
        ready, _, _ = select.select([fd], [], [], timeout)
        if not ready:
            return None
        return read_message(self.proc.stdout)

    def _handle_server_request(self, msg: dict):
        """Respond to requests from the server (progress tokens, config, etc.)."""
        method = msg.get("method", "")
        msg_id = msg.get("id")
        if msg_id is None:
            return  # notification, nothing to respond to

        if method == "window/workDoneProgress/create":
            self._send({"jsonrpc": "2.0", "id": msg_id, "result": None})
        elif method == "client/registerCapability":
            self._send({"jsonrpc": "2.0", "id": msg_id, "result": None})
        elif method == "workspace/configuration":
            # Return empty config for each item requested
            items = msg.get("params", {}).get("items", [])
            self._send({"jsonrpc": "2.0", "id": msg_id, "result": [{}] * len(items)})
        else:
            # Generic OK response
            self._send({"jsonrpc": "2.0", "id": msg_id, "result": None})

    def _drain_until(self, response_id: int, timeout: float = 30) -> dict:
        """Read messages until we get the response with the given id."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            msg = self._read_one(timeout=max(0.1, remaining))
            if msg is None:
                continue

            # Is this the response we want?
            if "id" in msg and msg["id"] == response_id and ("result" in msg or "error" in msg):
                return msg

            # Server request -- respond
            if "method" in msg and "id" in msg:
                self._handle_server_request(msg)

            # Notification -- check for progress/readiness signals
            if "method" in msg and "id" not in msg:
                self._handle_notification(msg)

        raise TimeoutError(f"timed out waiting for response id={response_id}")

    def _handle_notification(self, msg: dict):
        """Track progress notifications to detect HLS readiness."""
        method = msg.get("method", "")
        params = msg.get("params", {})

        if method == "$/progress":
            token = str(params.get("token", ""))
            value = params.get("value", {})
            kind = value.get("kind")
            title = value.get("title", "")
            if kind == "begin":
                self._active_progress.add(token)
                _progress(f"  [{len(self._active_progress)} active] {title}...")
            elif kind == "report":
                message = value.get("message", "")
                pct = value.get("percentage")
                if pct is not None:
                    _progress(f"  [{len(self._active_progress)} active] {message} ({pct}%)")
            elif kind == "end":
                self._active_progress.discard(token)
                message = value.get("message", "done")
                _progress(f"  [{len(self._active_progress)} active] {message}")
        elif method == "textDocument/publishDiagnostics":
            uri = params.get("uri", "")
            diags = params.get("diagnostics", [])
            if diags:
                _progress(f"  diagnostics: {len(diags)} issue(s) in {Path(uri).name}")

    # -- high-level operations --

    def initialize(self):
        _progress("initialising HLS...")
        init_id = self._id()
        self._send({
            "jsonrpc": "2.0",
            "id": init_id,
            "method": "initialize",
            "params": {
                "processId": os.getpid(),
                "rootUri": self.root_uri,
                "rootPath": self.root_path,
                "capabilities": {
                    "workspace": {
                        "workspaceEdit": {
                            "documentChanges": True,
                        },
                    },
                    "textDocument": {
                        "rename": {
                            "prepareSupport": True,
                        },
                    },
                    "window": {
                        "workDoneProgress": True,
                    },
                },
            },
        })
        result = self._drain_until(init_id, timeout=self.timeout)
        if "error" in result:
            raise RuntimeError(f"initialize failed: {result['error']}")

        res = result.get("result", {})
        server_info = res.get("serverInfo", {})
        caps = res.get("capabilities", {})
        has_rename = bool(caps.get("renameProvider"))
        _progress(f"HLS started: {server_info.get('name', '?')} {server_info.get('version', '?')}")
        _progress(f"  rename supported: {has_rename}")
        if not has_rename:
            raise RuntimeError("HLS does not support rename -- check project setup (hie.yaml, GHC version)")

        # Send initialized notification
        self._send({"jsonrpc": "2.0", "method": "initialized", "params": {}})

    def open_file(self, file_path: str):
        uri = Path(file_path).as_uri()
        with open(file_path, "r") as f:
            text = f.read()
        self._send({
            "jsonrpc": "2.0",
            "method": "textDocument/didOpen",
            "params": {
                "textDocument": {
                    "uri": uri,
                    "languageId": "haskell",
                    "version": 1,
                    "text": text,
                },
            },
        })

    def wait_for_ready(self, file_path: str):
        """Wait for HLS to finish indexing (all progress tokens complete)."""
        _progress("waiting for HLS to index project...")
        deadline = time.monotonic() + self.timeout
        # We need to see at least one progress token start and then wait
        # for all to finish. Use a quiescence approach: once we've seen
        # progress activity and it's all settled, we're ready.
        seen_any_progress = False
        idle_since: float | None = None

        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            msg = self._read_one(timeout=min(5, max(0.1, remaining)))

            if msg is None:
                # No message for 5s
                if seen_any_progress and not self._active_progress:
                    # All progress completed and no new messages
                    break
                if idle_since is None:
                    idle_since = time.monotonic()
                elif time.monotonic() - idle_since > 15:
                    # 15s of total silence -- assume ready
                    _progress("  (no progress signals, assuming ready)")
                    break
                continue

            idle_since = None

            if "method" in msg and "id" in msg:
                self._handle_server_request(msg)
            elif "method" in msg:
                self._handle_notification(msg)
                if msg.get("method") == "$/progress":
                    seen_any_progress = True

            # Check if all progress is done
            if seen_any_progress and not self._active_progress:
                # Drain any remaining buffered messages briefly
                drain_end = time.monotonic() + 2
                while time.monotonic() < drain_end:
                    extra = self._read_one(timeout=1)
                    if extra is None:
                        break
                    if "method" in extra and "id" in extra:
                        self._handle_server_request(extra)
                    elif "method" in extra:
                        self._handle_notification(extra)
                if not self._active_progress:
                    break

        _progress(f"HLS ready (active progress tokens: {len(self._active_progress)})")

    def prepare_rename(self, file_path: str, line: int, character: int) -> dict | None:
        """Check if rename is valid at position. Returns range or None."""
        uri = Path(file_path).as_uri()
        pid = self._id()
        self._send({
            "jsonrpc": "2.0",
            "id": pid,
            "method": "textDocument/prepareRename",
            "params": {
                "textDocument": {"uri": uri},
                "position": {"line": line, "character": character},
            },
        })
        result = self._drain_until(pid, timeout=60)
        if "error" in result:
            return None
        return result.get("result")

    def rename(self, file_path: str, line: int, character: int, new_name: str) -> dict:
        """Perform the rename. Returns WorkspaceEdit."""
        uri = Path(file_path).as_uri()
        rid = self._id()
        self._send({
            "jsonrpc": "2.0",
            "id": rid,
            "method": "textDocument/rename",
            "params": {
                "textDocument": {"uri": uri},
                "position": {"line": line, "character": character},
                "newName": new_name,
            },
        })
        result = self._drain_until(rid, timeout=120)
        if "error" in result:
            raise RuntimeError(f"rename failed: {result['error']}")
        return result.get("result", {})


# ---------------------------------------------------------------------------
# Applying edits
# ---------------------------------------------------------------------------

def apply_workspace_edit(edit: dict) -> list[str]:
    """Apply a WorkspaceEdit to disk. Returns list of changed file paths."""
    # Normalise to {uri: [TextEdit]} regardless of format
    changes: dict[str, list[dict]] = {}

    if "documentChanges" in edit:
        for dc in edit["documentChanges"]:
            if "textDocument" in dc and "edits" in dc:
                uri = dc["textDocument"]["uri"]
                changes.setdefault(uri, []).extend(dc["edits"])
    elif "changes" in edit:
        changes = edit["changes"]

    files_changed: list[str] = []
    for uri, edits in changes.items():
        file_path = uri_to_path(uri)
        apply_text_edits(file_path, edits)
        files_changed.append(file_path)

    return files_changed


def uri_to_path(uri: str) -> str:
    """Convert file:// URI to filesystem path."""
    if uri.startswith("file://"):
        return uri[7:]
    return uri


def apply_text_edits(file_path: str, edits: list[dict]):
    """Apply TextEdit list to a single file (handles multi-line edits)."""
    with open(file_path, "r") as f:
        lines = f.readlines()

    # Sort edits bottom-to-top so earlier edits don't shift later positions
    edits_sorted = sorted(
        edits,
        key=lambda e: (
            e["range"]["start"]["line"],
            e["range"]["start"]["character"],
        ),
        reverse=True,
    )

    for edit in edits_sorted:
        start_line = edit["range"]["start"]["line"]
        start_char = edit["range"]["start"]["character"]
        end_line = edit["range"]["end"]["line"]
        end_char = edit["range"]["end"]["character"]
        new_text = edit["newText"]

        # Handle single-line and multi-line edits uniformly
        if start_line < len(lines):
            prefix = lines[start_line][:start_char]
        else:
            prefix = ""

        if end_line < len(lines):
            suffix = lines[end_line][end_char:]
        else:
            suffix = ""

        replacement = prefix + new_text + suffix
        # Replace the range of lines with the single replacement
        lines[start_line : end_line + 1] = [replacement]

    with open(file_path, "w") as f:
        f.writelines(lines)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def find_project_root(start: str) -> str:
    """Walk up to find directory containing a .cabal file."""
    p = Path(start).resolve()
    if p.is_file():
        p = p.parent
    while p != p.parent:
        if list(p.glob("*.cabal")):
            return str(p)
        p = p.parent
    raise FileNotFoundError("no .cabal file found in any parent directory")


def _progress(msg: str):
    print(msg, file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="LSP rename via HLS")
    parser.add_argument("file", help="Haskell source file")
    parser.add_argument("line", type=int, help="1-based line number")
    parser.add_argument("character", type=int, help="1-based column number")
    parser.add_argument("new_name", help="new symbol name")
    parser.add_argument("--root", help="project root (auto-detected if omitted)")
    parser.add_argument("--timeout", type=int, default=120, help="HLS readiness timeout in seconds")
    args = parser.parse_args()

    file_path = str(Path(args.file).resolve())
    # LSP uses 0-based positions
    line = args.line - 1
    character = args.character - 1
    root = args.root or find_project_root(file_path)

    _progress(f"project root: {root}")
    _progress(f"target: {file_path}:{args.line}:{args.character} -> {args.new_name}")

    client = LSPClient(root, args.timeout)
    client.start()

    try:
        client.initialize()
        client.open_file(file_path)
        client.wait_for_ready(file_path)

        # Prepare rename -- check position is valid
        prep = client.prepare_rename(file_path, line, character)
        if prep is None:
            _progress("error: cannot rename at this position")
            sys.exit(1)
        _progress(f"prepare rename OK: {json.dumps(prep)}")

        # Perform rename
        _progress(f"renaming to '{args.new_name}'...")
        edit = client.rename(file_path, line, character, args.new_name)

        # Apply edits
        files_changed = apply_workspace_edit(edit)
        _progress(f"applied changes to {len(files_changed)} file(s)")

        # Output machine-readable result to stdout
        result = {
            "success": True,
            "files_changed": sorted(files_changed),
            "file_count": len(files_changed),
        }
        print(json.dumps(result, indent=2))

    except Exception as e:
        _progress(f"error: {e}")
        result = {"success": False, "error": str(e)}
        print(json.dumps(result, indent=2))
        sys.exit(1)

    finally:
        client.stop()


if __name__ == "__main__":
    main()
