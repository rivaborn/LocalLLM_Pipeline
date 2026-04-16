# generate_compile_commands.py -- Clang compile_commands.json Generator

## Purpose

`generate_compile_commands.py` automatically discovers build artifacts in a repository and generates
a `compile_commands.json` file that clangd can consume for semantic indexing. This is a prerequisite
for `serena_extract.ps1` / `serena_extract.py`, which use clangd to extract LSP symbol context.

The script employs a three-phase resolution strategy. First, it checks for a pre-existing
`compile_commands.json` anywhere under the repo root (e.g. from a previous CMake run) and copies it
to the output path if found. Second, it natively parses Visual Studio / Visual C++ project files --
`.vcxproj` (MSBuild, VS 2010+), `.vcproj` (VS 2002-2008), and `.dsp` (Visual C++ 6.0) -- to
extract source files, include directories, and preprocessor defines, then builds compile command
entries from them. Third, if no native VC++ projects are found, it delegates to build-system-specific
exporters for CMake, Meson, Ninja, or Bazel.

## Prerequisites

| Requirement | Details |
|---|---|
| Python 3.6+ | Standard library only (no pip dependencies) |
| Build artifacts | At least one of: existing `compile_commands.json`, `.vcxproj`, `.vcproj`, `.dsp`, `CMakeLists.txt`, `meson.build`, `build.ninja`, or `WORKSPACE` |
| (Delegate mode) cmake, meson, ninja, or bazel | Required on PATH if the corresponding build system is detected and no VC++ projects exist |

## Usage

```bash
python generate_compile_commands.py [--root <path>] [--output <path>]
```

### CLI Options

| Parameter | Type | Default | Description |
|---|---|---|---|
| `--root` | Path | `.` (current directory) | Repository root directory to scan for build artifacts |
| `--output` | Path | `<root>/compile_commands.json` | Output path for the generated file |

## How It Is Invoked

**Standalone:**
```bash
cd C:\Coding\Generals
python LocalLLMAnalysis\generate_compile_commands.py --root .
```

**Via ArchPipeline.py (analysis mode):**
Called as the first step in the optional "setup" phase before analysis begins:
```
python Common/ArchPipeline.py analysis
```
The pipeline runs `python generate_compile_commands.py` followed by `serena_extract.ps1` before
the main analysis steps. This setup phase can be skipped with the `--skip-setup` flag.

## Input Files

| Input | Source | Description |
|---|---|---|
| `.vcxproj` files | Recursive glob from `--root` | MSBuild project files (VS 2010+) |
| `.vcproj` files | Recursive glob from `--root` | Pre-MSBuild project files (VS 2002-2008) |
| `.dsp` files | Recursive glob from `--root` | Visual C++ 6.0 project files |
| `.sln` files | Recursive glob from `--root` | Solution files (parsed for project references, informational) |
| `compile_commands.json` | Recursive glob from `--root` | Pre-existing compile database (copied if found) |
| `CMakeLists.txt` / `meson.build` / `build.ninja` / `WORKSPACE` | Root directory | Triggers delegate build system |

## Output Files

| Output | Location | Description |
|---|---|---|
| `compile_commands.json` | `<root>/compile_commands.json` (default) or `--output` path | JSON array of compile commands for clangd |

### Output Format

Each entry in the JSON array follows the clang compilation database schema:
```json
{
    "directory": "/absolute/path/to/project",
    "file": "/absolute/path/to/source.cpp",
    "command": "clang++ -std=c++98 -DDEFINE1 -I/include/path -c /path/to/source.cpp"
}
```

## Resolution Order

1. **Pre-existing `compile_commands.json`**: Found anywhere under root (excluding our own output) -- copied verbatim.
2. **Native VC++ project parsing**: `.vcxproj` > `.vcproj` > `.dsp` (priority by age/format). Solution files contribute indirectly via referenced projects. Entries are deduplicated.
3. **Build system delegation**: CMake > Meson > Ninja > Bazel. The script invokes the build tool to generate the compile database.

## Environment Variables / .env Keys

This script does not read `.env` files. It uses no environment variables beyond standard PATH.

## Exit Codes

| Code | Meaning |
|---|---|
| `0` | Success (file generated or already exists) |
| `1` | No parseable build artifacts found under the repo root |
| `2` | Delegate build tool not on PATH, or delegate exporter failed |

## Project File Parsing Details

### .vcxproj (MSBuild)
- Extracts `AdditionalIncludeDirectories` and `PreprocessorDefinitions` from `ItemDefinitionGroup/ClCompile`
- Prefers Release configuration; falls back to first available
- Resolves include paths relative to project directory; skips unresolved MSBuild macros (`$(...)`)

### .vcproj (VS 2002-2008)
- Extracts settings from `Configuration/Tool[@Name='VCCLCompilerTool']`
- Parses `;` or `,` separated lists
- Walks nested `Files/Filter/File` elements for source discovery

### .dsp (Visual C++ 6.0)
- Reads `# ADD CPP` lines for compiler flags
- Extracts `/I "path"` for includes and `/D "define"` for preprocessor definitions
- Parses `SOURCE=` lines for source file enumeration

## Examples

**Example 1: Generate compile_commands.json for a VC++ project**
```bash
python generate_compile_commands.py --root C:\Coding\Generals
```
Discovers `.vcxproj` / `.dsp` files, parses them, writes `C:\Coding\Generals\compile_commands.json`.

**Example 2: Specify a custom output path**
```bash
python generate_compile_commands.py --root C:\Coding\MyProject --output C:\Coding\MyProject\build\compile_commands.json
```

**Example 3: CMake project (delegate mode)**
```bash
cd /home/user/cmake-project
python generate_compile_commands.py
```
Detects `CMakeLists.txt`, runs `cmake -G Ninja -DCMAKE_EXPORT_COMPILE_COMMANDS=ON`, copies the
generated `build/compile_commands.json` to the root.
