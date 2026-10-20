#!/usr/bin/env python3
"""Assemble the portable Windows bundle.

The runtime dependency closure is pure Python, so this script can assemble the
bundle on Linux, macOS, or Windows. The resulting bundle only runs on Windows.

The official CPython embeddable archive is downloaded from python.org and
verified against a pinned SHA-256 before it is used. Dependency wheels are
downloaded as Windows-compatible binaries and extracted without pip's target
directory handling, which keeps the bundle free of ``bin``/``Scripts``
launchers and bytecode caches.

Usage:
    python packaging/windows/build.py
    python packaging/windows/build.py --dist dist --no-zip
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tomllib
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

PYTHON_VERSION = "3.13.7"
PYTHON_MINOR = "3.13"
PYTHON_TAG = "313"
PYTHON_EMBED_FILENAME = f"python-{PYTHON_VERSION}-embed-amd64.zip"
PYTHON_EMBED_URL = (
    f"https://www.python.org/ftp/python/{PYTHON_VERSION}/{PYTHON_EMBED_FILENAME}"
)
PYTHON_EMBED_SHA256 = (
    "f6cca216a359be84797cabb54149ce5e062afb16cc7567eb7fc51cacb2d86b65"
)

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGING_DIR = Path(__file__).resolve().parent
LOCK_FILE = PACKAGING_DIR / "requirements.lock"

PTH_CONTENT = (
    f"python{PYTHON_TAG}.zip\n"
    ".\n"
    "Lib\\site-packages\n"
)

REQUIRED_PYTHON_FILES = (
    "python.exe",
    f"python{PYTHON_TAG}.dll",
    f"python{PYTHON_TAG}.zip",
    f"python{PYTHON_TAG}._pth",
    "vcruntime140.dll",
    "vcruntime140_1.dll",
)


def fail(message: str) -> None:
    raise SystemExit(f"error: {message}")


def run(command: list[str], **kwargs: object) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, check=True, **kwargs)  # type: ignore[arg-type]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download(url: str, destination: Path, expected_sha256: str) -> Path:
    if destination.is_file() and sha256_file(destination) == expected_sha256:
        print(f"Using cached {destination.name}")
        return destination

    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    print(f"Downloading {url}")
    with urllib.request.urlopen(url, timeout=60) as response, partial.open("wb") as out:
        shutil.copyfileobj(response, out)

    actual = sha256_file(partial)
    if actual != expected_sha256:
        partial.unlink(missing_ok=True)
        fail(
            f"SHA-256 mismatch for {url}\n"
            f"  expected: {expected_sha256}\n"
            f"  actual:   {actual}"
        )
    partial.replace(destination)
    return destination


def project_version() -> str:
    with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
        data = tomllib.load(handle)
    return data["project"]["version"]


def canonical_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def read_lock() -> list[tuple[str, str]]:
    requirements: list[tuple[str, str]] = []
    for line in LOCK_FILE.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        name, separator, version = line.partition("==")
        if not separator or not version:
            fail(f"unsupported requirements.lock line: {line!r}")
        requirements.append((canonical_name(name), version.strip()))
    if not requirements:
        fail(f"{LOCK_FILE} contains no requirements")
    return requirements


def clear_directory(directory: Path) -> None:
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True)


def extract_embed_python(archive: Path, python_dir: Path) -> None:
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(python_dir)


def download_dependencies(requirements: list[tuple[str, str]], wheel_dir: Path) -> None:
    clear_directory(wheel_dir)
    run(
        [
            sys.executable,
            "-m",
            "pip",
            "download",
            "--disable-pip-version-check",
            "--no-deps",
            "--only-binary=:all:",
            "--platform",
            "win_amd64",
            "--python-version",
            PYTHON_MINOR,
            "--implementation",
            "cp",
            "--abi",
            f"cp{PYTHON_TAG}",
            "--dest",
            str(wheel_dir),
            "-r",
            str(LOCK_FILE),
        ]
    )

    downloaded = sorted(wheel_dir.glob("*.whl"))
    if len(downloaded) != len(requirements):
        fail(
            "expected one wheel per locked requirement, "
            f"got {len(downloaded)} wheels for {len(requirements)} requirements"
        )


def build_app_wheel(wheel_dir: Path) -> Path:
    clear_directory(wheel_dir)
    run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--disable-pip-version-check",
            "--no-deps",
            "--wheel-dir",
            str(wheel_dir),
            str(REPO_ROOT),
        ]
    )
    wheels = sorted(wheel_dir.glob("massacre_mission_tracker-*.whl"))
    if len(wheels) != 1:
        fail(f"expected exactly one application wheel, found {len(wheels)}")
    return wheels[0]


def install_wheel(wheel: Path, site_packages: Path) -> None:
    """Extract a wheel into site-packages without installation metadata."""

    with zipfile.ZipFile(wheel) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            parts = PurePosixPath(info.filename).parts
            if not parts or "__pycache__" in parts:
                continue
            if info.filename.endswith((".pyc", ".pyo")):
                continue

            if parts[0].endswith(".data"):
                if len(parts) < 3 or parts[1] not in {"purelib", "platlib"}:
                    # Scripts, headers, and arbitrary data files are either
                    # regenerated by the launcher or not needed in the bundle.
                    continue
                parts = parts[2:]

            target = site_packages.joinpath(*parts)
            resolved = target.resolve()
            if site_packages.resolve() not in resolved.parents:
                fail(f"wheel {wheel.name} contains an unsafe path: {info.filename}")

            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("wb") as destination:
                shutil.copyfileobj(source, destination)


def write_crlf(path: Path, text: str) -> None:
    path.write_bytes(text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8"))


def read_dist_versions(site_packages: Path) -> dict[str, str]:
    versions: dict[str, str] = {}
    for metadata in site_packages.glob("*.dist-info/METADATA"):
        name: str | None = None
        version: str | None = None
        for line in metadata.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                break
            if line.startswith("Name: "):
                name = line[6:].strip()
            elif line.startswith("Version: "):
                version = line[9:].strip()
        if name and version:
            versions[canonical_name(name)] = version
    return versions


def remove_bytecode(stage: Path) -> None:
    for cache in sorted(stage.rglob("__pycache__")):
        shutil.rmtree(cache)
    for compiled in stage.rglob("*.py[co]"):
        compiled.unlink()


def validate(stage: Path, requirements: list[tuple[str, str]]) -> None:
    for name in ("massacre-tracker.cmd", "README.txt", "LICENSE"):
        if not (stage / name).is_file():
            fail(f"missing {stage / name}")

    python_dir = stage / "python"
    for name in REQUIRED_PYTHON_FILES:
        if not (python_dir / name).is_file():
            fail(f"missing {python_dir / name}")

    pth = python_dir / f"python{PYTHON_TAG}._pth"
    if pth.read_bytes() != PTH_CONTENT.replace("\n", "\r\n").encode("ascii"):
        fail(f"{pth} does not contain the expected paths")

    site_packages = python_dir / "Lib" / "site-packages"
    if not (site_packages / "massacre_tracker" / "ui" / "app.tcss").is_file():
        fail("massacre_tracker/ui/app.tcss is missing from the bundle")

    unexpected_native = [
        path
        for path in site_packages.rglob("*")
        if path.is_file() and path.suffix.lower() in {".pyd", ".dll", ".so", ".dylib"}
    ]
    if unexpected_native:
        listed = ", ".join(str(path.relative_to(stage)) for path in unexpected_native)
        fail(f"unexpected native binaries in site-packages: {listed}")

    if (site_packages / "bin").exists() or (site_packages / "Scripts").exists():
        fail("site-packages contains a bin or Scripts directory")

    path_files = list(site_packages.rglob("*.pth"))
    if path_files:
        listed = ", ".join(str(path.relative_to(stage)) for path in path_files)
        fail(
            "site-packages contains .pth files, but the embedded interpreter "
            f"does not process site packages: {listed}"
        )

    installed = read_dist_versions(site_packages)
    for name, version in requirements:
        actual = installed.get(name)
        if actual is None:
            fail(f"locked dependency {name}=={version} is not installed")
        if actual != version:
            fail(f"locked dependency {name}=={version} is installed as {actual}")

    for pattern in ("__pycache__", "*.pyc", "*.pyo"):
        leftovers = list(stage.rglob(pattern))
        if leftovers:
            fail(f"bundle contains bytecode artifacts: {leftovers[0]}")


def smoke_test(site_packages: Path) -> None:
    """Import the staged package tree with the host interpreter.

    This cannot prove the bundle runs on Windows, but it verifies that the
    wheels contain everything the application imports and that the stylesheet
    is packaged.
    """

    expected_root = os.path.realpath(site_packages)
    code = f"""
