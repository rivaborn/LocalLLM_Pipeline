# LocalLLM_Pipeline — Script Inventory

Line counts for every Python / PowerShell script in the toolkit. Generated
from `find . -name "*.py" -o -name "*.ps1"` excluding `.venv`, `.git`,
`__pycache__`.

## Common/ — unified orchestrator + shared libraries

| Lines | Path |
|------:|------|
|    61 | Common/ArchPipeline.py |
|   498 | Common/llm_common.ps1 |

### Common/_pipeline/ — orchestrator internals (Python package)

| Lines | Path |
|------:|------|
|     6 | Common/_pipeline/__init__.py |
|    61 | Common/_pipeline/claude.py |
|    61 | Common/_pipeline/config.py |
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
|   784 | Common/_pipeline/modes/coding.py |
|   389 | Common/_pipeline/modes/debug.py |

## LocalLLMAnalysis/ — architecture analysis pipeline (workers)

| Lines | Path |
|------:|------|
|   329 | LocalLLMAnalysis/Arch_Analysis_Pipeline.py |
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
|  1179 | LocalLLMCoding/Arch_Coding_Pipeline.ps1 |
|   277 | LocalLLMCoding/fix_imports.py |
|   472 | LocalLLMCoding/run_aider.py |

## LocalLLMDebug/ — debug pipeline (workers)

| Lines | Path |
|------:|------|
|   735 | LocalLLMDebug/Arch_Debug_Pipeline.ps1 |
|  1270 | LocalLLMDebug/bughunt_iterative_local.ps1 |
|   359 | LocalLLMDebug/bughunt_local.ps1 |
|   352 | LocalLLMDebug/dataflow_local.ps1 |
|   369 | LocalLLMDebug/interfaces_local.ps1 |
|   446 | LocalLLMDebug/testgap_local.ps1 |

## Totals

| Group | Files | Lines |
|-------|------:|------:|
| Common/ (orchestrator + libs) | 16 | 2 488 |
| LocalLLMAnalysis/ | 12 | 6 235 |
| LocalLLMCoding/  |  3 | 1 928 |
| LocalLLMDebug/   |  6 | 3 531 |
| **Grand total**  | **37** | **14 182** |
