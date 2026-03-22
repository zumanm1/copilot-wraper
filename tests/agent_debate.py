#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║          Multi-Agent Debate Framework  — agent_debate.py         ║
║                                                                  ║
║  Orchestrates a timed debate across all healthy agent containers:║
║    C2 (Aider)  ·  C2 (OpenCode)  ·  C5 (Claude Code)            ║
║    C6 (KiloCode)  ·  C7b (OpenClaw)  ·  C8 (Hermes)             ║
║                                                                  ║
║  The moderator LLM picks the topic and assigns a unique          ║
║  intellectual stance to each agent. No content is hardcoded.     ║
║  Agents read each other's arguments each round and respond.      ║
║  A judge scores all participants at the end.                     ║
║                                                                  ║
║  Usage:                                                          ║
║    python3 tests/agent_debate.py                                 ║
║    python3 tests/agent_debate.py --duration 300    # 5 min       ║
║    python3 tests/agent_debate.py --duration 60     # quick test  ║
║    python3 tests/agent_debate.py --max-rebuttal-rounds 2  # CI   ║
║    python3 tests/agent_debate.py --topic "your topic here"       ║
║    python3 tests/agent_debate.py --api http://localhost:8000     ║
║    python3 tests/agent_debate.py --agents C2a C5 C8  # subset   ║
╚══════════════════════════════════════════════════════════════════╝
"""

import argparse
import json
import os
import random
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ── ANSI colours ──────────────────────────────────────────────────────────────
RESET  = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RED    = "\033[31m"
GREEN  = "\033[32m"

AGENT_COLOURS = {
    "C2-Aider":     "\033[1;34m",   # bold blue
    "C2-OpenCode":  "\033[1;36m",   # bold cyan
    "C5-Claude":    "\033[1;35m",   # bold magenta
    "C6-KiloCode":  "\033[1;33m",   # bold yellow
    "C7b-OpenClaw": "\033[1;32m",   # bold green
    "C8-Hermes":    "\033[1;91m",   # bold bright red
    "Moderator":    "\033[1;97m",   # bold bright white
    "Judge":        "\033[1;96m",   # bold bright cyan
}

def col(name: str, text: str) -> str:
    c = AGENT_COLOURS.get(name, BOLD)
    return f"{c}{text}{RESET}"

def banner(title: str) -> None:
    w = 66
    print(f"\n{BOLD}╔{'═' * (w-2)}╗{RESET}")
    pad = (w - 2 - len(title)) // 2
    print(f"{BOLD}║{' ' * pad}{title}{' ' * (w - 2 - pad - len(title))}║{RESET}")
    print(f"{BOLD}╚{'═' * (w-2)}╝{RESET}\n")

def section(title: str, colour: str = BOLD) -> None:
    print(f"\n{colour}{'─' * 64}{RESET}")
    print(f"{colour}  {title}{RESET}")
    print(f"{colour}{'─' * 64}{RESET}\n")

def elapsed_str(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"

# ── Agent registry ─────────────────────────────────────────────────────────────
# All agents call C1 via http://localhost:8000/v1/chat/completions.
# Each gets a unique X-Agent-ID so C1 routes it to a dedicated Copilot session.
# Agents "hear" each other by receiving the accumulated transcript in each prompt.

ALL_AGENTS = [
    {
        "key":         "C2a",
        "id":          "debate-c2-aider",
        "name":        "C2-Aider",
        "service":     "agent-terminal",
        "description": "Aider AI coding agent (C2) — specialises in software engineering",
    },
    {
        "key":         "C2b",
        "id":          "debate-c2-opencode",
        "name":        "C2-OpenCode",
        "service":     "agent-terminal",
        "description": "OpenCode AI agent (C2) — broad system reasoning",
    },
    {
        "key":         "C5",
        "id":          "debate-c5-claude",
        "name":        "C5-Claude",
        "service":     "claude-code-terminal",
        "description": "Claude Code CLI (C5) — analytical and precise",
    },
    {
        "key":         "C6",
        "id":          "debate-c6-kilo",
        "name":        "C6-KiloCode",
        "service":     "kilocode-terminal",
        "description": "KiloCode CLI (C6) — practical, implementation-focused",
    },
    {
        "key":         "C7b",
        "id":          "debate-c7b-openclaw",
        "name":        "C7b-OpenClaw",
        "service":     "openclaw-cli",
        "description": "OpenClaw CLI (C7b) — systems and architecture perspective",
    },
    {
        "key":         "C8",
        "id":          "debate-c8-hermes",
        "name":        "C8-Hermes",
        "service":     "hermes-agent",
        "description": "Hermes Agent (C8) — long-horizon reasoning and memory",
    },
]

# Seed domains — the moderator LLM picks the specific topic, not us
SEED_DOMAINS = [
    "mathematics and formal proof systems",
    "quantum physics and the measurement problem",
    "deep learning and emergent intelligence",
    "machine learning theory vs empirical practice",
    "philosophy of mind and artificial consciousness",
    "complexity theory and computational limits",
    "evolutionary biology and information",
    "thermodynamics, entropy, and time",
    "neuroscience and the nature of memory",
    "information theory and the limits of compression",
]


# ── C1 API client ─────────────────────────────────────────────────────────────

def call_c1(api_base: str, agent_id: str, messages: list[dict],
            system: str | None = None, timeout: int = 90) -> str:
    """
    POST /v1/chat/completions with X-Agent-ID for per-agent session isolation.
    Returns the assistant reply text, or raises RuntimeError.
    """
    if system:
        full = [{"role": "system", "content": system}] + messages
    else:
        full = messages

    payload = json.dumps({
        "model": "copilot",
        "messages": full,
        "stream": False,
    }).encode()

    req = urllib.request.Request(
        f"{api_base}/v1/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "X-Agent-ID": agent_id,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
            return data["choices"][0]["message"]["content"].strip()
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code} from C1: {body[:400]}")
    except Exception as e:
        raise RuntimeError(f"C1 call failed ({agent_id}): {e}")


def check_c1(api_base: str) -> bool:
    try:
        with urllib.request.urlopen(f"{api_base}/health", timeout=5) as r:
            return json.loads(r.read()).get("status") == "ok"
    except Exception:
        return False


def get_healthy_services(compose_dir: str) -> set:
    """Return service names of running/healthy containers via docker compose ps."""
    try:
        result = subprocess.run(
            ["docker", "compose", "ps", "--format", "json"],
            capture_output=True, text=True, cwd=compose_dir,
        )
        services = set()
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                status = obj.get("Status", "").lower()
                if "running" in status or "healthy" in status:
                    services.add(obj.get("Service", ""))
            except json.JSONDecodeError:
                pass
        return services
    except Exception:
        return set()


# ── Prompt builders ────────────────────────────────────────────────────────────

def _strip_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:])
        if text.endswith("```"):
            text = text[:-3].strip()
    return text


def setup_debate(api_base: str, agents: list[dict],
                 domain: str, forced_topic: str | None = None) -> dict:
    """
    Ask the moderator LLM to choose a debate topic and assign unique stances.
    Returns {topic, description, stances: {agent_id: str}}.
    """
    agent_list = "\n".join(
        f"  - {a['name']}: {a['description']}" for a in agents
    )
    names_json = ", ".join(f'"{a["name"]}": "stance"' for a in agents)

    if forced_topic:
        topic_clause = f'The debate topic MUST be: "{forced_topic}"'
    else:
        topic_clause = (
            f"Choose a specific, nuanced, intellectually rich debate topic "
            f"within the domain of: {domain}. "
            "It must have multiple defensible positions with no obvious winner."
        )

    prompt = f"""You are the moderator of a high-stakes multi-agent academic debate.

