"""Claude Code CLI wrapper.

Handles account switching (CLAUDE_CONFIG_DIR), the ultrathink prefix,
and model routing (sonnet / opus / explicit tag). Used by the coding
mode; debug mode no longer calls Claude after the Step 5 migration to
a local LLM.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path  # noqa: F401  (used in type hint string)


# Map of account identifiers to their CLAUDE_CONFIG_DIR locations.
# Extend this dict if more accounts are added.
ACCOUNT_CONFIG_DIRS: dict[str, str] = {
    "claude1": str(Path.home() / ".clauderivalon"),
    "claude2": str(Path.home() / ".claudefksogbetun"),
}


class ClaudeError(RuntimeError):
    """Raised when the `claude` CLI exits non-zero."""


def resolve_account_dir(account: str) -> str:
    key = account.lower()
    if key not in ACCOUNT_CONFIG_DIRS:
        valid = ", ".join(sorted(ACCOUNT_CONFIG_DIRS))
        raise ClaudeError(f"Unknown Claude account '{account}'. Expected one of: {valid}")
    return ACCOUNT_CONFIG_DIRS[key]


def _extract_text_delta(event: dict) -> str:
    """Pull assistant text content out of a Claude Code stream-json event.
    Returns '' for events without displayable text (thinking deltas,
    signature deltas, tool events, system messages, result envelopes).

    Only `content_block_delta` events with `delta.type == "text_delta"`
    represent assistant-visible prose. Thinking blocks are internal
    reasoning and must NOT land in the audit artefact — they'd poison
    the resume prompt on re-run."""
    if event.get("type") != "stream_event":
        return ""
    inner = event.get("event", {})
    if inner.get("type") != "content_block_delta":
        return ""
    delta = inner.get("delta", {})
    if delta.get("type") == "text_delta":
        return delta.get("text", "")
    return ""


def invoke_claude(
    prompt: str,
    *,
    model: str,
    account: str = "Claude1",
    output_format: str = "text",
    permission_mode: str | None = None,
    stream_to: "Path | None" = None,
) -> str:
    """Run `claude -p --model <m>` with `prompt` piped via stdin.
    Returns the captured assistant text. Raises ClaudeError on failure.

    `-p / --print` is always passed: without it, `--output-format` is
    silently ignored by the CLI and Claude starts an interactive session
    on the piped stdin, producing unspecified output. That caused long
    invocations to leave only the CLI's error banner on disk when the
    rate limit hit mid-session.

    `permission_mode` (default None): when set, passed to the CLI as
    `--permission-mode <value>`. Use `"acceptEdits"` for review stages
    (2c / 3c) that need Claude to auto-approve Edit tool use — the
    pipeline runs non-interactively so there's no human available to
    answer permission prompts. Leave unset for text-only stages (0 / 1)
    where Claude produces prose only and never invokes Edit.

    `stream_to` (default None): when set to a Path, Claude is invoked
    with `--output-format stream-json --verbose --include-partial-messages`
    and each assistant `text_delta` is parsed out of the NDJSON and
    written to that file as it arrives. On non-zero exit (rate limit,
    CLI crash), every delta that already landed is preserved on disk —
    letting the caller's resume-on-rate-limit flow continue from real
    audit content rather than a banner. When None, behaviour is batch
    `--output-format text` with `subprocess.run` and a single capture."""
    env = os.environ.copy()
    env["CLAUDE_CONFIG_DIR"] = resolve_account_dir(account)

    cmd = ["claude", "-p", "--model", model]
    if permission_mode:
        cmd += ["--permission-mode", permission_mode]

    if stream_to is None:
        cmd += ["--output-format", output_format]
        proc = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
        )
        if proc.returncode != 0:
            raise ClaudeError(
                f"claude CLI failed (exit {proc.returncode}). "
                f"stderr:\n{proc.stderr}"
            )
        return proc.stdout.strip()

    # Streaming path: stream-json NDJSON parsed per-delta. stream-json
    # requires --verbose; --include-partial-messages emits text_delta
    # events as the model generates them (rather than only at
    # content_block_stop), so a rate-limit mid-response leaves real
    # audit text on disk, not just a banner.
    cmd += [
        "--output-format", "stream-json",
        "--verbose",
        "--include-partial-messages",
    ]

    collected: list[str] = []
    with subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=env,
        bufsize=1,
    ) as proc:
        assert proc.stdin is not None
        proc.stdin.write(prompt)
        proc.stdin.close()

        assert proc.stdout is not None
        with stream_to.open("a", encoding="utf-8") as out:
            for line in proc.stdout:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    event = json.loads(stripped)
                except json.JSONDecodeError:
                    # Non-JSON line — preserve verbatim. Could be a CLI
                    # error banner ("You've hit your limit..."); the
                    # caller's banner-stripping logic discards it when
                    # building the resume prompt.
                    out.write(line)
                    out.flush()
                    collected.append(line)
                    print(line, end="", flush=True)
                    continue
                text = _extract_text_delta(event)
                if text:
                    out.write(text)
                    out.flush()
                    collected.append(text)
                    print(text, end="", flush=True)

        rc = proc.wait()
        stderr = proc.stderr.read() if proc.stderr else ""

    if rc != 0:
        raise ClaudeError(
            f"claude CLI failed (exit {rc}); partial output preserved at "
            f"{stream_to}. Re-run the same command to continue.\n"
            f"stderr:\n{stderr}"
        )
    return "".join(collected).strip()
