from __future__ import annotations

import os
import tarfile
import tempfile
from pathlib import Path

import paramiko


ROOT = Path(__file__).resolve().parents[1]
REMOTE_HOST = os.environ["REMOTE_HOST"]
REMOTE_USER = os.environ["REMOTE_USER"]
REMOTE_PASSWORD = os.environ["REMOTE_PASSWORD"]
HARBOR_USER = os.environ["HARBOR_USER"]
HARBOR_PASSWORD = os.environ["HARBOR_PASSWORD"]

REMOTE_BUILD_DIR = os.environ.get("REMOTE_BUILD_DIR", "/tmp/pod-rebalancer-build")
IMAGE_NAME = os.environ.get("IMAGE_NAME", "harbor.local/library/pod-rebalancer:latest")
DOCKERFILE_PATH = os.environ.get("DOCKERFILE_PATH", "Dockerfile")


def make_archive(source: Path) -> Path:
    fd, archive_path = tempfile.mkstemp(prefix=f"{source.name}-", suffix=".tar.gz")
    os.close(fd)
    archive = Path(archive_path)
    excluded = {".git", ".venv", "__pycache__", ".pytest_cache", "node_modules", "dist", "target"}

    with tarfile.open(archive, "w:gz") as tar:
        for path in source.rglob("*"):
            rel = path.relative_to(source)
            if any(part in excluded for part in rel.parts):
                continue
            tar.add(path, arcname=str(rel))

    return archive


def run_command(ssh: paramiko.SSHClient, command: str) -> None:
    stdin, stdout, stderr = ssh.exec_command(command, get_pty=True)
    stdout_text = stdout.read().decode("utf-8", errors="replace")
    stderr_text = stderr.read().decode("utf-8", errors="replace")
    exit_code = stdout.channel.recv_exit_status()
    if exit_code != 0:
        raise RuntimeError(f"Command failed: {command}\nSTDOUT:\n{stdout_text}\nSTDERR:\n{stderr_text}")


def push_context(ssh: paramiko.SSHClient, source_dir: Path, image: str, remote_dir: str, dockerfile: str) -> None:
    archive = make_archive(source_dir)
    remote_archive = f"{remote_dir}/{source_dir.name}.tar.gz"
    remote_source_dir = f"{remote_dir}/{source_dir.name}"

    try:
        sftp = ssh.open_sftp()
        try:
            try:
                sftp.mkdir(remote_dir)
            except IOError:
                pass
            sftp.put(str(archive), remote_archive)
        finally:
            sftp.close()

        run_command(ssh, f"rm -rf {remote_source_dir} && mkdir -p {remote_source_dir}")
        run_command(ssh, f"tar -xzf {remote_archive} -C {remote_source_dir}")
        run_command(ssh, f"docker build -f {remote_source_dir}/{dockerfile} -t {image} {remote_source_dir}")
        run_command(ssh, f"docker push {image}")
    finally:
        archive.unlink(missing_ok=True)


def main() -> None:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(
        hostname=REMOTE_HOST,
        username=REMOTE_USER,
        password=REMOTE_PASSWORD,
        look_for_keys=False,
        allow_agent=False,
        timeout=30,
    )

    try:
        run_command(ssh, f"docker login harbor.local -u '{HARBOR_USER}' -p '{HARBOR_PASSWORD}'")
        push_context(ssh, ROOT, IMAGE_NAME, REMOTE_BUILD_DIR, DOCKERFILE_PATH)
    finally:
        ssh.close()


if __name__ == "__main__":
    main()
