"""Guard the Windows release packaging against a stray service asset.

Regression (0.8.34): PyInstaller's --onefile output sat at dist/ root and the
CI artifact glob `dist/*.exe` swept it up, so the Release page listed a
standalone MouseShareService.exe next to the installer even though the service
is already bundled inside the Setup exe. Two defences:
  * build.py removes the dist/ root leftover after copying it into the bundle
  * build.yml matches only the Setup installer, not every *.exe
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def test_workflow_does_not_glob_all_root_exes():
    build_yml = _read(".github/workflows/build.yml")
    # The old overly-broad pattern must be gone...
    assert "dist/*.exe" not in build_yml
    # ...replaced by an installer-specific pattern.
    assert "dist/MouseShare-*-Windows-x64-Setup.exe" in build_yml


def test_build_script_removes_service_leftover():
    build_py = _read("scripts/build.py")
    assert 'shutil.copyfile(DIST / "MouseShareService.exe"' in build_py
    assert '(DIST / "MouseShareService.exe").unlink()' in build_py


def test_service_is_bundled_into_installer():
    """The installer still ships the service from inside the bundle."""
    iss = _read("packaging/windows.iss")
    assert 'Source: "{#SourceDir}\\*"' in iss
    assert 'Filename: "{app}\\MouseShareService.exe"; Parameters: "--install"' in iss
