# Agentic AI Coding Tools — Alternatives to Custom Pipeline

Evaluation of purpose-built tools for the "initial prompt → complete
working app" workflow, motivated by recurring file-creation / tool-calling
failures in our custom `LocalLLM_Pipeline`.

## Root cause of our pain

The file-creation failures aren't really our pipeline's fault — they're
about the **output-format contract** aider requires:

```
filename.py
```
content
```
```

Local LLMs frequently:
- Emit only the filename header and stop (empty file)
- Emit code without the closing fence
- Skip files in multi-file steps
- Produce code in the wrong language fence

Models with **native tool-calling** handle file I/O vastly better via
JSON tool calls than via markdown fences. Qwen 2.5 Coder, DeepSeek
Coder V2.5, and Llama 3.3 all support structured tool calls. Tools
built around tool-calling (not fence-parsing) sidestep this class of
bugs entirely.

## Top alternatives, ordered by fit to our workflow

### 1. Cline / Roo Code (VS Code extensions)

Autonomous coding agents that plan, create files, run commands, and
iterate — all via **native tool-calling**. Supports local LLMs via
Ollama.

**Fix for the #1 pain point:** file creation goes through a
`write_to_file` tool call. Either succeeds or fails loudly — no silently
empty files.

**Tradeoff:** interactive VS Code session, not a headless pipeline.
Can be left running autonomously for long tasks but needs the IDE open.

### 2. Plandex (terminal CLI)

Open-source CLI that closely matches our current workflow:
prompt → plan → multi-file execution with review checkpoints. Local
LLM support. Resumable runs.

- Closest mental model to what we built.
- Handles file I/O via structured responses with better recovery
  than aider.
- Terminal-based, so drops into existing shell workflows.

### 3. OpenHands (formerly OpenDevin)

Fully autonomous agent running in a Docker sandbox. Give it a prompt
and a repo, it plans + builds + tests. Supports local LLMs.

- Most hands-off.
- Heavier setup (Docker).
- Best for "walk away and come back" runs.

## Other notable tools evaluated

| Tool | Notes |
|------|-------|
| **aider** (current) | Good at surgical edits, struggles with whole-file generation via local LLMs |
| **Claude Code** | Excellent — requires Claude API, not local |
| **Cursor / Windsurf** | IDE-native, interactive, closed source |
| **Continue.dev** | Open VS Code extension, good Ollama support, interactive |
| **GitHub Copilot** | IDE-native, closed source |
| **GPT-Engineer** | Older; similar scope to our pipeline; same class of issues with local models |
| **MetaGPT** | Multi-agent (PM / architect / engineer / QA); heavy |
| **Devin** | Proprietary SaaS, expensive |
| **AutoGen** | Multi-agent framework, flexible but DIY |
| **Goose** (Block) | Open-source, MCP-first, good file tools |
| **Smol Developer** | Unmaintained |
| **Cody** (Sourcegraph) | Oriented toward existing codebases |

## Recommendation

Try **Cline** first — lowest friction, runs in VS Code, configure it
against the existing Ollama server, point it at an empty folder with
the contents of `InitialPrompt.md` pasted in, compare against the
current pipeline output.

If the interactive loop is a dealbreaker, **Plandex** is the closer
drop-in for the current CLI pipeline.

## What to keep from the current pipeline regardless

Even after switching generation tools, the post-generation QA layers
we built are useful and none of the alternatives provide them:

- **ArchPipeline analysis** — architecture synthesis, cross-reference,
  Mermaid diagrams, interface extraction, data-flow traces, test-gap
  analysis
- **ArchPipeline debug** — advisory bug proposals written to
  `debug_proposals.md`
- **fix_imports.py** — advisory import check with structured LLM
  diagnosis

These should stay in the toolkit as post-generation validation,
regardless of what generates the code.

## Generated

2026-04-16
