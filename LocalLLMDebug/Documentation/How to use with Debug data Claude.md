# Using LocalLLMDebug Output with Claude Code

The debugging pipeline produces structured reports designed to be loaded directly into a Claude Code session as context before describing the problem. This document explains what to load and when.

---

## General Debugging Session

Load the four summary documents to give Claude Code the full picture:

```
Read architecture/INTERFACES.md
Read architecture/DATA_FLOW.md
Read bug_reports/SUMMARY.md
Read test_gaps/GAP_REPORT.md
```

These cover what each module's contract is, where data crosses boundaries, what bugs the scanner found, and where test coverage is thin. From there, describe the problem and Claude Code can reason about root causes without needing to read every source file.

---

## Specific File Investigation

Load the targeted reports for the file in question:

```
Read architecture/interfaces/src/nmon/collector.py.iface.md
Read bug_reports/src/nmon/collector.py.md
Read bug_fixes/src/nmon/collector.py.iter_log.md
Read src/nmon/collector.py
```

The interface contract shows what the function promises. The bug report shows what the LLM found wrong. The iteration log shows what fixes were attempted. The source is what Claude Code will actually edit.

---

## Which Outputs Matter Most for Which Problems

| Problem                                       | Load first                                                       |
|-----------------------------------------------|------------------------------------------------------------------|
| Wrong value displayed                         | `DATA_FLOW.md` -- Handoff Points section identifies which boundary to check |
| Silent failure / no error but wrong behaviour  | `INTERFACES.md` -- All Silent Failure Modes section              |
| Bug slipped past tests                        | `GAP_REPORT.md` -- Broken or Misleading Tests section            |
| Changed one module, broke another             | `INTERFACES.md` -- Cross-Module Obligations section              |
| Not sure where to start                       | `bug_reports/SUMMARY.md` -- triage by HIGH count                 |

---

## Practical Tips

- **Load summaries, not all per-file reports.** The summary documents are designed to fit in context. Per-file reports are for drilling down after you've identified the problem area.

- **Pair the bug report with the source.** A bug report without the source it describes is hard to act on. Load both when asking Claude Code to fix something specific.

- **The iteration log shows what was already tried.** If `bughunt_iterative` ran on a file and ended with `DIVERGING` or `MAX_ITER`, the iteration log shows what fixes were attempted and why they didn't converge. Loading this prevents Claude Code from repeating the same approaches.

- **Regenerate after edits.** The tools are SHA1-cached, so re-running after fixing a few files only refreshes the changed files. Keep the reports current as you fix things.
