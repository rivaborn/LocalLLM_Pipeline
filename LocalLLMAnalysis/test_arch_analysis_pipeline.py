import pytest
from unittest.mock import patch, MagicMock, call
from pathlib import Path
import subprocess
import tempfile
import shutil
import sys  # Added import
import argparse  # Added import
import logging  # Added import

# Import all functions from Arch_Analysis_Pipeline
from Arch_Analysis_Pipeline import (
    get_repo_root, parse_subsections, sanitize_subsection_name,
    setup_logging, build_command, run_command, rename_architecture_folder,
    run_one_time_steps, run_pipeline, parse_args, main,
    PipelineStep, PIPELINE_STEPS, BEGIN_MARKER, END_MARKER,
)

SAMPLE_ENV = """\
LLM_HOST=192.168.1.126
LLM_PORT=11434
PRESET=generals

#Subsections begin
# Generals (base game)
Generals\\Code\\GameEngine\\Source\\Common
Generals\\Code\\GameEngine\\Source\\GameLogic
Generals\\Code\\GameEngine\\Source\\GameClient
Generals\\Code\\GameEngine\\Source\\GameNetwork
Generals\\Code\\GameEngineDevice
Generals\\Code\\Libraries\\Source\\WWVegas
Generals\\Code\\Tools
# GeneralsMD (Zero Hour expansion)
GeneralsMD\\Code\\GameEngine
GeneralsMD\\Code\\GameEngineDevice
GeneralsMD\\Code\\Libraries
#Subsections end
"""

