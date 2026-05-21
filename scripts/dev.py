#!/usr/bin/env python3
"""ResearchSquid dev starter - cross-platform (Windows/Mac/Linux)."""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen

from dotenv import load_dotenv

from port_utils import describe_port_listeners, kill_ports

DOCKER_DESKTOP_PROCESS_NAMES = {
    "com.docker.backend",
    "com.docker.backend.exe",
}
DEFAULT_APP_PORTS = [3000, 8000]
NEO4J_PROBE_TIMEOUT_SECONDS = 5


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start the local ResearchSquid dev stack.")
    parser.add_argument(
        "--kill-ports",
        nargs="*",
        type=int,
        default=None,
        help="Force-kill listeners on these ports before startup. Defaults to 3000 and 8000.",
    )
    parser.add_argument(
        "--no-kill-ports",
        action="store_true",
        help="Do not clear existing frontend/backend listeners before startup.",
    )
    return parser.parse_args()


def run_cmd(cmd, check=True, capture=False):
    result = subprocess.run(cmd, shell=True, capture_output=capture, text=True)
    if check and result.returncode != 0:
        print(f"Command failed: {cmd}")
        if result.stderr:
            print(result.stderr)
        return None
    return result


def docker_services_running():
    services = docker_running_services()
    return "neo4j" in services and "postgres" in services


def docker_running_services():
    root_dir = os.path.dirname(os.path.dirname(__file__))
    result = run_cmd(
        f'docker compose -f "{root_dir}/docker-compose.yml" -f "{root_dir}/docker-compose.dev.yml" ps --services --filter status=running',
        check=False,
        capture=True,
    )
    if result and result.stdout:
        return set(result.stdout.strip().split("\n"))
    return set()


def start_docker_services():
    print("Starting Docker services (neo4j, postgres)...")
    root_dir = os.path.dirname(os.path.dirname(__file__))
    result = run_cmd(
        f'docker compose -f "{root_dir}/docker-compose.yml" -f "{root_dir}/docker-compose.dev.yml" up -d --remove-orphans neo4j postgres',
        check=False,
        capture=True,
    )
    if result is None or result.returncode != 0:
        print("WARNING: Could not start Docker services. Ensure Docker is running.")
        if result is not None:
            if result.stdout:
                print(result.stdout.strip())
            if result.stderr:
                print(result.stderr.strip())
        return False
    print("Docker Compose accepted the startup request.")
    return True


def recreate_neo4j_container():
    print("Recreating Neo4j container while preserving the neo4j_data volume...")
    root_dir = os.path.dirname(os.path.dirname(__file__))
    result = run_cmd(
        f'docker compose -f "{root_dir}/docker-compose.yml" -f "{root_dir}/docker-compose.dev.yml" rm -sf neo4j',
        check=False,
        capture=True,
    )
    if result is None or result.returncode != 0:
        print("WARNING: Could not remove the existing Neo4j container.")
        if result is not None:
            if result.stdout:
                print(result.stdout.strip())
            if result.stderr:
                print(result.stderr.strip())
        return False
    return True


def wait_for_docker_services_running(timeout_seconds: int = 60):
    print("Waiting for Docker containers to stay running...")
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        running = docker_running_services()
        if "neo4j" in running and "postgres" in running:
            print("Docker containers are running.")
            return True
        time.sleep(2)
    print("WARNING: Docker containers did not all stay running.")
    return False


def ensure_sandbox_image():
    print("Ensuring sandbox image is available...")
    root_dir = os.path.dirname(os.path.dirname(__file__))
    inspect = run_cmd(
        "docker image inspect squid-sandbox:latest",
        check=False,
        capture=True,
    )
    if inspect is not None and inspect.returncode == 0:
        print("Sandbox image is ready.")
        return True

    result = run_cmd(
        f'docker build -t squid-sandbox:latest -f "{root_dir}/backend/Dockerfile.sandbox" "{root_dir}/backend"',
        check=False,
        capture=True,
    )
    if result is None or result.returncode != 0:
        print("WARNING: Could not build sandbox image. Experiments may fail.")
        if result is not None:
            if result.stdout:
                print(result.stdout.strip())
            if result.stderr:
                print(result.stderr.strip())
        return False

    print("Sandbox image built.")
    return True


