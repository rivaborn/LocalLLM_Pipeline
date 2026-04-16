# Legacy entry points

These scripts were the pre-`ArchPipeline.py` entry points. They are
preserved as historical reference / emergency fallback and are **not
maintained**. New work happens in `Common/ArchPipeline.py` and the
`Common/_pipeline/` package.

| Legacy script | Replaced by |
|---------------|-------------|
| `Arch_Analysis_Pipeline.py` | `python Common/ArchPipeline.py analysis` |
| `Arch_Coding_Pipeline.ps1`  | `python Common/ArchPipeline.py coding`  |
| `Arch_Debug_Pipeline.ps1`   | `python Common/ArchPipeline.py debug`   |

The worker scripts these entry points called (`dataflow_local.ps1`,
`bughunt_local.ps1`, `archgen_local.ps1`, etc.) remain in their original
directories and are still called by the new orchestrator.
