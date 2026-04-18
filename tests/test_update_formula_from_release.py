from __future__ import annotations

import hashlib
import io
import shutil
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import unittest
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "update-formula-from-release.py"
)
TARBALL_URL = "https://api.github.com/repos/danseely/agendum/tarball/v0.2.0"


def find_python(*candidates: str) -> str | None:
    for candidate in candidates:
        resolved = shutil.which(candidate)
        if resolved is not None:
            return resolved
    return None


def updater_python() -> str:
    if sys.version_info >= (3, 11):
        return sys.executable

    resolved = find_python("python3.13", "python3.12", "python3.11")
    if resolved is None:
        raise unittest.SkipTest("python3.11+ is required for updater tests")
    return resolved


RUNTIME_GUARD_PYTHON = find_python("python3.10")
FORMULA_TEMPLATE = textwrap.dedent(
    """\
    class Agendum < Formula
      include Language::Python::Virtualenv

      desc "Terminal dashboard for GitHub PRs, issues, and tasks"
      homepage "https://github.com/danseely/agendum"
      url "https://github.com/danseely/agendum/archive/refs/tags/v0.1.1.tar.gz"
      sha256 "oldsha256"
      license "Apache-2.0"

      depends_on "gh"
      depends_on "python@3.13"

      resource "legacy" do
        url "https://example.com/legacy-1.0.0.tar.gz"
        sha256 "legacysha"
      end

      def install
        virtualenv_install_with_resources
      end

      test do
        ENV["HOME"] = testpath
        assert_match version.to_s, shell_output("#{bin}/agendum --version")
        system bin/"agendum", "self-check"
        assert_predicate testpath/".agendum/agendum.db", :exist?
      end
    end
    """
)


class UpdateFormulaFromReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)

    def write_formula(self) -> Path:
        formula_path = self.root / "Formula" / "agendum.rb"
        formula_path.parent.mkdir(parents=True, exist_ok=True)
        formula_path.write_text(FORMULA_TEMPLATE)
        return formula_path

    def write_tarball(self, *, lockfile_text: str | None) -> Path:
        tarball_path = self.root / "agendum-v0.2.0.tar.gz"
        with tarfile.open(tarball_path, "w:gz") as archive:
            if lockfile_text is not None:
                lockfile_bytes = lockfile_text.encode()
                info = tarfile.TarInfo("agendum-0.2.0/uv.lock")
                info.size = len(lockfile_bytes)
                archive.addfile(info, io.BytesIO(lockfile_bytes))
        return tarball_path

    def run_script(
        self,
        formula_path: Path,
        tarball_path: Path,
        *,
        python: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        if python is None:
            python = updater_python()

        return subprocess.run(
            [
                python,
                str(SCRIPT_PATH),
                "--formula",
                str(formula_path),
                "--version",
                "0.2.0",
                "--tarball",
                str(tarball_path),
                "--tarball-url",
                TARBALL_URL,
            ],
            capture_output=True,
            text=True,
        )

    def test_rewrites_formula_header_and_resources_deterministically(self) -> None:
        formula_path = self.write_formula()
        tarball_path = self.write_tarball(
            lockfile_text=textwrap.dedent(
                """\
                version = 1

                [[package]]
                name = "agendum"
                version = "0.2.0"
                source = { editable = "." }
                dependencies = [
                  { name = "textual" },
                  { name = "anyio" },
                ]

                [[package]]
                name = "anyio"
                version = "4.13.0"
                dependencies = [{ name = "idna" }]
                sdist = { url = "https://example.com/anyio-4.13.0.tar.gz", hash = "sha256:anyiohash" }

                [[package]]
                name = "idna"
                version = "3.11"
                dependencies = []
                sdist = { url = "https://example.com/idna-3.11.tar.gz", hash = "sha256:idnahash" }

                [[package]]
                name = "textual"
                version = "8.2.3"
                dependencies = []
                sdist = { url = "https://example.com/textual-8.2.3.tar.gz", hash = "sha256:textualhash" }
                """
            )
        )

        result = self.run_script(formula_path, tarball_path)

        self.assertEqual(result.returncode, 0, result.stderr)
        updated = formula_path.read_text()
        expected_sha256 = hashlib.sha256(tarball_path.read_bytes()).hexdigest()

        self.assertIn(f'url "{TARBALL_URL}"', updated)
        self.assertIn(f'sha256 "{expected_sha256}"', updated)
        self.assertNotIn('resource "legacy"', updated)
        self.assertLess(updated.index('resource "anyio"'), updated.index('resource "idna"'))
        self.assertLess(updated.index('resource "idna"'), updated.index('resource "textual"'))

    def test_preserves_install_and_test_blocks(self) -> None:
        formula_path = self.write_formula()
        tarball_path = self.write_tarball(
            lockfile_text=textwrap.dedent(
                """\
                version = 1

                [[package]]
                name = "agendum"
                version = "0.2.0"
                source = { editable = "." }
                dependencies = [{ name = "rich" }]

                [[package]]
                name = "rich"
                version = "14.3.4"
                dependencies = []
                sdist = { url = "https://example.com/rich-14.3.4.tar.gz", hash = "sha256:richhash" }
                """
            )
        )

        result = self.run_script(formula_path, tarball_path)

        self.assertEqual(result.returncode, 0, result.stderr)
        updated = formula_path.read_text()
        self.assertIn('depends_on "gh"', updated)
        self.assertIn("virtualenv_install_with_resources", updated)
        self.assertIn('ENV["HOME"] = testpath', updated)
        self.assertIn('assert_predicate testpath/".agendum/agendum.db", :exist?', updated)

    def test_fails_when_uv_lock_is_missing(self) -> None:
        formula_path = self.write_formula()
        tarball_path = self.write_tarball(lockfile_text=None)

        result = self.run_script(formula_path, tarball_path)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not contain uv.lock", result.stderr)

    def test_fails_when_dependency_metadata_is_missing(self) -> None:
        formula_path = self.write_formula()
        tarball_path = self.write_tarball(
            lockfile_text=textwrap.dedent(
                """\
                version = 1

                [[package]]
                name = "agendum"
                version = "0.2.0"
                source = { editable = "." }
                dependencies = [{ name = "rich" }]
                """
            )
        )

        result = self.run_script(formula_path, tarball_path)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing package metadata for dependency: rich", result.stderr)

    def test_fails_when_required_dependency_has_no_sdist(self) -> None:
        formula_path = self.write_formula()
        tarball_path = self.write_tarball(
            lockfile_text=textwrap.dedent(
                """\
                version = 1

                [[package]]
                name = "agendum"
                version = "0.2.0"
                source = { editable = "." }
                dependencies = [{ name = "rich" }]

                [[package]]
                name = "rich"
                version = "14.3.4"
                dependencies = []
                """
            )
        )

        result = self.run_script(formula_path, tarball_path)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("package rich is missing sdist metadata", result.stderr)

    def test_skips_dependencies_filtered_out_by_markers(self) -> None:
        formula_path = self.write_formula()
        tarball_path = self.write_tarball(
            lockfile_text=textwrap.dedent(
                """\
                version = 1

                [[package]]
                name = "agendum"
                version = "0.2.0"
                source = { editable = "." }
                dependencies = [{ name = "mcp" }]

                [[package]]
                name = "mcp"
                version = "1.27.0"
                dependencies = [
                  { name = "pywin32", marker = "sys_platform == 'win32'" },
                  { name = "typing-extensions", marker = "python_version == '3.12'" },
                  { name = "pygments", marker = "python_version == '3.13'" },
                  { name = "rich" },
                ]
                sdist = { url = "https://example.com/mcp-1.27.0.tar.gz", hash = "sha256:mcphash" }

                [[package]]
                name = "pywin32"
                version = "311"
                wheels = [
                  { url = "https://example.com/pywin32.whl", hash = "sha256:wheelhash" },
                ]

                [[package]]
                name = "rich"
                version = "14.3.4"
                dependencies = []
                sdist = { url = "https://example.com/rich-14.3.4.tar.gz", hash = "sha256:richhash" }

                [[package]]
                name = "pygments"
                version = "2.19.1"
                dependencies = []
                sdist = { url = "https://example.com/pygments-2.19.1.tar.gz", hash = "sha256:pygmentshash" }
                """
            )
        )

        result = self.run_script(formula_path, tarball_path)

        self.assertEqual(result.returncode, 0, result.stderr)
        updated = formula_path.read_text()
        self.assertIn('resource "mcp"', updated)
        self.assertIn('resource "pygments"', updated)
        self.assertIn('resource "rich"', updated)
        self.assertNotIn('resource "pywin32"', updated)
        self.assertNotIn('resource "typing-extensions"', updated)

    def test_requires_python_3_11_plus_runtime(self) -> None:
        if RUNTIME_GUARD_PYTHON is None:
            self.skipTest("python3.10 is not available to exercise the runtime guard")

        formula_path = self.write_formula()
        tarball_path = self.write_tarball(lockfile_text=None)

        result = self.run_script(formula_path, tarball_path, python=RUNTIME_GUARD_PYTHON)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires Python 3.11+", result.stderr)


if __name__ == "__main__":
    unittest.main()
