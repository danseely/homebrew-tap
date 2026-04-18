#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import re
import tarfile
from collections import deque
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised via subprocess tests
    tomllib = None


class FormulaUpdateError(RuntimeError):
    """Raised when the release tarball cannot be rendered into a formula."""


TARGET_PYTHON_VERSION = "3.13"
TARGET_PYTHON_FULL_VERSION = "3.13.0"

MARKER_ENV = {
    "implementation_name": "cpython",
    "platform_python_implementation": "CPython",
    "python_version": TARGET_PYTHON_VERSION,
    "python_full_version": TARGET_PYTHON_FULL_VERSION,
    "sys_platform": "linux",
}

VERSION_MARKER_KEYS = {"python_version", "python_full_version"}


def compute_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def extract_lockfile(tarball_path: Path) -> dict:
    if tomllib is None:
        raise FormulaUpdateError("update-formula-from-release.py requires Python 3.11+")

    with tarfile.open(tarball_path, "r:gz") as archive:
        lock_member = next(
            (member for member in archive.getmembers() if member.name.endswith("/uv.lock")),
            None,
        )
        if lock_member is None:
            raise FormulaUpdateError("release tarball does not contain uv.lock")

        lockfile = archive.extractfile(lock_member)
        if lockfile is None:
            raise FormulaUpdateError("uv.lock was not readable from the release tarball")

        try:
            return tomllib.loads(lockfile.read().decode())
        except tomllib.TOMLDecodeError as exc:
            raise FormulaUpdateError(f"uv.lock is malformed: {exc}") from exc


def dependency_name(dependency: dict) -> str:
    name = dependency.get("name")
    if not isinstance(name, str) or not name:
        raise FormulaUpdateError(f"dependency entry is missing a valid name: {dependency!r}")
    return name


def parse_version_marker_value(value: str) -> tuple[int, ...]:
    if not re.fullmatch(r"\d+(?:\.\d+)*", value):
        raise FormulaUpdateError(f"unsupported dependency marker version: {value}")
    return tuple(int(part) for part in value.split("."))


def compare_marker_values(key: str, actual: str, expected: str) -> int:
    if key in VERSION_MARKER_KEYS:
        actual_parts = parse_version_marker_value(actual)
        expected_parts = parse_version_marker_value(expected)
        width = max(len(actual_parts), len(expected_parts))
        actual_value = actual_parts + (0,) * (width - len(actual_parts))
        expected_value = expected_parts + (0,) * (width - len(expected_parts))
        return (actual_value > expected_value) - (actual_value < expected_value)

    return (actual > expected) - (actual < expected)


def marker_applies(marker: str | None) -> bool:
    if not marker:
        return True

    match = re.fullmatch(r"([a-z_]+)\s*(==|!=|<|<=|>|>=)\s*'([^']+)'", marker.strip())
    if match is None:
        raise FormulaUpdateError(f"unsupported dependency marker: {marker}")

    key, operator, expected = match.groups()
    actual = MARKER_ENV.get(key)
    if actual is None:
        raise FormulaUpdateError(f"unsupported dependency marker variable: {key}")

    comparison = compare_marker_values(key, actual, expected)

    if operator == "==":
        return comparison == 0
    if operator == "!=":
        return comparison != 0
    if operator == "<":
        return comparison < 0
    if operator == "<=":
        return comparison <= 0
    if operator == ">":
        return comparison > 0
    if operator == ">=":
        return comparison >= 0

    raise FormulaUpdateError(f"unsupported dependency marker operator: {operator}")


def resource_packages(lockfile: dict) -> list[dict]:
    packages = lockfile.get("package")
    if not isinstance(packages, list) or not packages:
        raise FormulaUpdateError("uv.lock does not contain any package metadata")

    by_name: dict[str, dict] = {}
    root_package: dict | None = None
    for package in packages:
        name = package.get("name")
        if not isinstance(name, str) or not name:
            raise FormulaUpdateError(f"package entry is missing a valid name: {package!r}")
        by_name[name] = package
        if package.get("source", {}).get("editable") == ".":
            root_package = package

    if root_package is None:
        raise FormulaUpdateError("uv.lock does not define the editable root package")

    queue = deque(
        dependency_name(dep)
        for dep in root_package.get("dependencies", [])
        if marker_applies(dep.get("marker"))
    )
    seen: set[str] = set()

    while queue:
        name = queue.popleft()
        if name in seen:
            continue
        seen.add(name)

        package = by_name.get(name)
        if package is None:
            raise FormulaUpdateError(f"missing package metadata for dependency: {name}")

        for dependency in package.get("dependencies", []):
            if marker_applies(dependency.get("marker")):
                queue.append(dependency_name(dependency))

    return [by_name[name] for name in sorted(seen)]


def build_resource_blocks(packages: list[dict]) -> str:
    blocks: list[str] = []
    for package in packages:
        resource_name = package["name"]
        sdist = package.get("sdist")
        if not isinstance(sdist, dict):
            raise FormulaUpdateError(f"package {resource_name} is missing sdist metadata")

        url = sdist.get("url")
        if not isinstance(url, str) or not url:
            raise FormulaUpdateError(f"package {resource_name} is missing an sdist url")

        hash_value = sdist.get("hash")
        if not isinstance(hash_value, str) or not hash_value.startswith("sha256:"):
            raise FormulaUpdateError(
                f"package {resource_name} is missing a sha256 sdist hash"
            )

        sha256 = hash_value.removeprefix("sha256:")
        blocks.extend(
            [
                f'  resource "{resource_name}" do',
                f'    url "{url}"',
                f'    sha256 "{sha256}"',
                "  end",
                "",
            ]
        )

    return "\n".join(blocks).rstrip() + ("\n" if blocks else "")


def replace_once(text: str, pattern: str, replacement: str, *, label: str) -> str:
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.MULTILINE)
    if count != 1:
        raise FormulaUpdateError(f"could not locate {label} in formula")
    return updated


def rewrite_formula(
    formula_path: Path,
    *,
    tarball_url: str,
    tarball_sha256: str,
    resource_blocks: str,
) -> None:
    text = formula_path.read_text()
    install_marker = "\n  def install"
    install_start = text.find(install_marker)
    if install_start == -1:
        raise FormulaUpdateError("formula is missing a def install block")

    resource_marker = '\n  resource "'
    resource_start = text.find(resource_marker)
    if resource_start == -1:
        header = text[:install_start]
    else:
        header = text[:resource_start]

    header = replace_once(
        header,
        r'^  url ".*"$',
        f'  url "{tarball_url}"',
        label="formula url",
    )
    header = replace_once(
        header,
        r'^  sha256 ".*"$',
        f'  sha256 "{tarball_sha256}"',
        label="formula sha256",
    )

    tail = text[install_start + 1 :]
    separator = resource_blocks if resource_blocks else "\n"
    formula_path.write_text(header + separator + tail)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--formula", required=True, type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--tarball", required=True, type=Path)
    parser.add_argument("--tarball-url", required=True)
    args = parser.parse_args()

    if not args.version.strip():
        raise SystemExit("version must not be empty")

    try:
        tarball_sha256 = compute_sha256(args.tarball)
        lockfile = extract_lockfile(args.tarball)
        packages = resource_packages(lockfile)
        rewrite_formula(
            args.formula,
            tarball_url=args.tarball_url,
            tarball_sha256=tarball_sha256,
            resource_blocks=build_resource_blocks(packages),
        )
    except FormulaUpdateError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
