#!/usr/bin/env python3
"""
Standalone validation script for C1/C2/C3/C5/C6/C7a/C7b/C8.

It validates:
1) Core container health endpoints
2) C1+C3 direct chat completion roundtrip
3) Agent pairs with "Tell me a joke"
4) Multi-session isolation for C2
5) Optional parallel ask validation
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMPOSE_CMD = ["docker", "compose"]


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str


def _http_json(url: str, timeout: int = 8):
    req = urllib.request.Request(url, headers={"User-Agent": "c9-validator/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        status = resp.status
        body = resp.read().decode("utf-8", errors="replace")
    return status, json.loads(body)


def _run_cmd(cmd: list[str], timeout: int = 180) -> tuple[int, str, str]:
    p = subprocess.run(
        cmd,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def check_health() -> list[CheckResult]:
    checks = [
        ("C1 /health", "http://localhost:8000/health", lambda d: d.get("status") == "ok"),
        ("C3 /health", "http://localhost:8001/health", lambda d: d.get("status") == "ok"),
        ("C7a /healthz", "http://localhost:18789/healthz", lambda d: d.get("status") in {"ok", "standby"}),
    ]
    out: list[CheckResult] = []
    for name, url, pred in checks:
        try:
            code, data = _http_json(url, timeout=8)
            ok = code == 200 and pred(data)
            out.append(CheckResult(name=name, ok=ok, detail=f"HTTP {code} {data}"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            out.append(CheckResult(name=name, ok=False, detail=f"{type(exc).__name__}: {exc}"))
    return out


def check_c1_c3_pair() -> CheckResult:
    payload = json.dumps(
        {
            "model": "copilot",
            "messages": [{"role": "user", "content": "Tell me a joke"}],
            "stream": False,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        "http://localhost:8000/v1/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "X-Agent-ID": "validator-c1-c3",
            "User-Agent": "c9-validator/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            code = resp.status
            body = resp.read().decode("utf-8", errors="replace")
        data = json.loads(body)
        text = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        ok = code == 200 and isinstance(text, str) and bool(text.strip())
        detail = f"HTTP {code}; content_len={len(text)}"
        return CheckResult(name="C1+C3 pair roundtrip", ok=ok, detail=detail)
    except Exception as exc:  # noqa: BLE001
        return CheckResult(name="C1+C3 pair roundtrip", ok=False, detail=str(exc))


def _is_successful_ask_output(text: str) -> bool:
    t = text or ""
    if "HTTP status: 500" in t:
        return False
    if "returned no response" in t.lower():
        return False
    if "✗" in t:
        return False
    if "[tokens:" in t or "[in:" in t:
        return True
    return False


def run_agent_ask(service: str, question: str = "Tell me a joke", retries: int = 2) -> CheckResult:
    cmd = COMPOSE_CMD + ["run", "--rm", service, "ask", question]
    attempts: list[str] = []
    for idx in range(retries + 1):
        code, out, err = _run_cmd(cmd, timeout=240)
        merged = f"{out}\n{err}".strip()
        attempts.append(f"attempt={idx+1} exit={code} :: {merged[-300:]}")
        if code == 0 and "Asking:" in merged and _is_successful_ask_output(merged):
            return CheckResult(name=f"{service} ask", ok=True, detail=merged[-500:])
        if idx < retries:
            time.sleep(2)
    return CheckResult(name=f"{service} ask", ok=False, detail=" || ".join(attempts))


def check_multi_session_c2() -> CheckResult:
    # Two independent runs for same service should both succeed.
    r1 = run_agent_ask("agent-terminal", "Tell me a joke")
    time.sleep(1)
    r2 = run_agent_ask("agent-terminal", "Tell me a joke about developers")
    ok = r1.ok and r2.ok
    detail = f"run1_ok={r1.ok}; run2_ok={r2.ok}"
    return CheckResult(name="C2 multi-session (2 sequential asks)", ok=ok, detail=detail)


def check_parallel_agents() -> list[CheckResult]:
    services = [
        "agent-terminal",
        "claude-code-terminal",
        "kilocode-terminal",
        "openclaw-cli",
        "hermes-agent",
    ]
    results: list[CheckResult] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(services)) as ex:
        futs = {ex.submit(run_agent_ask, s): s for s in services}
        for fut in concurrent.futures.as_completed(futs):
            try:
                results.append(fut.result())
            except Exception as exc:  # noqa: BLE001
                s = futs[fut]
                results.append(CheckResult(name=f"{s} ask", ok=False, detail=str(exc)))
    return sorted(results, key=lambda r: r.name)


def print_results(results: list[CheckResult]) -> None:
    print("\nValidation Summary")
    print("-" * 80)
    print(f"{'Check':45} {'Result':8} Detail")
    print("-" * 80)
    for r in results:
        mark = "PASS" if r.ok else "FAIL"
        print(f"{r.name:45} {mark:8} {r.detail}")
    print("-" * 80)
    passed = sum(1 for r in results if r.ok)
    print(f"Passed {passed}/{len(results)} checks")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate all AI-agent container pairs.")
    parser.add_argument(
        "--parallel",
        action="store_true",
        help="Also run all 5 agent ask checks in parallel.",
    )
    args = parser.parse_args()

    results: list[CheckResult] = []
    results.extend(check_health())
    results.append(check_c1_c3_pair())

    for service in [
        "agent-terminal",
        "claude-code-terminal",
        "kilocode-terminal",
        "openclaw-cli",
        "hermes-agent",
    ]:
        results.append(run_agent_ask(service))

    results.append(check_multi_session_c2())
    if args.parallel:
        results.extend(check_parallel_agents())

    print_results(results)
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
