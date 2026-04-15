#!/usr/bin/env python3
"""
generate_compile_commands.py -- Generate compile_commands.json for clangd

Discovers whichever build artifacts are present under the repo root and
uses them to produce a compile_commands.json that clangd can consume for
semantic indexing.

Resolution order:
    1. A pre-existing compile_commands.json anywhere under the root
       (e.g. from a previous `cmake -DCMAKE_EXPORT_COMPILE_COMMANDS=ON`
       run in `build/`) -- copied verbatim to the output path.
    2. Native Visual Studio / Visual C++ project files, parsed directly:
         .vcxproj  (MSBuild, VS 2010+)
         .vcproj   (VS 2002-2008, pre-MSBuild)
         .dsp      (Visual C++ 6.0 / Watcom-era)
       .sln files are reported for visibility; they're containers, so they
       only contribute indirectly via the projects they reference.
    3. Delegate to a native exporter for higher-level build systems whose
       config files are too complex to parse directly:
         CMakeLists.txt   -> `cmake ... -DCMAKE_EXPORT_COMPILE_COMMANDS=ON`
         meson.build      -> `meson setup build`
         build.ninja      -> `ninja -t compdb`
         WORKSPACE/BUILD  -> Bazel (requires hedron_compile_commands)
       If the required delegate tool is not on PATH, the script stops and
       prints an install URL.

Usage:
    python generate_compile_commands.py [--root <path>] [--output <path>]

Defaults:
    --root   .                          (repo root)
    --output <root>/compile_commands.json
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

MSBUILD_NS = "http://schemas.microsoft.com/developer/msbuild/2003"
NS = {"ms": MSBUILD_NS}

SOURCE_EXTS = (".cpp", ".cxx", ".cc", ".c")

INSTALL_URLS = {
    "cmake": "https://cmake.org/download/",
    "meson": "https://mesonbuild.com/Getting-meson.html",
    "ninja": "https://github.com/ninja-build/ninja/releases",
    "bazel": "https://bazel.build/install",
}


def _require_tool(tool: str) -> str:
    path = shutil.which(tool)
    if path:
        return path
    print(f"  ERROR: required tool `{tool}` is not on PATH.", file=sys.stderr)
    url = INSTALL_URLS.get(tool)
    if url:
        print(f"         Install from: {url}", file=sys.stderr)
    sys.exit(2)


# --- .vcxproj (MSBuild, VS 2010+) ------------------------------------------


def _split_msbuild_list(value: str) -> list[str]:
    """Split a ';'-delimited MSBuild metadata value and drop inherit placeholders."""
    parts = [p.strip() for p in value.split(";")]
    return [p for p in parts if p and not p.startswith("%(")]


def parse_vcxproj(vcxproj: Path) -> dict | None:
    try:
        tree = ET.parse(vcxproj)
    except ET.ParseError as exc:
        print(f"  WARN: failed to parse {vcxproj}: {exc}", file=sys.stderr)
        return None

    root_el = tree.getroot()
    proj_dir = vcxproj.parent.resolve()

    clcompile_settings = None
    for idg in root_el.findall("ms:ItemDefinitionGroup", NS):
        cc = idg.find("ms:ClCompile", NS)
        if cc is None:
            continue
        cond = idg.get("Condition", "")
        if "Release" in cond:
            clcompile_settings = cc
            break
        if clcompile_settings is None:
            clcompile_settings = cc

    include_dirs: list[str] = []
    defines: list[str] = []
    if clcompile_settings is not None:
        inc_el = clcompile_settings.find("ms:AdditionalIncludeDirectories", NS)
        if inc_el is not None and inc_el.text:
            include_dirs = _split_msbuild_list(inc_el.text)
        def_el = clcompile_settings.find("ms:PreprocessorDefinitions", NS)
        if def_el is not None and def_el.text:
            defines = _split_msbuild_list(def_el.text)

    sources: list[str] = []
    for item_group in root_el.findall("ms:ItemGroup", NS):
        for cc in item_group.findall("ms:ClCompile", NS):
            inc = cc.get("Include")
            if inc:
                sources.append(inc)

    return {
        "kind": "vcxproj",
        "path": vcxproj,
        "project_dir": proj_dir,
        "sources": sources,
        "include_dirs": include_dirs,
        "defines": defines,
    }


# --- .vcproj (VS 2002-2008, pre-MSBuild) -----------------------------------
# .vcproj is a namespace-free XML document. Compile flags live in
# Configurations/Configuration/Tool[@Name='VCCLCompilerTool'] with
# ';' or ',' separated lists. Source files are nested under
# Files/Filter.../File[@RelativePath].


def _split_vcproj_list(value: str) -> list[str]:
    parts = re.split(r"[;,]", value)
    out: list[str] = []
    for p in parts:
        s = p.strip().strip('"')
        if s and not s.startswith("$(NoInherit"):
            out.append(s)
    return out


def parse_vcproj(vcproj: Path) -> dict | None:
    try:
        tree = ET.parse(vcproj)
    except ET.ParseError as exc:
        print(f"  WARN: failed to parse {vcproj}: {exc}", file=sys.stderr)
        return None

    root_el = tree.getroot()
    proj_dir = vcproj.parent.resolve()

    release_cfg = None
    fallback_cfg = None
    for cfg in root_el.findall(".//Configurations/Configuration"):
        if fallback_cfg is None:
            fallback_cfg = cfg
        if "Release" in cfg.get("Name", "") and release_cfg is None:
            release_cfg = cfg
    cfg = release_cfg or fallback_cfg

    include_dirs: list[str] = []
    defines: list[str] = []
    if cfg is not None:
        for tool in cfg.findall("Tool"):
            if tool.get("Name") != "VCCLCompilerTool":
                continue
            inc = tool.get("AdditionalIncludeDirectories", "")
            if inc:
                include_dirs = _split_vcproj_list(inc)
            dfn = tool.get("PreprocessorDefinitions", "")
            if dfn:
                defines = _split_vcproj_list(dfn)
            break

    sources: list[str] = []

    def walk_files(node: ET.Element) -> None:
        for file_el in node.findall("File"):
            path = file_el.get("RelativePath", "")
            if path and path.lower().endswith(SOURCE_EXTS):
                sources.append(path)
        for filter_el in node.findall("Filter"):
            walk_files(filter_el)

    files_el = root_el.find("Files")
    if files_el is not None:
        walk_files(files_el)

    return {
        "kind": "vcproj",
        "path": vcproj,
        "project_dir": proj_dir,
        "sources": sources,
        "include_dirs": include_dirs,
        "defines": defines,
    }


# --- .dsp (Visual C++ 6.0) -------------------------------------------------

_DSP_CFG_RE = re.compile(r'"\$\(CFG\)"\s*==\s*"([^"]+)"')
_DSP_INC_RE = re.compile(r'/I\s+"([^"]+)"')
_DSP_DEF_RE = re.compile(r'/D\s+(?:"([^"]+)"|(\S+))')
_DSP_SRC_RE = re.compile(r"^\s*SOURCE\s*=\s*(.+?)\s*$", re.IGNORECASE)


def parse_dsp(dsp: Path) -> dict | None:
    try:
        text = dsp.read_text(encoding="latin-1", errors="replace")
    except OSError as exc:
        print(f"  WARN: failed to read {dsp}: {exc}", file=sys.stderr)
        return None

    proj_dir = dsp.parent.resolve()

    release_cpp: str | None = None
    fallback_cpp: str | None = None
    current_cfg = ""
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("!IF") or line.startswith("!ELSEIF"):
            m = _DSP_CFG_RE.search(line)
            current_cfg = m.group(1) if m else ""
        elif line.startswith("# ADD CPP"):
            if fallback_cpp is None:
                fallback_cpp = line
            if "Release" in current_cfg and release_cpp is None:
                release_cpp = line

    cpp_line = release_cpp or fallback_cpp or ""
    include_dirs = _DSP_INC_RE.findall(cpp_line)
    defines = [quoted or bare for quoted, bare in _DSP_DEF_RE.findall(cpp_line)]

    sources: list[str] = []
    for raw_line in text.splitlines():
        m = _DSP_SRC_RE.match(raw_line)
        if not m:
            continue
        path = m.group(1).strip().strip('"')
        if path.lower().endswith(SOURCE_EXTS):
            sources.append(path)

    return {
        "kind": "dsp",
        "path": dsp,
        "project_dir": proj_dir,
        "sources": sources,
        "include_dirs": include_dirs,
        "defines": defines,
    }


# --- .sln (Visual Studio solution, informational only) -------------------

_SLN_PROJECT_RE = re.compile(r'^Project\("[^"]+"\)\s*=\s*"[^"]*",\s*"([^"]+)"')


def parse_sln_projects(sln: Path) -> list[Path]:
    try:
        text = sln.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return []
    sln_dir = sln.parent
    referenced: list[Path] = []
    for line in text.splitlines():
        m = _SLN_PROJECT_RE.match(line)
        if not m:
            continue
        proj_rel = m.group(1).replace("\\", "/")
        if proj_rel.lower().endswith((".vcxproj", ".vcproj", ".dsp")):
            proj_path = (sln_dir / proj_rel).resolve()
            if proj_path.is_file():
                referenced.append(proj_path)
    return referenced


# --- Entry building --------------------------------------------------------


def resolve_includes(project_dir: Path, include_dirs: list[str]) -> list[str]:
    resolved: list[str] = [str(project_dir).replace("\\", "/")]
    for raw in include_dirs:
        if "$(" in raw:  # unresolved MSBuild macro
            continue
        candidate = (project_dir / raw).resolve()
        if candidate.is_dir():
            resolved.append(str(candidate).replace("\\", "/"))

    seen: set[str] = set()
    deduped: list[str] = []
    for p in resolved:
        if p not in seen:
            seen.add(p)
            deduped.append(p)
    return deduped


def build_entries(project: dict) -> list[dict]:
    proj_dir: Path = project["project_dir"]
    proj_dir_str = str(proj_dir).replace("\\", "/")

    include_flags: list[str] = []
    for inc in resolve_includes(proj_dir, project["include_dirs"]):
        include_flags.extend(["-I", inc])
    define_flags = [f"-D{d}" for d in project["defines"]]

    entries: list[dict] = []
    for rel in project["sources"]:
        rel_norm = rel.replace("\\", "/")
        src_abs = (proj_dir / rel_norm).resolve()
        if not src_abs.is_file():
            continue
        src_str = str(src_abs).replace("\\", "/")
        command_parts = ["clang++", "-std=c++98"] + define_flags + include_flags + ["-c", src_str]
        entries.append({
            "directory": proj_dir_str,
            "file": src_str,
            "command": " ".join(command_parts),
        })
    return entries


# --- Discovery -------------------------------------------------------------


def discover_artifacts(root: Path) -> dict[str, list[Path]]:
    return {
        "sln":     sorted(root.rglob("*.sln")),
        "vcxproj": sorted(root.rglob("*.vcxproj")),
        "vcproj":  sorted(root.rglob("*.vcproj")),
        "dsp":     sorted(root.rglob("*.dsp")),
    }


def find_existing_compile_commands(root: Path, our_output: Path) -> Path | None:
    """Return the first pre-existing compile_commands.json under root, if any.

    Skips our own output path so we don't loop on a previous run.
    """
    our_resolved = our_output.resolve() if our_output.exists() else None
    for cc in sorted(root.rglob("compile_commands.json")):
        if our_resolved is not None and cc.resolve() == our_resolved:
            continue
        return cc
    return None


def detect_delegate_system(root: Path) -> str | None:
    """Return the name of a delegate-capable build system at the root."""
    if (root / "CMakeLists.txt").is_file():
        return "cmake"
    if (root / "meson.build").is_file():
        return "meson"
    if (root / "build.ninja").is_file():
        return "ninja"
    if any((root / name).is_file() for name in
           ("WORKSPACE", "WORKSPACE.bazel", "MODULE.bazel")):
        return "bazel"
    return None


# --- Delegates -------------------------------------------------------------


def _copy_generated(src: Path, dest: Path) -> None:
    dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"  Copied {src} -> {dest}")


def run_cmake_delegate(root: Path, output: Path) -> None:
    _require_tool("cmake")
    _require_tool("ninja")  # generator; required for CMAKE_EXPORT_COMPILE_COMMANDS
    build_dir = root / "build"
    build_dir.mkdir(exist_ok=True)
    cmd = [
        "cmake", "-S", str(root), "-B", str(build_dir),
        "-G", "Ninja",
        "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON",
    ]
    print(f"  Running: {' '.join(cmd)}")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print(f"  ERROR: cmake exited with code {result.returncode}", file=sys.stderr)
        sys.exit(2)
    generated = build_dir / "compile_commands.json"
    if not generated.is_file():
        print(f"  ERROR: cmake ran but {generated} was not produced.", file=sys.stderr)
        sys.exit(2)
    _copy_generated(generated, output)


def run_meson_delegate(root: Path, output: Path) -> None:
    _require_tool("meson")
    _require_tool("ninja")  # meson's default backend
    build_dir = root / "build"
    cmd = ["meson", "setup", str(build_dir), str(root)]
    print(f"  Running: {' '.join(cmd)}")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print(f"  ERROR: meson setup exited with code {result.returncode}", file=sys.stderr)
        sys.exit(2)
    generated = build_dir / "compile_commands.json"
    if not generated.is_file():
        print(f"  ERROR: meson ran but {generated} was not produced.", file=sys.stderr)
        sys.exit(2)
    _copy_generated(generated, output)


def run_ninja_delegate(root: Path, output: Path) -> None:
    _require_tool("ninja")
    cmd = ["ninja", "-C", str(root), "-t", "compdb"]
    print(f"  Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  ERROR: `ninja -t compdb` exited with code {result.returncode}\n{result.stderr}",
              file=sys.stderr)
        sys.exit(2)
    if not result.stdout.strip():
        print("  ERROR: `ninja -t compdb` produced no output.", file=sys.stderr)
        sys.exit(2)
    output.write_text(result.stdout, encoding="utf-8")
    print(f"  Wrote compdb output -> {output}")


def run_bazel_delegate(root: Path, output: Path) -> None:
    # Bazel's native tooling doesn't emit compile_commands.json. The community
    # standard is the hedron_compile_commands rule, which must be wired into the
    # workspace. We can't bootstrap that from outside the project, so we stop
    # here and instruct the user.
    print("  ERROR: Bazel workspace detected.", file=sys.stderr)
    print("         compile_commands.json for Bazel requires hedron_compile_commands:",
          file=sys.stderr)
    print("         https://github.com/hedronvision/bazel-compile-commands-extractor",
          file=sys.stderr)
    print("         Add it to your WORKSPACE / MODULE.bazel, then run:", file=sys.stderr)
    print("             bazel run @hedron_compile_commands//:refresh_all", file=sys.stderr)
    _require_tool("bazel")  # additionally require bazel itself to be installed
    sys.exit(2)


DELEGATE_RUNNERS = {
    "cmake": run_cmake_delegate,
    "meson": run_meson_delegate,
    "ninja": run_ninja_delegate,
    "bazel": run_bazel_delegate,
}


# --- Orchestration ---------------------------------------------------------


def generate_compile_commands(root: Path, output: Path) -> None:
    root = root.resolve()
    print(f"Scanning {root} for build artifacts ...")

    # Phase A: pre-existing compile_commands.json wins if present.
    existing = find_existing_compile_commands(root, output)
    if existing is not None:
        print(f"  Found existing compile_commands.json at {existing}")
        _copy_generated(existing, output)
        print("Done.")
        return

    # Phase B: native Visual Studio / VC++ project files.
    artifacts = discover_artifacts(root)
    print(f"  Found: .sln={len(artifacts['sln'])}  "
          f".vcxproj={len(artifacts['vcxproj'])}  "
          f".vcproj={len(artifacts['vcproj'])}  "
          f".dsp={len(artifacts['dsp'])}")

    if artifacts["sln"]:
        referenced: set[Path] = set()
        for sln in artifacts["sln"]:
            for p in parse_sln_projects(sln):
                referenced.add(p)
        if referenced:
            print(f"    {len(referenced)} project(s) referenced by solutions")

    parsers = (
        (artifacts["vcxproj"], parse_vcxproj),
        (artifacts["vcproj"],  parse_vcproj),
        (artifacts["dsp"],     parse_dsp),
    )
    parsed_projects: list[dict] = []
    for files, parser in parsers:
        for f in files:
            p = parser(f)
            if p is not None:
                parsed_projects.append(p)

    all_entries: list[dict] = []
    for proj in parsed_projects:
        entries = build_entries(proj)
        rel_path = (proj["path"].relative_to(root)
                    if proj["path"].is_relative_to(root) else proj["path"])
        print(f"    [{proj['kind']}] {rel_path}: {len(entries)} source file(s)")
        all_entries.extend(entries)

    if all_entries:
        seen: set[str] = set()
        deduped: list[dict] = []
        for e in all_entries:
            if e["file"] in seen:
                continue
            seen.add(e["file"])
            deduped.append(e)
        dropped = len(all_entries) - len(deduped)
        if dropped:
            print(f"  Deduplicated {dropped} duplicate source entries "
                  "(.vcxproj > .vcproj > .dsp)")

        output.write_text(json.dumps(deduped, indent=2), encoding="utf-8")
        size_kb = output.stat().st_size / 1024
        print(f"  Wrote {output} ({len(deduped)} entries, {size_kb:.1f} KB)")
        print("Done.")
        return

    # Phase C: delegate to a higher-level build system.
    system = detect_delegate_system(root)
    if system is not None:
        print(f"  No native VC++ projects parseable; delegating to {system}")
        DELEGATE_RUNNERS[system](root, output)
        print("Done.")
        return

    print("  ERROR: no parseable build artifacts found under the repo root.",
          file=sys.stderr)
    sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate compile_commands.json for clangd")
    parser.add_argument("--root", type=Path, default=Path("."), help="Repository root")
    parser.add_argument("--output", type=Path, default=None, help="Output path")
    args = parser.parse_args()

    root = args.root.resolve()
    output = args.output or (root / "compile_commands.json")

    if output.exists():
        print(f"  {output} already exists, skipping generation.")
        return

    generate_compile_commands(root, output)


if __name__ == "__main__":
    main()
