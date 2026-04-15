Changes needed for new codebase

1. Start with
Read `Architecture Analysis Toolkit - Setup & Usage Guide.md` in this folder, then the `Quickstart.md`.

2. Update `LocalLLM_Pipeline/Common/.env` for the current codebase
The shared .env lives at `LocalLLM_Pipeline/Common/.env` (not in `LocalLLMAnalysis/`). Update:
- `CODEBASE_DESC` — one-line description of the new project
- `PRESET` — pick from `python`, `quake`, `unreal`, `godot`, `unity`, `source`, `rust`, `generals`/`cnc`/`sage`
- `INCLUDE_EXT_REGEX` / `EXCLUDE_DIRS_REGEX` — preset overrides for project-specific extensions or excluded dirs
- `#Subsections begin` / `#Subsections end` — list the subdirectories to analyse, one per line

3. Review other scripts
Do the scripts need any other changes to handle this codebase? (Rare — the preset + include/exclude regex usually suffice.)

4. Optional: wire into the Debug pipeline
The Debug pipeline (`LocalLLMDebug/`) can consume Analysis outputs when `ARCHITECTURE_DIR` (and optionally `SERENA_CONTEXT_DIR`) in `Common/.env` point at the right locations. See the integration section in `DebugWorkflow.md`.
