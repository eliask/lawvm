from __future__ import annotations

import hashlib
import io
import json
import re
import subprocess
import tarfile
from pathlib import Path


_MARKDOWN_LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def test_tracked_markdown_local_file_links_resolve() -> None:
    broken: list[str] = []
    for path in _tracked_markdown_files():
        text = path.read_text(encoding="utf-8")
        for match in _MARKDOWN_LINK_RE.finditer(text):
            target = match.group(1).split("#", 1)[0]
            if not target or "://" in target or target.startswith("mailto:"):
                continue
            target_path = Path(target)
            if target_path.is_absolute():
                broken.append(f"{path}: absolute local link {target}")
                continue
            if not (path.parent / target_path).exists():
                broken.append(f"{path}: missing local link {target}")
    assert broken == []


def test_release_docs_do_not_expose_developer_local_paths() -> None:
    offenders: list[str] = []
    for path in _tracked_markdown_files():
        text = path.read_text(encoding="utf-8")
        if "/home/" in text or "/Users/" in text:
            offenders.append(str(path))
    assert offenders == []


def test_release_scripts_have_valid_shell_syntax() -> None:
    subprocess.run(("bash", "-n", "scripts/ci.sh"), check=True)
    subprocess.run(("bash", "-n", "scripts/ci_sharded.sh"), check=True)
    subprocess.run(("bash", "-n", "scripts/test_shard.sh"), check=True)
    subprocess.run(("bash", "-n", "scripts/release_hygiene.sh"), check=True)
    subprocess.run(("bash", "-n", "scripts/build_release_archive.sh"), check=True)
    subprocess.run(("bash", "-n", "scripts/verify_release_archive.sh"), check=True)


def test_canonical_ci_validates_pytest_shard_ownership() -> None:
    script = Path("scripts/ci.sh").read_text(encoding="utf-8")

    assert "./scripts/test_shard.sh validate" in script
    assert "scripts/test_shard.py" in script
    assert "FAIL: pytest shard ownership is invalid." in script


def test_sharded_ci_supports_affected_path_selection() -> None:
    script = Path("scripts/ci_sharded.sh").read_text(encoding="utf-8")

    assert "--affected" in script
    assert "./scripts/test_shard.sh affected" in script
    assert "LAWVM_CI_AFFECTED_PATHS" in script


def test_release_archive_script_emits_verification_sidecars() -> None:
    script = Path("scripts/build_release_archive.sh").read_text(encoding="utf-8")
    assert 'sha256sum "$out"' in script
    assert '>"${out}.sha256"' in script
    assert ".manifest.json" in script
    assert '"git_commit"' in script
    assert './scripts/verify_release_archive.sh "$out"' in script


def test_release_archive_verifier_checks_sidecars(tmp_path) -> None:
    archive = tmp_path / "lawvm-abcdef1.tar.gz"
    prefix = "lawvm-abcdef1/"
    data = b"hello\n"
    with tarfile.open(archive, "w:gz") as tar:
        root = tarfile.TarInfo(prefix.rstrip("/"))
        root.type = tarfile.DIRTYPE
        tar.addfile(root)
        info = tarfile.TarInfo(prefix + "README.md")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(archive.suffix + ".sha256").write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    archive.with_suffix(archive.suffix + ".manifest.json").write_text(
        json.dumps(
            {
                "archive": archive.name,
                "archive_prefix": prefix,
                "git_commit": "abcdef1" + "0" * 33,
                "git_short": "abcdef1",
                "sha256": digest,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    subprocess.run(("bash", "scripts/verify_release_archive.sh", str(archive)), check=True)

    archive.with_suffix(archive.suffix + ".manifest.json").write_text(
        json.dumps(
            {
                "archive": archive.name,
                "archive_prefix": prefix,
                "git_commit": "abcdef1" + "0" * 33,
                "git_short": "abcdef1",
                "sha256": "0" * 64,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    result = subprocess.run(("bash", "scripts/verify_release_archive.sh", str(archive)), check=False)
    assert result.returncode == 1


def test_release_archive_verifier_rejects_unsafe_link_targets(tmp_path) -> None:
    archive = tmp_path / "lawvm-abcdef1.tar.gz"
    prefix = "lawvm-abcdef1/"
    with tarfile.open(archive, "w:gz") as tar:
        root = tarfile.TarInfo(prefix.rstrip("/"))
        root.type = tarfile.DIRTYPE
        tar.addfile(root)
        link = tarfile.TarInfo(prefix + "escape")
        link.type = tarfile.SYMTYPE
        link.linkname = "../outside"
        tar.addfile(link)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(archive.suffix + ".sha256").write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    archive.with_suffix(archive.suffix + ".manifest.json").write_text(
        json.dumps(
            {
                "archive": archive.name,
                "archive_prefix": prefix,
                "git_commit": "abcdef1" + "0" * 33,
                "git_short": "abcdef1",
                "sha256": digest,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = subprocess.run(("bash", "scripts/verify_release_archive.sh", str(archive)), check=False)
    assert result.returncode == 1


def test_release_hygiene_blocks_tracked_local_artifact_paths() -> None:
    script = Path("scripts/release_hygiene.sh").read_text(encoding="utf-8")
    assert '".tmp/"' in script
    assert '".farchive"' in script
    assert '".sqlite"' in script
    assert '".duckdb"' in script
    assert '".parquet"' in script
    assert "FAIL: tracked generated/local artifact paths found" in script


def _tracked_markdown_files() -> tuple[Path, ...]:
    output = subprocess.check_output(("git", "ls-files", "*.md"), text=True)
    return tuple(Path(line) for line in output.splitlines() if line)