def stop_docker_services():
    print("Stopping Docker services...")
    root_dir = os.path.dirname(os.path.dirname(__file__))
    run_cmd(
        f'docker compose -f "{root_dir}/docker-compose.yml" -f "{root_dir}/docker-compose.dev.yml" stop neo4j postgres',
        check=False,
    )


def maybe_kill_requested_ports(ports, *, disabled: bool = False):
    if disabled:
        print("Skipping app port cleanup.")
        return
    target_ports = ports if ports is not None else DEFAULT_APP_PORTS
    if not target_ports:
        target_ports = DEFAULT_APP_PORTS
    print(f"Force-clearing listening processes on ports: {', '.join(str(port) for port in target_ports)}")
    results = kill_ports(target_ports)
    for port in target_ports:
        killed = results.get(port, set())
        if killed:
            print(f"  Port {port}: terminated PIDs {', '.join(str(pid) for pid in sorted(killed))}")
        else:
            print(f"  Port {port}: no listeners terminated")


def kill_existing_dev_starters():
    if sys.platform == "win32":
        return

    try:
        result = subprocess.run(
            ["ps", "-eo", "pid=,command="],
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception:
        return
    if result.returncode != 0:
        return

    current_pid = os.getpid()
    killed = []
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        pid_text, _, command = stripped.partition(" ")
        try:
            pid = int(pid_text)
        except ValueError:
            continue
        if pid == current_pid:
            continue
        if "python" not in command or "scripts/dev.py" not in command:
            continue
        try:
            os.kill(pid, signal.SIGTERM)
            killed.append(pid)
        except OSError:
            pass

    if killed:
        print(f"Terminated existing dev starter PIDs: {', '.join(str(pid) for pid in killed)}")


def ensure_python_modules(venv_python):
    checks = [
        ("openai", "Real LLM calls"),
        ("neo4j", "Neo4j connectivity"),
        ("fastapi", "Backend server"),
    ]
    missing = []
    for module_name, label in checks:
        result = subprocess.run(
            [venv_python, "-c", f"import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('{module_name}') else 1)"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            missing.append((module_name, label))
    if not missing:
        return
    print("ERROR: The virtual environment is missing required Python packages:")
    for module_name, label in missing:
        print(f"  - {module_name} ({label})")
    print()
    print("Run this once to sync the environment:")
    print("  python scripts\\setup.py" if sys.platform == "win32" else "  python scripts/setup.py")
    print()
    sys.exit(1)


def neo4j_probe(venv_python, env):
    probe = [
        venv_python,
        "-c",
        (
            "import os; "
            "from neo4j import GraphDatabase; "
            "driver = GraphDatabase.driver("
            "os.getenv('NEO4J_URI', 'bolt://localhost:7687'), "
            "auth=(os.getenv('NEO4J_USER', 'neo4j'), os.getenv('NEO4J_PASSWORD', 'researchsquid')), "
            f"connection_timeout={NEO4J_PROBE_TIMEOUT_SECONDS}"
            "); "
            "driver.verify_connectivity(); "
            "driver.close()"
        ),
    ]
    try:
        result = subprocess.run(
            probe,
            env=env,
            capture_output=True,
            text=True,
            timeout=NEO4J_PROBE_TIMEOUT_SECONDS + 1,
        )
    except subprocess.TimeoutExpired:
        return False, f"probe timed out after {NEO4J_PROBE_TIMEOUT_SECONDS + 1}s"
    if result.returncode == 0:
        return True, ""
    detail = (result.stderr or result.stdout or "probe exited without details").strip()
    if len(detail) > 600:
        detail = detail[-600:]
    return False, detail


def neo4j_ready(venv_python, env):
    ready, _detail = neo4j_probe(venv_python, env)
    return ready


def _local_docker_env_mismatches(env):
    mismatches = []
    neo4j_uri = env.get("NEO4J_URI", "bolt://localhost:7687")
    neo4j_user = env.get("NEO4J_USER", "neo4j")
    neo4j_password = env.get("NEO4J_PASSWORD", "researchsquid")
    database_url = env.get("DATABASE_URL", "postgresql+asyncpg://squid:researchsquid@localhost:5432/squid")

    if neo4j_uri in {"bolt://localhost:7687", "bolt://127.0.0.1:7687"}:
        if neo4j_user != "neo4j" or neo4j_password != "researchsquid":
            mismatches.append(
                "NEO4J_* in .env does not match the local Docker Neo4j credentials "
                "(expected neo4j / researchsquid for bolt://localhost:7687)."
            )

    normalized_db = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    expected_db = "postgresql+asyncpg://squid:researchsquid@localhost:5432/squid"
    expected_db_loopback = "postgresql+asyncpg://squid:researchsquid@127.0.0.1:5432/squid"
    if "@localhost:5432/" in normalized_db or "@127.0.0.1:5432/" in normalized_db:
        if normalized_db not in {expected_db, expected_db_loopback}:
            mismatches.append(
                "DATABASE_URL in .env does not match the local Docker Postgres credentials "
                "(expected squid / researchsquid on localhost:5432, database squid)."
            )

    return mismatches


def _uses_local_neo4j(env):
    neo4j_uri = env.get("NEO4J_URI", "bolt://localhost:7687")
    return "localhost:" in neo4j_uri or "127.0.0.1:" in neo4j_uri


def wait_for_neo4j(venv_python, env, timeout_seconds: int = 90):
    print("Waiting for Neo4j Bolt readiness...")
    deadline = time.time() + timeout_seconds
    attempt = 1
    last_detail = ""
    while time.time() < deadline:
        ready, detail = neo4j_probe(venv_python, env)
        if ready:
            print("Neo4j Bolt is ready.")
            return True
        if detail:
            last_detail = detail
        running = docker_running_services()
        if "postgres" in running and "neo4j" not in running:
            print("WARNING: Neo4j container exited before Bolt became ready.")
            if last_detail:
                print("Last Neo4j probe error:")
                print(last_detail)
            return False
        remaining = max(0, int(deadline - time.time()))
        print(f"  Neo4j Bolt not ready yet (attempt {attempt}, {remaining}s left).")
        attempt += 1
        time.sleep(2)
    print("WARNING: Neo4j Bolt did not become ready in time.")
    if last_detail:
        print("Last Neo4j probe error:")
        print(last_detail)
    return False


def docker_logs_command() -> str:
    return "docker compose -f docker-compose.yml -f docker-compose.dev.yml logs --tail=120 neo4j"


def backend_ready(url: str) -> bool:
    try:
        with urlopen(url, timeout=2) as response:
            return response.status == 200
    except (OSError, URLError):
        return False


def wait_for_backend(url: str, timeout_seconds: int = 90) -> bool:
    print(f"Waiting for backend readiness at {url}...")
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if backend_ready(url):
            print("Backend is ready.")
            return True
        time.sleep(1)
    print("WARNING: Backend did not become ready before frontend startup.")
    return False


def ensure_neo4j_available(venv_python, env, *, attempted_docker_start: bool) -> None:
    if neo4j_ready(venv_python, env):
        print("Using existing Neo4j instance.")
        return
    print("ERROR: Neo4j is not reachable at the configured NEO4J_URI.")
    conflicts = _classify_institute_port_listeners(env)
    actionable_ports = _print_port_listener_diagnostics(conflicts)
    if actionable_ports:
        _print_kill_ports_help(actionable_ports)
    if _has_expected_docker_proxy(conflicts):
        print("Docker Desktop proxy listeners are expected for published Docker service ports.")
    if attempted_docker_start:
        print("Docker Compose startup was attempted, but Neo4j Bolt is still unavailable.")
        print("Inspect Neo4j logs with:")
        print(f"  {docker_logs_command()}")
    else:
        print("Start Docker services or point NEO4J_URI to a running local Neo4j instance, then retry.")
    print(f"Current NEO4J_URI: {env.get('NEO4J_URI', 'bolt://localhost:7687')}")
    sys.exit(1)


def _local_service_ports(env):
    ports = {3000, 8000}
    neo4j_uri = env.get("NEO4J_URI", "bolt://localhost:7687")
    database_url = env.get("DATABASE_URL", "postgresql://squid:researchsquid@localhost:5432/squid")
    if "localhost:" in neo4j_uri or "127.0.0.1:" in neo4j_uri:
        try:
            ports.add(int(neo4j_uri.rsplit(":", 1)[-1]))
        except ValueError:
            pass
    if "@localhost:" in database_url or "@127.0.0.1:" in database_url:
        try:
            ports.add(int(database_url.rsplit(":", 1)[-1].split("/", 1)[0]))
        except ValueError:
            pass
    return ports


def _classify_institute_port_listeners(env):
    listeners_by_port = describe_port_listeners(sorted(_local_service_ports(env)))
    running_services = docker_running_services()
    classified = {}
    for port, listeners in listeners_by_port.items():
        if not listeners:
            continue
        classified[port] = [
            {
                **listener,
                "expected_docker_proxy": _is_expected_docker_proxy(port, listener, running_services),
            }
            for listener in listeners
        ]
    return classified


def _is_expected_docker_proxy(port, listener, running_services):
    process_name = os.path.basename(str(listener.get("process_name", ""))).lower()
    service_by_port = {
        5432: "postgres",
        7687: "neo4j",
    }
    service = service_by_port.get(port)
    if service is None or service not in running_services:
        return False
    return process_name in DOCKER_DESKTOP_PROCESS_NAMES or process_name == "unknown"


def _has_expected_docker_proxy(conflicts):
    return any(
        listener["expected_docker_proxy"]
        for listeners in conflicts.values()
        for listener in listeners
    )


def _print_port_listener_diagnostics(conflicts):
    if not conflicts:
        return []

    actionable_ports = []
    print("Local port listeners detected:")
    for port, listeners in conflicts.items():
        actionable_for_port = False
        for listener in listeners:
            suffix = " (expected Docker Desktop proxy)" if listener["expected_docker_proxy"] else ""
            print(f"  Port {port}: PID {listener['pid']} ({listener['process_name']}){suffix}")
            actionable_for_port = actionable_for_port or not listener["expected_docker_proxy"]
        if actionable_for_port:
            actionable_ports.append(port)
    return actionable_ports


def _print_kill_ports_help(ports):
    port_args = " ".join(str(port) for port in ports)
    print("If those are stale listeners, clear them with:")
    print(f"  python scripts\\kill_ports.py {port_args}" if sys.platform == "win32" else f"  python scripts/kill_ports.py {port_args}")


def fail_docker_neo4j_startup(env, reason: str) -> None:
    print(f"ERROR: {reason}")
    ps = run_cmd(
        "docker compose -f docker-compose.yml -f docker-compose.dev.yml ps -a",
        check=False,
        capture=True,
    )
    if ps is not None and ps.stdout:
        print("Docker service status:")
        print(ps.stdout.strip())
    conflicts = _classify_institute_port_listeners(env)
    actionable_ports = _print_port_listener_diagnostics(conflicts)
    if actionable_ports:
        _print_kill_ports_help(actionable_ports)
    if _has_expected_docker_proxy(conflicts):
        print("Docker Desktop proxy listeners are expected for published Docker service ports.")
    print("Inspect Neo4j logs with:")
    print(f"  {docker_logs_command()}")
    print(f"Current NEO4J_URI: {env.get('NEO4J_URI', 'bolt://localhost:7687')}")
    sys.exit(1)


def main():
    args = parse_args()
    print("Starting ResearchSquid...")
    procs = []

    def cleanup(sig=None, frame=None):
        print("\nStopping...")
        for p in procs:
            try:
                p.terminate()
            except Exception:
                pass
        stop_docker_services()
        maybe_kill_requested_ports(DEFAULT_APP_PORTS)
        sys.exit(0)

    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    # Determine venv python path
    venv_dir = os.path.join(os.path.dirname(__file__), "..", ".venv")
    root_dir = os.path.dirname(venv_dir)
    load_dotenv(os.path.join(root_dir, ".env"))
    if sys.platform == "win32":
        venv_python = os.path.join(venv_dir, "Scripts", "python.exe")
        npm_cmd = "npm.cmd"
    else:
        venv_python = os.path.join(venv_dir, "bin", "python")
        npm_cmd = "npm"

    ensure_python_modules(venv_python)

    kill_existing_dev_starters()
    maybe_kill_requested_ports(args.kill_ports, disabled=args.no_kill_ports)

    env = os.environ.copy()

    # Start Docker services if not running
    docker_available = run_cmd("docker --version", check=False) is not None
    attempted_docker_start = False
    recreated_neo4j = False
    if docker_available and _uses_local_neo4j(env) and "neo4j" not in docker_running_services():
        recreated_neo4j = recreate_neo4j_container()
    services_running = docker_services_running() if docker_available else False
    if docker_available and not services_running:
        attempted_docker_start = True
        started = start_docker_services()
        if not started:
            ensure_neo4j_available(venv_python, env, attempted_docker_start=attempted_docker_start)
        services_running = wait_for_docker_services_running()
        if not services_running:
            fail_docker_neo4j_startup(env, "Docker Compose accepted startup, but Neo4j did not stay running.")
    if docker_available and services_running:
        mismatches = _local_docker_env_mismatches(env)
        if mismatches:
            print("ERROR: Local Docker services are running, but your root .env uses different local DB credentials.")
            for mismatch in mismatches:
                print(f"  - {mismatch}")
            print("Update the repo-root .env or use external service URLs instead of localhost defaults.")
            sys.exit(1)
        if not wait_for_neo4j(venv_python, env):
            if docker_available and _uses_local_neo4j(env) and not recreated_neo4j:
                print("Neo4j did not become ready; trying one safe container recreate.")
                recreated_neo4j = recreate_neo4j_container()
                if recreated_neo4j:
                    attempted_docker_start = True
                    if start_docker_services() and wait_for_docker_services_running() and wait_for_neo4j(venv_python, env):
                        ensure_sandbox_image()
                    else:
                        fail_docker_neo4j_startup(env, "Neo4j still did not become ready after a safe container recreate.")
                else:
                    fail_docker_neo4j_startup(env, "Neo4j did not become ready, and the safe container recreate failed.")
            else:
                running = docker_running_services()
                if "neo4j" not in running:
                    fail_docker_neo4j_startup(env, "Neo4j container exited before Bolt became reachable.")
                fail_docker_neo4j_startup(env, "Neo4j container is running, but Bolt never became reachable.")
            running = docker_running_services()
            if "neo4j" not in running:
                fail_docker_neo4j_startup(env, "Neo4j container exited before Bolt became reachable.")
        ensure_sandbox_image()
    else:
        ensure_neo4j_available(venv_python, env, attempted_docker_start=attempted_docker_start)

    # Start backend
    print("Starting backend on :8000...")
    backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend"))
    backend = subprocess.Popen(
        [
            venv_python,
            "run_server.py",
        ],
        env=env,
        cwd=backend_dir,
    )
    procs.append(backend)

    wait_for_backend("http://127.0.0.1:8000/health")

    # Start frontend
    print("Starting frontend on :3000...")
    frontend_dir = os.path.join(os.path.dirname(__file__), "..", "frontend")
    frontend = subprocess.Popen(
        [npm_cmd, "run", "dev"],
        cwd=os.path.abspath(frontend_dir),
    )
    procs.append(frontend)

    print()
    print("ResearchSquid running:")
    print("   Frontend: http://localhost:3000")
    print("   Backend:  http://localhost:8000")
    print("   Health:   http://localhost:8000/health")
    print()
    print("Press Ctrl+C to stop")

    try:
        for p in procs:
            p.wait()
    except KeyboardInterrupt:
        cleanup()


if __name__ == "__main__":
    main()