import os
import sys

sys.path.insert(0, {str(site_packages)!r})
import massacre_tracker
import rich
import textual
from importlib.metadata import version
from massacre_tracker.ui.app import TrackerApp

root = os.path.realpath({expected_root!r})
assert os.path.realpath(massacre_tracker.__file__).startswith(root), massacre_tracker.__file__
assert os.path.isfile(os.path.join({str(site_packages)!r}, "massacre_tracker", "ui", "app.tcss"))
print(
    "smoke test ok:",
    version("massacre-mission-tracker"),
    "textual", version("textual"),
    "rich", version("rich"),
)
"""
    run([sys.executable, "-B", "-X", "utf8", "-c", code], cwd=str(site_packages.parent))


def zip_bundle(stage: Path, archive: Path) -> None:
    archive.parent.mkdir(parents=True, exist_ok=True)
    if archive.exists():
        archive.unlink()

    with zipfile.ZipFile(
        archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as bundle:
        for path in sorted(stage.rglob("*")):
            if not path.is_file():
                continue
            info = zipfile.ZipInfo(
                path.relative_to(stage.parent).as_posix(),
                date_time=(1980, 1, 1, 0, 0, 0),
            )
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 0
            info.external_attr = 0o644 << 16
            with path.open("rb") as source, bundle.open(info, "w") as destination:
                shutil.copyfileobj(source, destination)


def directory_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}"
        value /= 1024
    raise AssertionError("unreachable")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dist",
        type=Path,
        default=REPO_ROOT / "dist",
        help="build output directory (default: dist)",
    )
    parser.add_argument(
        "--stage",
        type=Path,
        help="staging directory (default: DIST/massacre-tracker-VERSION-win64)",
    )
    parser.add_argument(
        "--no-zip",
        action="store_true",
        help="leave the staged folder in place without creating a zip",
    )
    parser.add_argument(
        "--skip-smoke",
        action="store_true",
        help="skip the host-interpreter import smoke test",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    dist: Path = args.dist.resolve()
    version = project_version()
    stage: Path = (
        args.stage.resolve()
        if args.stage
        else dist / f"massacre-tracker-{version}-win64"
    )

    if stage.name != f"massacre-tracker-{version}-win64":
        fail(f"refusing to use unexpected staging directory name: {stage.name}")
    if stage == REPO_ROOT or REPO_ROOT not in stage.parents:
        fail(f"refusing to write outside the repository: {stage}")

    requirements = read_lock()
    cache = dist / "cache"
    python_dir = stage / "python"
    site_packages = python_dir / "Lib" / "site-packages"

    print(f"Building Massacre Mission Tracker {version} Windows bundle")
    print(f"  upstream Python: {PYTHON_VERSION} (embeddable amd64)")
    print(f"  staging: {stage}")

    embed_archive = download(
        PYTHON_EMBED_URL, cache / PYTHON_EMBED_FILENAME, PYTHON_EMBED_SHA256
    )

    print("Staging embeddable Python")
    clear_directory(stage)
    extract_embed_python(archive=embed_archive, python_dir=python_dir)
    site_packages.mkdir(parents=True, exist_ok=True)

    print("Downloading locked dependencies")
    dependency_wheels = cache / "wheels"
    download_dependencies(requirements, dependency_wheels)

    print("Building the application wheel")
    app_wheel = build_app_wheel(cache / "app-wheel")

    print("Installing wheels")
    for wheel in sorted(dependency_wheels.glob("*.whl")):
        install_wheel(wheel, site_packages)
    install_wheel(app_wheel, site_packages)

    shutil.rmtree(site_packages / "bin", ignore_errors=True)
    shutil.rmtree(site_packages / "Scripts", ignore_errors=True)

    write_crlf(python_dir / f"python{PYTHON_TAG}._pth", PTH_CONTENT)
    write_crlf(
        stage / "massacre-tracker.cmd",
        (PACKAGING_DIR / "massacre-tracker.cmd").read_text(encoding="utf-8"),
    )
    shutil.copy2(PACKAGING_DIR / "README.txt", stage / "README.txt")
    shutil.copy2(REPO_ROOT / "LICENSE", stage / "LICENSE")

    remove_bytecode(stage)
    validate(stage, requirements)

    if not args.skip_smoke:
        print("Running host-interpreter smoke test")
        smoke_test(site_packages)

    print(f"Bundle size: {human_size(directory_size(stage))}")
    if args.no_zip:
        print(f"Bundle staged at {stage}")
        return

    archive = dist / f"{stage.name}.zip"
    print("Creating zip archive")
    zip_bundle(stage, archive)
    print(f"Archive: {archive} ({human_size(archive.stat().st_size)})")
    print(f"SHA-256: {sha256_file(archive)}")


if __name__ == "__main__":
    main()