{topic_clause}

Participating agents:
{agent_list}

Instructions:
1. State a sharp, precise debate topic (one sentence — a real question, not a statement).
2. Write a 2-sentence framing of the debate question.
3. Assign each agent a UNIQUE intellectual stance. Each stance must:
   - Be clearly distinct from all other stances
   - Be genuinely defensible with real arguments
   - Create natural tension with at least two other stances
   - Be 2-3 sentences giving the agent context for their position

Respond ONLY with valid JSON — no markdown, no preamble:
{{
  "topic": "...",
  "description": "...",
  "stances": {{
    {names_json}
  }}
}}"""

    system = (
        "You are a rigorous academic debate moderator. "
        "Assign stances that create real intellectual conflict. "
        "Respond with valid JSON only — no markdown fences, no commentary."
    )

    raw = call_c1(api_base, "debate-moderator",
                  [{"role": "user", "content": prompt}], system, timeout=60)
    raw = _strip_fence(raw)

    try:
        obj = json.loads(raw)
        stances = {}
        for a in agents:
            stances[a["id"]] = obj["stances"].get(
                a["name"],
                f"Present a rigorous, evidence-based perspective on the topic from {a['name']}'s viewpoint."
            )
        return {
            "topic": obj["topic"],
            "description": obj["description"],
            "stances": stances,
        }
    except (json.JSONDecodeError, KeyError):
        # Graceful fallback — debate still runs
        return {
            "topic": forced_topic or f"The fundamental nature of intelligence in {domain}",
            "description": (
                "Agents debate from distinct perspectives, "
                "each defending a different view of the core question."
            ),
            "stances": {
                a["id"]: f"Argue for a unique, well-reasoned position on the topic from {a['name']}'s perspective."
                for a in agents
            },
        }


def opening_prompt(setup: dict, agent: dict) -> str:
    return f"""You are participating in a structured multi-agent academic debate.

