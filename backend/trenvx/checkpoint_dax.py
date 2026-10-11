"""Seal one complete guest root overlay upper as an immutable DAX layer."""
from __future__ import annotations

import os
import json
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path


def image_sha256(image: Path) -> str:
    return subprocess.check_output(["sha256sum", str(image)], text=True, timeout=30).split()[0]


def prepare_upper_copy(image: Path) -> tuple[Path, dict]:
    """Replay only a private copy; preserve input and processing evidence."""
    if image.is_symlink():
        raise ValueError("captured image must not be a symlink")
    image = image.resolve(strict=True)
    if not image.is_file():
        raise ValueError("upper must be an inactive regular captured image")
    if subprocess.check_output(["losetup", "-j", str(image)], text=True, timeout=10).strip():
        raise ValueError("captured upper is still attached")
    work = Path(tempfile.mkdtemp(prefix="journal-copy-", dir=image.parent))
    clone = work / "upper.ext4"
    audit = dict(status="FAIL", source=str(image), private_copy=str(clone),
                 processing="journal-only; no general repair", source_sha256=image_sha256(image))
    def run(name, argv, timeout=30):
        with (work / (name + ".log")).open("x") as stream:
            result = subprocess.run(argv, stdout=stream, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, timeout=timeout)
        return result.returncode
    try:
        subprocess.run(["cp", "--reflink=never", "--sparse=always", str(image), str(clone)],
                       check=True, timeout=30)
        if image_sha256(clone) != audit["source_sha256"]:
            raise RuntimeError("private journal copy differs from captured image")
        code = run("superblock-before", ["dumpe2fs", "-h", str(clone)])
        if code: raise RuntimeError("cannot read copied ext4 superblock")
        before = (work / "superblock-before.log").read_text()
        audit["needs_recovery_before"] = "needs_recovery" in before
        audit["journal_exit_code"] = run("journal-only", ["e2fsck", "-p", "-E", "journal_only", str(clone)])
        if audit["journal_exit_code"] not in (0, 1):
            raise RuntimeError("private-copy journal replay failed")
        code = run("superblock-after", ["dumpe2fs", "-h", str(clone)])
        if code: raise RuntimeError("cannot read replayed ext4 superblock")
        audit["needs_recovery_after"] = "needs_recovery" in (work / "superblock-after.log").read_text()
        if audit["needs_recovery_after"]:
            raise RuntimeError("private copy still requires journal recovery")
        audit["readonly_fsck_exit_code"] = run("readonly-fsck", ["e2fsck", "-f", "-n", str(clone)], timeout=60)
        check = (work / "readonly-fsck.log").read_text()
        audit["allocation_count_warnings"] = [line for line in check.splitlines() if "count wrong" in line]
        audit["filesystem_fully_clean"] = (audit["readonly_fsck_exit_code"] == 0 and
                                              "Fix? no" not in check and not audit["allocation_count_warnings"])
        # Counters may lag on a live captured FS. Do not repair them or call them clean.
        questions = [line for line in check.splitlines() if "? no" in line and line.strip() != "Fix? no"]
        if (audit["readonly_fsck_exit_code"] != 0 or questions or
                ("Fix? no" in check and not audit["allocation_count_warnings"])):
            raise RuntimeError("private-copy readonly fsck found unresolved errors")
        audit["status"] = "PASS" if audit["filesystem_fully_clean"] else "COUNTER_WARNINGS"
        return clone, audit
    except BaseException as error:
        audit["error"] = repr(error)
        raise
    finally:
        audit["source_sha256_after"] = image_sha256(image)
        audit["source_unchanged"] = audit["source_sha256_after"] == audit["source_sha256"]
        if clone.exists(): audit["private_copy_sha256"] = image_sha256(clone)
        if not audit["source_unchanged"]: audit["status"] = "FAIL"
        (work / "journal.json").write_text(json.dumps(audit, indent=2) + "\n")
        if not audit["source_unchanged"]:
            raise RuntimeError("captured source image changed during journal handling")


def archive_upper(image: Path, archive: Path) -> None:
    """Archive from a journal-replayed private image, preserving overlay metadata."""
    clone, audit = prepare_upper_copy(image)
    evidence = clone.parent / "archive.json"
    mountpoint = Path(tempfile.mkdtemp(prefix="mixfs-exp5-upper-"))
    mounted = False
    row = dict(status="FAIL", image=str(clone), archive=str(archive), journal_status=audit["status"])
    try:
        subprocess.run(["sudo", "-n", "mount", "-o", "loop,ro,noload", str(clone), str(mountpoint)], check=True, timeout=15)
        mounted = True
        root = mountpoint / "root"
        if not root.is_dir():
            raise RuntimeError(f"overlay upper has no root directory: {image}")
        subprocess.run([
            "sudo", "-n", "tar", "--xattrs", "--xattrs-include=*", "--acls",
            "--numeric-owner", "-C", str(root), "-cf", str(archive), ".",
        ], check=True, timeout=60)
        subprocess.run(["sudo", "-n", "chown", f"{os.getuid()}:{os.getgid()}", str(archive)], check=True, timeout=10)
        row.update(status="PASS", root_inode=root.stat().st_ino, archive_bytes=archive.stat().st_size,
                   archive_sha256=image_sha256(archive))
    except BaseException as error:
        row["error"] = repr(error)
        raise
    finally:
        try:
            if mounted:
                subprocess.run(["sudo", "-n", "umount", str(mountpoint)], check=True, timeout=15)
            mountpoint.rmdir()
            row["mount_removed"] = True
        except BaseException as error:
            row.update(status="FAIL", cleanup_error=repr(error))
            raise
        finally:
            evidence.write_text(json.dumps(row, indent=2) + "\n")

def build_layer(archive: Path, image: Path) -> None:
    """Materialize one root upper without copying the shared base or older layers."""
    with tarfile.open(archive) as tar:
        payload = sum(member.size for member in tar.getmembers())
    size = max(256 << 20, int(payload * 1.5) + (128 << 20))
    size = (size + (2 << 20) - 1) // (2 << 20) * (2 << 20)
    image.parent.mkdir(parents=True, exist_ok=True)
    if image.exists():
        raise FileExistsError(image)
    with image.open("wb") as handle:
        handle.truncate(size)
    mountpoint = Path(tempfile.mkdtemp(prefix="mixfs-exp5-dax-"))
    mounted = False
    try:
        subprocess.run(["mkfs.ext4", "-F", "-q", str(image)], check=True)
        subprocess.run(["sudo", "-n", "mount", "-o", "loop", str(image), str(mountpoint)], check=True)
        mounted = True
        subprocess.run(["sudo", "-n", "mkdir", str(mountpoint / "delta")], check=True)
        subprocess.run(["sudo", "-n", "tar", "--xattrs", "--xattrs-include=*", "--acls",
                        "--numeric-owner", "-C", str(mountpoint / "delta"), "-xf", str(archive)], check=True)
    except BaseException:
        image.unlink(missing_ok=True)
        raise
    finally:
        if mounted:
            subprocess.run(["sudo", "-n", "umount", str(mountpoint)], check=True)
        mountpoint.rmdir()
    image.chmod(0o444)
    with image.open("rb") as handle:
        os.fsync(handle.fileno())
        os.posix_fadvise(handle.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
    directory_fd = os.open(image.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def link_layers(instance: Path, template_image: Path) -> list[str]:
    """Preserve inode identity for every inherited and newly attached DAX layer."""
    names = []
    for source in sorted(instance.glob("checkpoint-*.ext4")):
        target = template_image / source.name
        os.link(source, target)
        names.append(source.name)
    return names
