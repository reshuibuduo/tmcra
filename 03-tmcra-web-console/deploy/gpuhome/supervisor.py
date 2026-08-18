from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path


DEPLOY_DIRECTORY = Path(__file__).resolve().parent
ENVIRONMENT_FILE = Path(
    os.environ.get("TMCRA_DEPLOY_ENV", DEPLOY_DIRECTORY / "deployment.env")
)


def load_environment(path: Path) -> None:
    if not path.is_file():
        raise RuntimeError(f"deployment environment file is missing: {path}")
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, value = line.partition("=")
        if not separator or not name.strip():
            raise RuntimeError(f"invalid deployment environment line: {raw_line!r}")
        os.environ[name.strip()] = value.strip()


def main() -> None:
    load_environment(ENVIRONMENT_FILE)
    project = Path(os.environ["TMCRA_PROJECT_ROOT"]).resolve()
    node = Path(os.environ["TMCRA_NODE_BIN"]).resolve()
    log_directory = Path(os.environ["TMCRA_LOG_DIR"]).resolve()
    log_directory.mkdir(parents=True, exist_ok=True)
    if not node.is_file():
        raise RuntimeError(f"Node runtime is missing: {node}")
    vite = project / "node_modules" / "vite" / "bin" / "vite.js"
    proxy = project / "deploy" / "gpuhome" / "proxy.py"
    maintenance = project / "deploy" / "gpuhome" / "maintenance.py"
    if not vite.is_file() or not proxy.is_file() or not maintenance.is_file():
        raise RuntimeError("deployment artifacts are incomplete")
    if len(os.environ.get("TMCRA_DEVICE_MAINTENANCE_SECRET", "").strip()) < 43:
        raise RuntimeError("TMCRA_DEVICE_MAINTENANCE_SECRET is missing or too short")

    commands = {
        "preview": [
            str(node),
            str(vite),
            "preview",
            "--host",
            "127.0.0.1",
            "--port",
            os.environ.get("TMCRA_UPSTREAM_PORT", "2001"),
            "--strictPort",
        ],
        "gateway": [sys.executable, "-u", str(proxy)],
        "maintenance": [sys.executable, "-u", str(maintenance)],
    }
    children: dict[str, tuple[subprocess.Popen[bytes], object]] = {}
    stopping = False

    def stop(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    def start(name: str) -> None:
        log = (log_directory / f"{name}.log").open("ab", buffering=0)
        process = subprocess.Popen(
            commands[name],
            cwd=project,
            env=os.environ.copy(),
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        children[name] = (process, log)
        print(f"started {name} pid={process.pid}", flush=True)

    for service_name in commands:
        start(service_name)

    try:
        while not stopping:
            for name, (process, log) in list(children.items()):
                code = process.poll()
                if code is None:
                    continue
                print(f"{name} exited code={code}; restarting", flush=True)
                log.close()
                time.sleep(2)
                start(name)
            time.sleep(1)
    finally:
        for process, _log in children.values():
            if process.poll() is None:
                process.terminate()
        deadline = time.time() + 8
        for process, _log in children.values():
            timeout = max(0.1, deadline - time.time())
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
        for _process, log in children.values():
            log.close()


if __name__ == "__main__":
    main()
