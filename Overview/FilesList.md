# LocalLLM_Pipeline — Script Inventory

Line counts for every Python / PowerShell script in the toolkit.
Excludes `.venv`, `.git`, `__pycache__`.

## Common/ — unified orchestrator + shared libraries

| Lines | Path |
|------:|------|
|    61 | Common/ArchPipeline.py |
|    17 | Common/llm_common.ps1 *(shim — dot-sources llm_core + file_helpers)* |
|   175 | Common/llm_core.ps1 |
|   230 | Common/file_helpers.ps1 |

### Common/_pipeline/ — orchestrator internals (Python package)

| Lines | Path |
|------:|------|
|     6 | Common/_pipeline/__init__.py |
|    61 | Common/_pipeline/claude.py |
|    96 | Common/_pipeline/config.py |
|   257 | Common/_pipeline/lsp_pyright.py |
|   159 | Common/_pipeline/ollama.py |
|    78 | Common/_pipeline/progress.py |
|    75 | Common/_pipeline/subprocess_runner.py |
|   144 | Common/_pipeline/symbols.py |
|    83 | Common/_pipeline/ui.py |

### Common/_pipeline/modes/ — per-mode implementations

| Lines | Path |
|------:|------|
|     6 | Common/_pipeline/modes/__init__.py |
|   132 | Common/_pipeline/modes/all_modes.py |
|   184 | Common/_pipeline/modes/analysis.py |

### Common/_pipeline/modes/coding/ — coding pipeline (6-module package)

| Lines | Path |
|------:|------|
|    17 | coding/__init__.py |
|   226 | coding/cli.py |
|   112 | coding/fileops.py |
|   118 | coding/router.py |
|    66 | coding/stages_exec.py |
|   314 | coding/stages_llm.py |

### Common/_pipeline/modes/debug/ — debug pipeline (3-module package)

| Lines | Path |
|------:|------|
|    16 | debug/__init__.py |
|   173 | debug/cli.py |
|   223 | debug/fix_bugs.py |

## LocalLLMAnalysis/ — architecture analysis pipeline (workers)

| Lines | Path |
|------:|------|
|   311 | LocalLLMAnalysis/archgen_local.ps1 |
|   357 | LocalLLMAnalysis/arch_overview_local.ps1 |
|   733 | LocalLLMAnalysis/archgraph.ps1 |
|   511 | LocalLLMAnalysis/archpass2_context.ps1 |
|   358 | LocalLLMAnalysis/archpass2_local.ps1 |
|   832 | LocalLLMAnalysis/archxref.ps1 |
|    36 | LocalLLMAnalysis/conftest.py |
|   550 | LocalLLMAnalysis/generate_compile_commands.py |
|   386 | LocalLLMAnalysis/serena_extract.ps1 |
|  1458 | LocalLLMAnalysis/serena_extract.py |
|   374 | LocalLLMAnalysis/test_arch_analysis_pipeline.py |

## LocalLLMCoding/ — coding pipeline (workers)

| Lines | Path |
|------:|------|
|    34 | LocalLLMCoding/run_aider.py *(shim)* |
|   256 | LocalLLMCoding/fix_imports.py |

### LocalLLMCoding/_aider/ — run_aider internals (5-module package)

| Lines | Path |
|------:|------|
|    12 | _aider/__init__.py |
|   154 | _aider/cli.py |
|    64 | _aider/parser.py |
|    66 | _aider/prompts.py |
|   137 | _aider/runner.py |

## LocalLLMDebug/ — debug pipeline (workers)

| Lines | Path |
|------:|------|
|  1270 | LocalLLMDebug/bughunt_iterative_local.ps1 |
|   359 | LocalLLMDebug/bughunt_local.ps1 |
|   352 | LocalLLMDebug/dataflow_local.ps1 |
|   369 | LocalLLMDebug/interfaces_local.ps1 |
|   446 | LocalLLMDebug/testgap_local.ps1 |

## legacy/ — archived entry points (not maintained)

| Lines | Path |
|------:|------|
|   329 | legacy/Arch_Analysis_Pipeline.py |
|  1179 | legacy/Arch_Coding_Pipeline.ps1 |
|   735 | legacy/Arch_Debug_Pipeline.ps1 |

## Totals

| Group | Files | Lines |
|-------|------:|------:|
| Common/ (orchestrator + libs) | 22 | 2 800 |
| LocalLLMAnalysis/ | 11 | 5 906 |
| LocalLLMCoding/ | 7 | 723 |
| LocalLLMDebug/ | 5 | 2 796 |
| legacy/ | 3 | 2 243 |
| **Grand total** | **48** | **14 468** |
