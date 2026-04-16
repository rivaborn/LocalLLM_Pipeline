# ============================================================
# llm_common.ps1 -- Shim that loads both sub-modules
#
# Location: LocalLLM_Pipeline/Common/llm_common.ps1
# All 8 worker scripts dot-source this file; it delegates to
# the two sub-modules so callers need zero changes.
#
# Split:
#   llm_core.ps1     - LLM invocation, endpoint resolution,
#                       env parsing, cancel key, Cfg helper
#   file_helpers.ps1 - Presets, hashing, fence-lang mapping,
#                       trivial-file detection, truncation,
#                       architecture/LSP resolution, progress
# ============================================================

. (Join-Path $PSScriptRoot 'llm_core.ps1')
. (Join-Path $PSScriptRoot 'file_helpers.ps1')
