"""Unit and integration tests for cross-platform startup script suite.

Verifies:
- Script files exist and are well-formed (start.py, start.ps1, start.sh).
- Argument parser correctly parses all options.
- Banner formatting contains all required endpoint paths.
- Preflight validation (Python version check, dependencies check).
- start.py --check-only execution.
- Custom port and flag handling.
- PowerShell wrapper execution on Windows.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from start import (
    PROJECT_ROOT,
    REQUIRED_PACKAGES,
    check_dependencies,
    check_python_version,
    get_banner_text,
    main,
    parse_arguments,
)


def test_script_files_exist_and_non_empty():
    """Verify that start.py, start.ps1, and start.sh exist in project root and are non-empty."""
    scripts = ["start.py", "start.ps1", "start.sh"]
    for script_name in scripts:
        script_path = PROJECT_ROOT / script_name
        assert script_path.exists(), f"Expected {script_name} to exist at {script_path}"
        assert script_path.is_file(), f"Expected {script_name} to be a file"
        content = script_path.read_text(encoding="utf-8")
        assert len(content.strip()) > 0, f"Expected {script_name} to be non-empty"


def test_bash_script_format_and_shebang():
    """Verify start.sh has POSIX shebang and invokes start.py via exec."""
    bash_path = PROJECT_ROOT / "start.sh"
    content = bash_path.read_text(encoding="utf-8")
    assert content.startswith("#!/usr/bin/env bash")
    assert 'exec "$PY_BIN" "$SCRIPT_DIR/start.py"' in content
    assert "set -eo pipefail" in content


def test_powershell_script_parameters():
    """Verify start.ps1 contains required parameter definitions."""
    ps_path = PROJECT_ROOT / "start.ps1"
    content = ps_path.read_text(encoding="utf-8")
    assert "[string]$HostName" in content
    assert "[int]$Port" in content
    assert "[switch]$Reload" in content
    assert "[switch]$Seed" in content
    assert "[switch]$NoSeed" in content
    assert "[switch]$ResetAds" in content
    assert "[switch]$CheckOnly" in content
    assert 'start.py"' in content


def test_argument_parser_defaults():
    """Verify argument parser defaults."""
    args = parse_arguments([])
    assert isinstance(args.host, str)
    assert isinstance(args.port, int)
    assert args.reload is False
    assert args.seed is False
    assert args.no_seed is False
    assert args.reset_ads is False
    assert args.check_only is False
    assert args.log_level == "info"


def test_argument_parser_custom_values():
    """Verify argument parser parses all custom options."""
    args = parse_arguments([
        "--host", "127.0.0.1",
        "--port", "8899",
        "--reload",
        "--seed",
        "--reset-ads",
        "--check-only",
        "--log-level", "debug",
    ])
    assert args.host == "127.0.0.1"
    assert args.port == 8899
    assert args.reload is True
    assert args.seed is True
    assert args.reset_ads is True
    assert args.check_only is True
    assert args.log_level == "debug"


def test_argument_parser_no_flags():
    """Verify --no-reload and --no-seed flags."""
    args = parse_arguments(["--no-reload", "--no-seed"])
    assert args.reload is False
    assert args.no_seed is True


def test_argument_parser_invalid_log_level():
    """Verify argument parser rejects invalid log levels."""
    with pytest.raises(SystemExit):
        parse_arguments(["--log-level", "super_verbose"])


def test_banner_formatting_routes():
    """Verify banner text contains all required endpoints and clickable URLs."""
    banner = get_banner_text(host="0.0.0.0", port=8080, reload_enabled=False)

    # Required routes according to specification
    assert "/docs" in banner
    assert "/health" in banner
    assert "/badge/" in banner
    assert "/click/" in banner
    assert "/maintainers/" in banner
    assert "/revenue/" in banner

    # Host translation: 0.0.0.0 translates to clickable localhost
    assert "http://localhost:8080" in banner
    assert "Bound to 0.0.0.0:8080" in banner
    assert "Disabled" in banner


def test_banner_formatting_custom_host():
    """Verify banner text preserves custom host and enabled reload."""
    banner = get_banner_text(host="192.168.1.100", port=9000, reload_enabled=True)
    assert "http://192.168.1.100:9000" in banner
    assert "Enabled" in banner


def test_check_python_version_current():
    """Verify current Python version meets MIN_PYTHON requirement."""
    assert check_python_version() is True


def test_check_python_version_future_fails():
    """Verify that an unsatisfied Python version causes sys.exit(1)."""
    with pytest.raises(SystemExit) as exc_info:
        check_python_version(min_version=(3, 99))
    assert exc_info.value.code == 1


def test_check_dependencies_current():
    """Verify all required dependencies are present."""
    assert check_dependencies() is True
    assert "fastapi" in REQUIRED_PACKAGES
    assert "uvicorn" in REQUIRED_PACKAGES
    assert "sqlalchemy" in REQUIRED_PACKAGES


def test_check_dependencies_missing_fails():
    """Verify missing dependencies cause sys.exit(1)."""
    with pytest.raises(SystemExit) as exc_info:
        check_dependencies({"nonexistent_synthetic_pkg_xyz_99": "Test Missing Package"})
    assert exc_info.value.code == 1


def test_main_check_only_direct():
    """Verify calling main(['--check-only']) returns 0."""
    exit_code = main(["--check-only"])
    assert exit_code == 0


def test_subprocess_check_only():
    """Verify running 'python start.py --check-only' succeeds with exit code 0."""
    proc = subprocess.run(
        [sys.executable, "start.py", "--check-only"],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, f"Process failed with stderr: {proc.stderr}"
    assert "[OK]" in proc.stdout or "preflight checks passed" in proc.stdout


def test_subprocess_custom_port_check_only():
    """Verify 'python start.py --port 8899 --check-only' succeeds with exit code 0."""
    proc = subprocess.run(
        [sys.executable, "start.py", "--port", "8899", "--check-only"],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, f"Process failed with stderr: {proc.stderr}"


def test_subprocess_no_seed_check_only():
    """Verify 'python start.py --no-seed --check-only' succeeds with exit code 0."""
    proc = subprocess.run(
        [sys.executable, "start.py", "--no-seed", "--check-only"],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, f"Process failed with stderr: {proc.stderr}"


@pytest.mark.skipif(shutil.which("powershell") is None, reason="PowerShell is not installed")
def test_powershell_script_check_only():
    """Verify PowerShell wrapper execution with -CheckOnly parameter."""
    proc = subprocess.run(
        ["powershell", "-ExecutionPolicy", "Bypass", "-File", ".\\start.ps1", "-CheckOnly"],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, f"PowerShell failed with stderr: {proc.stderr}"


def _find_bash_executable() -> str | None:
    if sys.platform != "win32":
        return shutil.which("bash")
    # On Windows, prioritize Git Bash over WSL relay if present
    git_bash = r"C:\Program Files\Git\bin\bash.exe"
    if Path(git_bash).exists():
        return git_bash
    return shutil.which("bash")


@pytest.mark.skipif(_find_bash_executable() is None, reason="Bash is not available")
def test_bash_script_check_only():
    """Verify Bash wrapper execution with --check-only parameter."""
    bash_bin = _find_bash_executable()
    proc = subprocess.run(
        [bash_bin, "start.sh", "--check-only"],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, f"Bash failed with stderr: {proc.stderr}"