TOPIC: {setup["topic"]}
{setup["description"]}

YOUR ASSIGNED STANCE:
{setup["stances"][agent["id"]]}

Deliver your OPENING STATEMENT (4–6 sentences):
- State your core thesis clearly and boldly
- Present your single strongest opening argument with specific evidence or reasoning
- Signal how your position differs from what you expect others to argue
- Use precise, technical language — no hedging phrases like "I think" or "I believe"

You are {agent["name"]}. Speak as yourself."""


def rebuttal_prompt(setup: dict, agent: dict,
                    transcript: list[dict], round_num: int) -> str:
    others = [
        f"[{e['agent']} — Round {e['round']}]\n{e['text']}"
        for e in transcript
        if e["agent"] != agent["name"]
    ]
    transcript_text = "\n\n".join(others) if others else "(No prior statements yet.)"

    return f"""You are {agent["name"]} in Round {round_num} of the debate on:
TOPIC: {setup["topic"]}
YOUR STANCE: {setup["stances"][agent["id"]]}

WHAT YOUR OPPONENTS HAVE ARGUED:
{transcript_text}

Your response (4–6 sentences):
1. Name and directly rebut the STRONGEST argument made by a specific opponent (cite their name)
2. Expose a flaw, gap, or unstated assumption in their reasoning
3. Advance your own position with a NEW argument not made in your previous rounds
4. Be intellectually aggressive — this is a debate, not a seminar

Do NOT repeat arguments you already made. Build on the debate."""


def closing_prompt(setup: dict, agent: dict, transcript: list[dict]) -> str:
    others_text = "\n\n".join(
        f"[{e['agent']} — Round {e['round']}]\n{e['text']}"
        for e in transcript
        if e["agent"] != agent["name"]
    )
    return f"""You are {agent["name"]} delivering your CLOSING STATEMENT.

TOPIC: {setup["topic"]}
YOUR STANCE: {setup["stances"][agent["id"]]}

WHAT YOUR OPPONENTS ARGUED:
{others_text}

Closing statement (3–4 sentences):
- Restate your thesis with the confidence earned through the debate
- Acknowledge the most compelling counterargument and explain precisely why it does not defeat your position
- End with a single, memorable, quotable conclusion

