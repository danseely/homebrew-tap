#!/usr/bin/env python3

from __future__ import annotations

import argparse
import tarfile
from collections import deque
from pathlib import Path
import tomllib


def extract_lockfile(tarball_path: Path) -> dict:
    with tarfile.open(tarball_path, "r:gz") as archive:
        lock_member = next(
            member for member in archive.getmembers() if member.name.endswith("/uv.lock")
        )
        lockfile = archive.extractfile(lock_member)
        if lockfile is None:
            raise SystemExit("uv.lock was not readable from the release tarball")
        return tomllib.loads(lockfile.read().decode())


def resource_packages(lockfile: dict) -> list[dict]:
    packages = lockfile["package"]
    by_name = {package["name"]: package for package in packages}
    root = next(
        package
        for package in packages
        if package.get("source", {}).get("editable") == "."
    )

    queue = deque(dependency["name"] for dependency in root.get("dependencies", []))
    seen: set[str] = set()

    while queue:
        name = queue.popleft()
        if name in seen:
            continue
        seen.add(name)

        package = by_name.get(name)
        if package is None:
            raise SystemExit(f"missing package metadata for dependency: {name}")

        for dependency in package.get("dependencies", []):
            queue.append(dependency["name"])

    return [by_name[name] for name in sorted(seen)]


def build_resource_blocks(packages: list[dict]) -> str:
    blocks: list[str] = []
    for package in packages:
        sdist = package.get("sdist")
        if not sdist:
            continue

        resource_name = package["name"]
        sha256 = sdist["hash"].removeprefix("sha256:")
        blocks.extend(
            [
                f'  resource "{resource_name}" do',
                f'    url "{sdist["url"]}"',
                f'    sha256 "{sha256}"',
                "  end",
                "",
            ]
        )

    return "\n".join(blocks).rstrip() + "\n"


def rewrite_formula(formula_path: Path, resource_blocks: str) -> None:
    text = formula_path.read_text()
    resource_start = text.index('  resource "')
    install_start = text.index("\n  def install")
    updated = text[:resource_start] + resource_blocks + text[install_start + 1 :]
    formula_path.write_text(updated)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--formula", required=True, type=Path)
    parser.add_argument("--tarball", required=True, type=Path)
    args = parser.parse_args()

    lockfile = extract_lockfile(args.tarball)
    packages = resource_packages(lockfile)
    rewrite_formula(args.formula, build_resource_blocks(packages))


if __name__ == "__main__":
    main()
