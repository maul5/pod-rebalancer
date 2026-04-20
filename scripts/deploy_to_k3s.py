from __future__ import annotations

import os
import posixpath
import tarfile
import tempfile
from pathlib import Path

import paramiko


ROOT = Path(__file__).resolve().parents[1]
K8S_DIR = ROOT / "k8s"

REMOTE_HOST = os.environ["REMOTE_HOST"]
REMOTE_USER = os.environ["REMOTE_USER"]
REMOTE_PASSWORD = os.environ["REMOTE_PASSWORD"]
REMOTE_DEPLOY_DIR = os.environ.get("REMOTE_DEPLOY_DIR", "/tmp/pod-rebalancer-deploy")


def make_archive(source: Path) -> Path:
    fd, archive_path = tempfile.mkstemp(prefix=f"{source.name}-", suffix=".tar.gz")
    os.close(fd)
    archive = Path(archive_path)

    with tarfile.open(archive, "w:gz") as tar:
        for path in source.rglob("*"):
            rel = path.relative_to(source)
            tar.add(path, arcname=str(rel))

    return archive


def run_command(ssh: paramiko.SSHClient, command: str) -> str:
    stdin, stdout, stderr = ssh.exec_command(command, get_pty=True)
    stdout_text = stdout.read().decode("utf-8", errors="replace")
    stderr_text = stderr.read().decode("utf-8", errors="replace")
    exit_code = stdout.channel.recv_exit_status()
    if exit_code != 0:
        raise RuntimeError(f"Command failed: {command}\nSTDOUT:\n{stdout_text}\nSTDERR:\n{stderr_text}")
    return stdout_text


def upload_k8s_directory(ssh: paramiko.SSHClient, source_dir: Path, remote_dir: str) -> str:
    archive = make_archive(source_dir)
    remote_archive = f"{remote_dir}/{source_dir.name}.tar.gz"
    remote_source_dir = f"{remote_dir}/{source_dir.name}"

    try:
        sftp = ssh.open_sftp()
        try:
            current = ""
            for part in remote_dir.strip("/").split("/"):
                current = f"{current}/{part}" if current else f"/{part}"
                try:
                    sftp.mkdir(current)
                except IOError:
                    pass
            sftp.put(str(archive), remote_archive)
        finally:
            sftp.close()

        run_command(ssh, f"rm -rf {remote_source_dir} && mkdir -p {remote_source_dir}")
        run_command(ssh, f"tar -xzf {remote_archive} -C {remote_source_dir}")
        return remote_source_dir
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
        remote_k8s_dir = upload_k8s_directory(ssh, K8S_DIR, REMOTE_DEPLOY_DIR)
        for name in ("rbac.yaml", "configmap.yaml", "cronjob.yaml"):
            run_command(ssh, f"kubectl apply -f {posixpath.join(remote_k8s_dir, name)}")
    finally:
        ssh.close()


if __name__ == "__main__":
    main()