This is your final word. Make it resonate."""


def judge_prompt(setup: dict, agents: list[dict], transcript: list[dict]) -> str:
    # Keep only the last statement per agent (opening or closing) to stay within C1 context limits
    # Build a compact summary: one best entry per agent (prefer closing > rebuttal > opening)
    per_agent: dict[str, dict] = {}
    phase_rank = {"closing": 3, "opening": 1}
    for e in transcript:
        name = e["agent"]
        rank = phase_rank.get(e["phase"].split("-")[0], 2)
        if name not in per_agent or rank > per_agent[name]["_rank"]:
            per_agent[name] = {**e, "_rank": rank}

    summary_lines = []
    for a in agents:
        e = per_agent.get(a["name"])
        if e:
            # Truncate each entry to 300 chars to avoid context overflow
            text = e["text"][:300] + ("..." if len(e["text"]) > 300 else "")
            summary_lines.append(f"[{e['agent']} — {e['phase']}]:\n{text}")

    transcript_text = "\n\n".join(summary_lines)
    names_schema = ", ".join(
        f'"{a["name"]}": {{"accuracy": 0, "depth": 0, "engagement": 0, "persuasiveness": 0, "comment": ""}}'
        for a in agents
    )
    return f"""You are the impartial judge of an academic debate on:
TOPIC: {setup["topic"]}

PARTICIPANT STATEMENTS (best statement per agent):
{transcript_text}

Score each participant (1–10) on four criteria:
- accuracy: factual correctness and precision
- depth: intellectual depth and nuance
- engagement: how effectively they engaged with opponents' arguments
- persuasiveness: overall persuasive force of their position

Also provide a 1-sentence comment on each participant's performance.
Identify the winner (or "tie" if genuinely equal).