class TestParseSubsections:
    def test_full_parse(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text(SAMPLE_ENV)
        subsections = parse_subsections(env_file)
        assert len(subsections) == 10

    def test_comments_excluded(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text(f"{BEGIN_MARKER}\n# Comment\nPath\\To\\Subsection\n{END_MARKER}")
        subsections = parse_subsections(env_file)
        assert subsections == ["Path\\To\\Subsection"]

    def test_blanks_excluded(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text(f"{BEGIN_MARKER}\n\nPath\\To\\Subsection\n{END_MARKER}")
        subsections = parse_subsections(env_file)
        assert subsections == ["Path\\To\\Subsection"]

    def test_config_excluded(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text(f"LLM_HOST=192.168.1.126\n{BEGIN_MARKER}\nPath\\To\\Subsection\n{END_MARKER}")
        subsections = parse_subsections(env_file)
        assert subsections == ["Path\\To\\Subsection"]

    def test_no_markers_returns_empty(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("LLM_HOST=192.168.1.126")
        subsections = parse_subsections(env_file)
        assert subsections == []

    def test_empty_file_returns_empty(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.touch()
        subsections = parse_subsections(env_file)
        assert subsections == []

    def test_empty_block_returns_empty(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text(f"{BEGIN_MARKER}\n{END_MARKER}")
        subsections = parse_subsections(env_file)
        assert subsections == []

class TestSetupLogging:
    def test_creates_log_file(self, tmp_path):
        log_path = tmp_path / "subdir" / "pipeline.log"
        logger = setup_logging(log_path)
        logger.info("test message")
        for handler in logger.handlers:
            handler.flush()
        assert log_path.exists()
        content = log_path.read_text()
        assert "test message" in content
        assert "[INFO]" in content
        logger.handlers.clear()

    def test_has_console_handler(self, tmp_path):
        log_path = tmp_path / "pipeline.log"
        logger = setup_logging(log_path)
        handler_types = [type(h) for h in logger.handlers]
        assert logging.StreamHandler in handler_types or logging.FileHandler in handler_types
        assert any(isinstance(h, logging.FileHandler) for h in logger.handlers)
        assert any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler) for h in logger.handlers)
        logger.handlers.clear()

class TestSanitizeSubsectionName:
    def test_standard(self):
        assert sanitize_subsection_name("Generals\\Code\\GameEngine\\Source\\Common") == "Generals_Code_GameEngine_Source_Common"

    def test_no_backslashes(self):
        assert sanitize_subsection_name("NoBackslashes") == "NoBackslashes"

    def test_whitespace(self):
        assert sanitize_subsection_name("  Whitespace  ") == "Whitespace"

class TestBuildCommand:
    def test_ps_with_target_dir(self, tmp_path):
        step = PipelineStep("Per-file docs", "archgen_local.ps1", ["-Preset", "generals"], use_target_dir=True, is_powershell=True)
        cmd = build_command(step, "Path\\To\\Subsection", tmp_path)
        assert cmd == [
            "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(tmp_path / "LocalLLMAnalysis" / "archgen_local.ps1"),
            "-TargetDir", "Path\\To\\Subsection", "-Preset", "generals"
        ]

    def test_ps_without_target_dir(self, tmp_path):
        step = PipelineStep("Cross-reference index", "archxref.ps1", [], use_target_dir=False, is_powershell=True)
        cmd = build_command(step, None, tmp_path)
        assert cmd == [
            "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(tmp_path / "LocalLLMAnalysis" / "archxref.ps1")
        ]

    def test_python(self, tmp_path):
        step = PipelineStep("Architecture overview", "arch_overview_local.py", [], use_target_dir=False, is_powershell=False)
        cmd = build_command(step, None, tmp_path)
        assert cmd == [
            sys.executable, str(tmp_path / "LocalLLMAnalysis" / "arch_overview_local.py")
        ]

    def test_args_appended(self, tmp_path):
        step = PipelineStep("Pass 2 analysis", "archpass2_local.ps1", ["-Top", "100"], use_target_dir=False, is_powershell=True)
        cmd = build_command(step, None, tmp_path)
        assert cmd == [
            "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(tmp_path / "LocalLLMAnalysis" / "archpass2_local.ps1"),
            "-Top", "100"
        ]

class TestRunCommand:
    @patch("Arch_Analysis_Pipeline.subprocess.run")
    def test_success(self, mock_run, tmp_path):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="hello\n", stderr="")
        logger = MagicMock()
        run_command(["echo", "hello"], tmp_path, logger)
        mock_run.assert_called_once_with(
            ["echo", "hello"],
            cwd=str(tmp_path),
            capture_output=True,
            text=True
        )
        logger.info.assert_any_call("Running: echo hello")
        logger.debug.assert_any_call("hello")

    @patch("Arch_Analysis_Pipeline.subprocess.run")
    def test_failure_raises(self, mock_run, tmp_path):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="Error occurred")
        logger = MagicMock()
        with pytest.raises(subprocess.CalledProcessError):
            run_command(["echo", "hello"], tmp_path, logger)
        mock_run.assert_called_once_with(
            ["echo", "hello"],
            cwd=str(tmp_path),
            capture_output=True,
            text=True
        )
        logger.info.assert_any_call("Running: echo hello")
        logger.error.assert_any_call("Command failed (exit 1): echo hello\nstderr: Error occurred")

    @patch("Arch_Analysis_Pipeline.subprocess.run")
    def test_dry_run_skips(self, mock_run, tmp_path):
        logger = MagicMock()
        run_command(["echo", "hello"], tmp_path, logger, dry_run=True)
        mock_run.assert_not_called()
        logger.info.assert_any_call("Running: echo hello")
        logger.info.assert_any_call("[DRY RUN] Skipped")

    @patch("Arch_Analysis_Pipeline.subprocess.run")
    def test_cwd_correct(self, mock_run, tmp_path):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        logger = MagicMock()
        run_command(["echo", "hello"], tmp_path, logger)
        mock_run.assert_called_once_with(
            ["echo", "hello"],
            cwd=str(tmp_path),
            capture_output=True,
            text=True
        )

class TestRenameArchitectureFolder:
    def test_success(self, tmp_path):
        src = tmp_path / "architecture"
        dst = tmp_path / "1. Generals_Code_GameEngine_Source_Common"
        src.mkdir()
        logger = MagicMock()
        rename_architecture_folder(tmp_path, 1, "Generals\\Code\\GameEngine\\Source\\Common", logger)
        assert not src.exists()
        assert dst.exists()

    def test_no_src(self, tmp_path):
        logger = MagicMock()
        with pytest.raises(FileNotFoundError):
            rename_architecture_folder(tmp_path, 1, "Generals\\Code\\GameEngine\\Source\\Common", logger)

    def test_dst_exists(self, tmp_path):
        src = tmp_path / "architecture"
        dst = tmp_path / "1. Generals_Code_GameEngine_Source_Common"
        src.mkdir()
        dst.mkdir()
        logger = MagicMock()
        with pytest.raises(FileExistsError):
            rename_architecture_folder(tmp_path, 1, "Generals\\Code\\GameEngine\\Source\\Common", logger)

    def test_dry_run(self, tmp_path):
        src = tmp_path / "architecture"
        src.mkdir()
        logger = MagicMock()
        rename_architecture_folder(tmp_path, 1, "Generals\\Code\\GameEngine\\Source\\Common", logger, dry_run=True)
        assert src.exists()

    def test_naming_format(self, tmp_path):
        src = tmp_path / "architecture"
        dst = tmp_path / "1. Generals_Code_GameEngine_Source_Common"
        src.mkdir()
        logger = MagicMock()
        rename_architecture_folder(tmp_path, 1, "Generals\\Code\\GameEngine\\Source\\Common", logger)
        assert not src.exists()
        assert dst.exists()

class TestRunOneTimeSteps:
    @patch("Arch_Analysis_Pipeline.run_command")
    def test_skip_lsp_skips(self, mock_run):
        repo_root = Path("/repo/root")
        logger = MagicMock()
        run_one_time_steps(repo_root, logger, skip_lsp=True)
        mock_run.assert_not_called()
        logger.info.assert_any_call("Skipping LSP steps")

    @patch("Arch_Analysis_Pipeline.run_command")
    def test_skip_lsp_runs(self, mock_run):
        repo_root = Path("/repo/root")
        logger = MagicMock()
        run_one_time_steps(repo_root, logger, dry_run=False, skip_lsp=False)
        assert mock_run.call_count == 2
        logger.info.assert_any_call("=== One-time setup steps ===")

class TestRunPipeline:
    @patch("Arch_Analysis_Pipeline.rename_architecture_folder")
    @patch("Arch_Analysis_Pipeline.build_command")
    @patch("Arch_Analysis_Pipeline.run_command")
    def test_two_subsections(self, mock_run, mock_build, mock_rename, tmp_path):
        subsections = ["Subsection1", "Subsection2"]
        logger = MagicMock()
        run_pipeline(tmp_path, subsections, logger)
        assert mock_run.call_count == 12
        assert mock_build.call_count == 12
        assert mock_rename.call_count == 2

    @patch("Arch_Analysis_Pipeline.rename_architecture_folder")
    @patch("Arch_Analysis_Pipeline.build_command")
    @patch("Arch_Analysis_Pipeline.run_command")
    def test_start_from(self, mock_run, mock_build, mock_rename, tmp_path):
        subsections = ["Subsection1", "Subsection2"]
        logger = MagicMock()
        run_pipeline(tmp_path, subsections, logger, start_from=2)
        assert mock_run.call_count == 6
        assert mock_build.call_count == 6
        assert mock_rename.call_count == 1

    @patch("Arch_Analysis_Pipeline.rename_architecture_folder")
    @patch("Arch_Analysis_Pipeline.build_command")
    @patch("Arch_Analysis_Pipeline.run_command")
    def test_correct_commands(self, mock_run, mock_build, mock_rename, tmp_path):
        subsections = ["Subsection1"]
        logger = MagicMock()
        run_pipeline(tmp_path, subsections, logger)
        for step in PIPELINE_STEPS:
            mock_build.assert_any_call(step, "Subsection1", tmp_path)

class TestParseArgs:
    def test_defaults(self):
        args = parse_args([])
        assert args.dry_run is False
        assert args.start_from == 1
        assert args.skip_lsp is False

    def test_dry_run(self):
        args = parse_args(["--dry-run"])
        assert args.dry_run is True

    def test_start_from(self):
        args = parse_args(["--start-from", "3"])
        assert args.start_from == 3

    def test_skip_lsp(self):
        args = parse_args(["--skip-lsp"])
        assert args.skip_lsp is True

    def test_invalid_start_from(self):
        with pytest.raises(SystemExit):
            parse_args(["--start-from", "0"])

class TestGetRepoRoot:
    def test_normal(self, tmp_path):
        # Create fake directory structure: tmp/LocalLLMAnalysis/fake_script.py + tmp/.env
        arch_dir = tmp_path / "LocalLLMAnalysis"
        arch_dir.mkdir()
        (tmp_path / ".env").touch()
        # Patch __file__ to point inside LocalLLMAnalysis
        with patch("Arch_Analysis_Pipeline.Path") as mock_path_cls:
            mock_file_path = MagicMock()
            mock_file_path.resolve.return_value.parent.parent = tmp_path
            mock_path_cls.return_value = mock_file_path
            mock_path_cls.__call__ = lambda self, x: mock_file_path
            # Just verify the real function works with the actual __file__
        # Simpler: just verify get_repo_root doesn't crash (it uses the real __file__)
        root = get_repo_root()
        assert (root / ".env").exists()

    def test_missing_env(self, tmp_path):
        with patch("Arch_Analysis_Pipeline.__file__", str(tmp_path / "LocalLLMAnalysis" / "Arch_Analysis_Pipeline.py")):
            with pytest.raises(FileNotFoundError):
                get_repo_root()

class TestMain:
    @patch("Arch_Analysis_Pipeline.parse_args")
    @patch("Arch_Analysis_Pipeline.get_repo_root")
    @patch("Arch_Analysis_Pipeline.setup_logging")
    @patch("Arch_Analysis_Pipeline.parse_subsections")
    @patch("Arch_Analysis_Pipeline.run_one_time_steps")
    @patch("Arch_Analysis_Pipeline.run_pipeline")
    def test_happy_path(self, mock_run_pipeline, mock_run_one_time_steps, mock_parse_subsections, mock_setup_logging, mock_get_repo_root, mock_parse_args):
        mock_parse_args.return_value = argparse.Namespace(dry_run=False, start_from=1, skip_lsp=False)
        mock_get_repo_root.return_value = Path("/repo/root")
        mock_setup_logging.return_value = MagicMock()
        mock_parse_subsections.return_value = ["Subsection1"]
        main()
        mock_run_one_time_steps.assert_called_once_with(Path("/repo/root"), mock_setup_logging.return_value, False, False)
        mock_run_pipeline.assert_called_once_with(Path("/repo/root"), ["Subsection1"], mock_setup_logging.return_value, False, 1)

    @patch("Arch_Analysis_Pipeline.parse_args")
    @patch("Arch_Analysis_Pipeline.get_repo_root")
    @patch("Arch_Analysis_Pipeline.setup_logging")
    @patch("Arch_Analysis_Pipeline.parse_subsections")
    @patch("Arch_Analysis_Pipeline.run_one_time_steps")
    @patch("Arch_Analysis_Pipeline.run_pipeline")
    def test_called_process_error(self, mock_run_pipeline, mock_run_one_time_steps, mock_parse_subsections, mock_setup_logging, mock_get_repo_root, mock_parse_args):
        mock_parse_args.return_value = argparse.Namespace(dry_run=False, start_from=1, skip_lsp=False)
        mock_get_repo_root.return_value = Path("/repo/root")
        mock_setup_logging.return_value = MagicMock()
        mock_parse_subsections.return_value = ["Subsection1"]
        mock_run_pipeline.side_effect = subprocess.CalledProcessError(1, "cmd", output="stdout", stderr="stderr")
        with pytest.raises(SystemExit) as excinfo:
            main()
        assert excinfo.value.code == 1

    @patch("Arch_Analysis_Pipeline.parse_args")
    @patch("Arch_Analysis_Pipeline.get_repo_root")
    @patch("Arch_Analysis_Pipeline.setup_logging")
    @patch("Arch_Analysis_Pipeline.parse_subsections")
    def test_empty_subsections(self, mock_parse_subsections, mock_setup_logging, mock_get_repo_root, mock_parse_args):
        mock_parse_args.return_value = argparse.Namespace(dry_run=False, start_from=1, skip_lsp=False)
        mock_get_repo_root.return_value = Path("/repo/root")
        mock_setup_logging.return_value = MagicMock()
        mock_parse_subsections.return_value = []
        with pytest.raises(SystemExit) as excinfo:
            main()
        assert excinfo.value.code == 1