Respond ONLY with valid JSON (no markdown, no preamble):
{{
  "winner": "...",
  "winner_reason": "one sentence",
  "scores": {{
    {names_schema}
  }}
}}"""


# ── Debate runner ─────────────────────────────────────────────────────────────

# Minimum seconds to wait between consecutive API calls to avoid rate-limiting
INTER_AGENT_DELAY = 3.0
# Minimum real-time per full debate round (all agents) to prevent runaway loops
MIN_ROUND_SECONDS = len(ALL_AGENTS) * INTER_AGENT_DELAY + 5.0


def run_agent_turn(api_base: str, agent: dict, prompt: str,
                   timeout: int = 90) -> tuple[str, bool]:
    """
    Call C1 on behalf of an agent.
    Returns (reply_text, success_bool).
    Automatically backs off when the circuit breaker is OPEN.
    """
    try:
        reply = call_c1(
            api_base,
            agent["id"],
            [{"role": "user", "content": prompt}],
            system=None,
            timeout=timeout,
        )
        return reply, True
    except RuntimeError as e:
        err = str(e)
        # Circuit breaker is open — back off and let it recover
        if "OPEN" in err or "circuit" in err.lower():
            print(f"\n  {DIM}[circuit breaker OPEN — waiting 35s for recovery]{RESET}")
            time.sleep(35)
        return f"[{agent['name']} error: {err[:120]}]", False


def print_turn(agent_name: str, phase: str, round_num: int, text: str) -> None:
    label = f"{agent_name}  |  {phase}  |  Round {round_num}"
    print(col(agent_name, f"┌─ {label}"))
    # Wrap text at ~70 chars
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        while len(line) > 72:
            split = line.rfind(" ", 0, 72)
            if split == -1:
                split = 72
            print(col(agent_name, f"│  {line[:split]}"))
            line = line[split:].strip()
        print(col(agent_name, f"│  {line}"))
    print(col(agent_name, "└" + "─" * 60))
    print()


def run_debate(
    api_base: str,
    compose_dir: str,
    duration: int,
    forced_topic: str | None,
    agent_keys: list[str] | None,
    max_rebuttal_rounds: int | None = None,
) -> dict:
    """
    Main debate orchestrator. Returns the full debate record as a dict.

    If ``max_rebuttal_rounds`` is set, Phase 2 runs exactly that many full
    rebuttal passes (each agent speaks once per pass), then closings and
    judging run regardless of remaining ``duration`` (subject to enough time
    for closings + judge).
    """
    start_time = time.time()
    transcript: list[dict] = []

    banner("Multi-Agent Debate Framework")
    print(f"  Duration     : {duration // 60} min {duration % 60} s")
    if max_rebuttal_rounds is not None:
        print(f"  Rebuttals    : fixed — {max_rebuttal_rounds} round(s) (then closings + judge)")
    else:
        print(f"  Rebuttals    : time-bounded (until duration − reserve)")
    print(f"  API          : {api_base}")
    print(f"  Start        : {datetime.now().strftime('%H:%M:%S')}")
    print()

    # ── Verify C1 ────────────────────────────────────────────────────────────
    print(f"  Checking C1 at {api_base} ...", end=" ", flush=True)
    if not check_c1(api_base):
        print(f"{RED}OFFLINE{RESET}")
        sys.exit(1)
    print(f"{GREEN}healthy{RESET}")

    # ── Discover healthy containers ────────────────────────────────────────
    print("  Discovering healthy containers ...", end=" ", flush=True)
    healthy = get_healthy_services(compose_dir)
    print(f"{GREEN}found {len(healthy)}{RESET}")

    # Filter agent list
    agents = []
    for a in ALL_AGENTS:
        if agent_keys and a["key"] not in agent_keys:
            continue
        if a["service"] in healthy:
            agents.append(a)
        else:
            print(f"  {DIM}  skipping {a['name']} — service '{a['service']}' not healthy{RESET}")

    if len(agents) < 2:
        print(f"\n{RED}ERROR: Need at least 2 healthy agent containers.{RESET}")
        print("Start them with: docker compose up -d")
        sys.exit(1)

    print(f"\n  Participants ({len(agents)}):")
    for a in agents:
        print(col(a["name"], f"    {a['name']:18s} [{a['service']}]"))
    print()

    # ── Phase 0: Moderator selects topic & assigns stances ─────────────────
    domain = random.choice(SEED_DOMAINS)
    section(f"PHASE 0 — Moderator choosing topic (domain: {domain})", AGENT_COLOURS["Moderator"])
    print("  Asking moderator to assign topic and stances ...", end=" ", flush=True)

    try:
        setup = setup_debate(api_base, agents, domain, forced_topic)
    except RuntimeError as e:
        print(f"{RED}FAILED{RESET}\n  {e}")
        sys.exit(1)

    print(f"{GREEN}done{RESET}")
    print()
    print(col("Moderator", f"TOPIC: {setup['topic']}"))
    print(col("Moderator", f"       {setup['description']}"))
    print()
    print(col("Moderator", "ASSIGNED STANCES:"))
    for a in agents:
        print(col(a["name"], f"  {a['name']:18s}: {setup['stances'][a['id']][:80]}..."))
    print()

    debate_record = {
        "meta": {
            "started_at":    datetime.now(timezone.utc).isoformat(),
            "duration_s":    duration,
            "max_rebuttal_rounds": max_rebuttal_rounds,
            "api_base":      api_base,
            "domain_seed":   domain,
            "forced_topic":  forced_topic,
            "participants":  [a["name"] for a in agents],
        },
        "setup":      setup,
        "transcript": transcript,
        "scores":     {},
    }

    # ── Phase 1: Opening statements ────────────────────────────────────────
    section("PHASE 1 — Opening Statements", AGENT_COLOURS["C8-Hermes"])
    for a in agents:
        elapsed = time.time() - start_time
        remaining = duration - elapsed
        if remaining < 30:
            print(f"  {DIM}[time limit reached — skipping {a['name']} opening]{RESET}")
            continue

        print(f"  {a['name']} opening ...", end=" ", flush=True)
        t0 = time.time()
        prompt = opening_prompt(setup, a)
        reply, ok = run_agent_turn(api_base, a, prompt, timeout=min(60, int(remaining) - 5))
        print(f"{'done' if ok else 'error'} ({time.time()-t0:.1f}s){RESET}")

        entry = {
            "agent":  a["name"],
            "phase":  "opening",
            "round":  1,
            "text":   reply,
            "ts":     elapsed_str(time.time() - start_time),
        }
        transcript.append(entry)
        print_turn(a["name"], "Opening", 1, reply)
        time.sleep(INTER_AGENT_DELAY)

    # ── Phase 2: Rebuttal rounds ───────────────────────────────────────────
    round_num = 1
    rebuttal_rounds_done = 0
    # Reserve time for closings (agents × 20s each) + judging (60s)
    closing_reserve = len(agents) * 25 + 60
    while True:
        elapsed = time.time() - start_time
        remaining = duration - elapsed

        # Fixed-round mode: completed enough full rebuttal passes
        if max_rebuttal_rounds is not None and rebuttal_rounds_done >= max_rebuttal_rounds:
            break

        # Always stop if we cannot finish closings + judge
        if remaining < closing_reserve:
            break

        round_num += 1
        round_start = time.time()
        section(f"PHASE 2 — Rebuttal Round {round_num}  [{elapsed_str(elapsed)} elapsed, "
                f"{elapsed_str(remaining - closing_reserve)} left]",
                AGENT_COLOURS["C2-Aider"])

        round_successes = 0
        for a in agents:
            elapsed = time.time() - start_time
            remaining = duration - elapsed
            if remaining < closing_reserve:
                break

            print(f"  {a['name']} rebuttal R{round_num} ...", end=" ", flush=True)
            t0 = time.time()
            prompt = rebuttal_prompt(setup, a, transcript, round_num)
            reply, ok = run_agent_turn(api_base, a, prompt,
                                       timeout=min(60, int(remaining) - 15))
            print(f"{'done' if ok else 'error'} ({time.time()-t0:.1f}s){RESET}")
            if ok:
                round_successes += 1

            entry = {
                "agent":  a["name"],
                "phase":  f"rebuttal-r{round_num}",
                "round":  round_num,
                "text":   reply,
                "ts":     elapsed_str(time.time() - start_time),
            }
            transcript.append(entry)
            print_turn(a["name"], f"Rebuttal R{round_num}", round_num, reply)

            # Pace between agents to avoid rate-limiting C1
            time.sleep(INTER_AGENT_DELAY)

        # If all agents failed, back off before the next round
        if round_successes == 0:
            print(f"  {DIM}[all agents failed this round — backing off 30s]{RESET}")
            time.sleep(30)

        # Pace between rounds — ensure each round takes a minimum real time
        round_duration = time.time() - round_start
        if round_duration < MIN_ROUND_SECONDS:
            time.sleep(MIN_ROUND_SECONDS - round_duration)

        rebuttal_rounds_done += 1

    # ── Phase 3: Closing statements ────────────────────────────────────────
    section("PHASE 3 — Closing Statements", AGENT_COLOURS["C6-KiloCode"])
    closing_round = round_num + 1
    for a in agents:
        elapsed = time.time() - start_time
        remaining = duration - elapsed
        if remaining < 20:
            print(f"  {DIM}[time limit — skipping {a['name']} closing]{RESET}")
            continue

        print(f"  {a['name']} closing ...", end=" ", flush=True)
        t0 = time.time()
        prompt = closing_prompt(setup, a, transcript)
        reply, ok = run_agent_turn(api_base, a, prompt, timeout=min(60, int(remaining) - 5))
        print(f"{'done' if ok else 'error'} ({time.time()-t0:.1f}s){RESET}")

        entry = {
            "agent":  a["name"],
            "phase":  "closing",
            "round":  closing_round,
            "text":   reply,
            "ts":     elapsed_str(time.time() - start_time),
        }
        transcript.append(entry)
        print_turn(a["name"], "Closing", closing_round, reply)
        time.sleep(INTER_AGENT_DELAY)

    # ── Phase 4: Judging ──────────────────────────────────────────────────
    section("PHASE 4 — Judging", AGENT_COLOURS["Judge"])
    print("  Judge scoring all participants ...", end=" ", flush=True)
    t0 = time.time()

    try:
        j_prompt = judge_prompt(setup, agents, transcript)
        raw = call_c1(
            api_base, "debate-judge",
            [{"role": "user", "content": j_prompt}],
            system=(
                "You are an impartial academic debate judge. "
                "Score fairly based solely on argument quality. "
                "Respond with valid JSON only — no markdown."
            ),
            timeout=90,
        )
        raw = _strip_fence(raw)
        scores = json.loads(raw)
    except Exception as e:
        scores = {"winner": "unknown", "winner_reason": str(e)[:200], "scores": {}}

    print(f"{GREEN}done ({time.time()-t0:.1f}s){RESET}\n")

    debate_record["scores"] = scores

    # ── Print leaderboard ──────────────────────────────────────────────────
    total_elapsed = time.time() - start_time
    banner(f"DEBATE RESULTS  —  {elapsed_str(total_elapsed)} elapsed")

    print(col("Moderator", f"  TOPIC   : {setup['topic']}"))
    print(col("Judge",     f"  WINNER  : {scores.get('winner','?')}"))
    print(col("Judge",     f"  REASON  : {scores.get('winner_reason','—')}"))
    print()

    # Build sorted leaderboard
    score_rows = []
    for a in agents:
        s = scores.get("scores", {}).get(a["name"], {})
        total = sum([
            s.get("accuracy", 0),
            s.get("depth", 0),
            s.get("engagement", 0),
            s.get("persuasiveness", 0),
        ])
        score_rows.append((total, a["name"], s))

    score_rows.sort(key=lambda x: -x[0])

    hdr = f"  {'Rank':<5} {'Agent':<18} {'Acc':>4} {'Dep':>4} {'Eng':>4} {'Per':>4} {'Total':>6}"
    print(col("Judge", hdr))
    print(col("Judge", "  " + "─" * 54))

    for rank, (total, name, s) in enumerate(score_rows, 1):
        row = (
            f"  {rank:<5} {name:<18} "
            f"{s.get('accuracy','-'):>4} "
            f"{s.get('depth','-'):>4} "
            f"{s.get('engagement','-'):>4} "
            f"{s.get('persuasiveness','-'):>4} "
            f"{total:>6}"
        )
        print(col(name, row))

    print()
    print(col("Judge", "  Comments:"))
    for _, name, s in score_rows:
        comment = s.get("comment", "—")
        print(col(name, f"    {name}: {comment}"))

    print()
    print(f"  {DIM}Rounds completed : {round_num}{RESET}")
    print(f"  {DIM}Transcript entries: {len(transcript)}{RESET}")

    return debate_record


# ── Save transcript ───────────────────────────────────────────────────────────

def save_transcript(record: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"debate_{ts}.json"
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False))
    return path


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Multi-Agent Debate Framework — all agents debate via C1",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 tests/agent_debate.py                         # 10-min debate, random topic
  python3 tests/agent_debate.py --duration 300          # 5-min debate
  python3 tests/agent_debate.py --duration 60           # quick 1-min smoke test
  python3 tests/agent_debate.py --max-rebuttal-rounds 2 --duration 900
          # exactly 2 rebuttal rounds + closings + judge (CI / integration)
  python3 tests/agent_debate.py --topic "P vs NP"       # forced topic (LLM assigns stances)
  python3 tests/agent_debate.py --agents C2a C5 C8      # only 3 agents
        """,
    )
    parser.add_argument(
        "--api",
        default="http://localhost:8000",
        help="C1 API base URL (default: http://localhost:8000)",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=600,
        help="Total debate duration in seconds (default: 600 = 10 min)",
    )
    parser.add_argument(
        "--topic",
        default=None,
        help="Force a specific debate topic. The LLM still assigns stances.",
    )
    parser.add_argument(
        "--agents",
        nargs="+",
        choices=["C2a", "C2b", "C5", "C6", "C7b", "C8"],
        default=None,
        help="Subset of agents to include (default: all healthy). "
             "Keys: C2a=Aider C2b=OpenCode C5=Claude C6=KiloCode C7b=OpenClaw C8=Hermes",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Directory to save transcript JSON (default: tests/debate-transcripts/)",
    )
    parser.add_argument(
        "--max-rebuttal-rounds",
        type=int,
        default=None,
        metavar="N",
        help=(
            "Run exactly N full rebuttal passes (each agent speaks once per pass), "
            "then closings and judging. Overrides time-only stopping for Phase 2. "
            "Use with a generous --duration so closings + judge still fit."
        ),
    )
    args = parser.parse_args()

    compose_dir = str(Path(__file__).resolve().parent.parent)
    out_dir = Path(args.output) if args.output else Path(compose_dir) / "tests" / "debate-transcripts"

    record = run_debate(
        api_base=args.api,
        compose_dir=compose_dir,
        duration=args.duration,
        forced_topic=args.topic,
        agent_keys=args.agents,
        max_rebuttal_rounds=args.max_rebuttal_rounds,
    )

    path = save_transcript(record, out_dir)
    print(f"\n  {DIM}Transcript saved → {path}{RESET}\n")


if __name__ == "__main__":
    main()
