"""Simple eval harness for Curunir. Sends prompts from simple_evals.md one at a
time, shows real-time output, and saves results to a timestamped file.

Usage:
    python eval/run_evals.py [--host localhost] [--port 8765]
"""

import argparse
import asyncio
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import websockets

DEFAULT_EVALS_FILE = Path(__file__).parent / "simple_evals.md"
RESULTS_DIR = Path(__file__).parent / "eval_results"


def parse_prompts(path: Path) -> list[dict]:
    """Extract prompts and their categories from the evals markdown file."""
    text = path.read_text()
    prompts = []
    current_category = ""
    for line in text.splitlines():
        if line.startswith("## "):
            current_category = line.removeprefix("## ").strip()
        elif line.startswith("```") and current_category:
            # skip, handled below
            pass

    # Regex: find each ## heading, then all ``` blocks under it
    sections = re.split(r"^## ", text, flags=re.MULTILINE)[1:]
    for section in sections:
        lines = section.strip().splitlines()
        category = lines[0].strip()
        body = "\n".join(lines[1:])
        for match in re.finditer(
            r"```(?:max_loops=(\d+))?\n(.*?)\n```", body, re.DOTALL
        ):
            max_loops = int(match.group(1)) if match.group(1) else None
            prompts.append({
                "category": category,
                "prompt": match.group(2).strip(),
                "max_loops": max_loops,
            })
    return prompts


def get_version() -> str:
    try:
        return subprocess.check_output(
            ["git", "describe", "--tags", "--always"], text=True
        ).strip()
    except Exception:
        return "unknown"


async def send_prompt(ws, prompt: str, max_loops: int | None = None) -> dict:
    """Send a prompt, stream output to terminal, return collected result."""
    # Clear session first
    await ws.send(json.dumps({"content": "", "command": "reset"}))
    # Drain any clear response
    async for raw in ws:
        data = json.loads(raw)
        if data.get("final"):
            break

    await ws.send(json.dumps({"content": prompt, "command": None}))

    tool_calls = []
    content_parts = []
    stats = None
    tool_call_count = 0
    hit_limit = False

    async for raw in ws:
        data = json.loads(raw)

        for tc in data.get("tool_calls") or []:
            tool_calls.append(tc)
            tool_call_count += 1
            print(f"  ├─ {tc}")

        text = data.get("content") or ""
        if text:
            content_parts.append(text)
            print(text)

        if data.get("stats"):
            stats = data["stats"]

        if data.get("final"):
            break

        # Abort if we've exceeded the tool-call budget for this prompt
        if max_loops is not None and tool_call_count >= max_loops:
            hit_limit = True
            print(f"  ⚠ eval limit reached ({tool_call_count}/{max_loops} tool calls) — resetting")
            await ws.send(json.dumps({"content": "", "command": "reset"}))
            # Drain the reset response
            async for reset_raw in ws:
                reset_data = json.loads(reset_raw)
                if reset_data.get("final"):
                    break
            break

    # Print stats summary
    if stats:
        parts = []
        if stats.get("prompt_tokens"):
            parts.append(f"prompt: {stats['prompt_tokens']} tok")
        if stats.get("completion_tokens"):
            parts.append(f"completion: {stats['completion_tokens']} tok")
        if stats.get("completion_tps"):
            parts.append(f"{stats['completion_tps']} tok/s")
        if stats.get("wall_elapsed_sec"):
            parts.append(f"{stats['wall_elapsed_sec']}s")
        if parts:
            print(f"  [{' | '.join(parts)}]")

    return {
        "tool_calls": tool_calls,
        "response": "\n".join(content_parts),
        "stats": stats,
        "hit_limit": hit_limit,
    }


async def run(host: str, port: int, evals_file: Path) -> None:
    uri = f"ws://{host}:{port}"
    prompts = parse_prompts(evals_file)
    print(f"Loaded {len(prompts)} eval prompts from {evals_file.name}")
    print(f"Connecting to {uri}...")

    async with websockets.connect(uri) as ws:
        # Read welcome message to get model
        raw = await ws.recv()
        welcome = json.loads(raw)
        model = welcome.get("model", "unknown")
        print(f"Model: {model}\n")

        version = get_version()
        results = {
            "version": version,
            "model": model,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "results": [],
        }

        for i, item in enumerate(prompts, 1):
            max_loops = item.get("max_loops")
            loops_label = f" (max_loops={max_loops})" if max_loops else ""
            header = f"[{i}/{len(prompts)}] {item['category']}{loops_label}"
            print(f"\n{'='*60}")
            print(header)
            print(f"{'='*60}")
            print(f"> {item['prompt']}\n")

            result = await send_prompt(ws, item["prompt"], max_loops=max_loops)
            results["results"].append({
                "category": item["category"],
                "prompt": item["prompt"],
                "max_loops": max_loops,
                **result,
            })

    # Save results
    RESULTS_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe_model = re.sub(r"[^\w\-]", "_", model)

    # Markdown output
    md_lines = [
        f"# Eval Results: {model}",
        "",
        f"- **Version:** {version}",
        f"- **Model:** {model}",
        f"- **Timestamp:** {results['timestamp']}",
        "",
        "---",
        "",
    ]
    current_category = ""
    prompt_num = 0
    for entry in results["results"]:
        if entry["category"] != current_category:
            current_category = entry["category"]
            md_lines.append(f"## {current_category}")
            md_lines.append("")
        prompt_num += 1
        md_lines.append(f"### {prompt_num}. {entry['prompt']}")
        md_lines.append("")

        if entry.get("max_loops"):
            status = " — **LIMIT HIT**" if entry.get("hit_limit") else ""
            md_lines.append(f"**Max tool calls:** {entry['max_loops']}{status}")
            md_lines.append("")

        # Stats table
        stats = entry.get("stats")
        if stats:
            md_lines.append("**Stats:**")
            md_lines.append("")
            md_lines.append("| Metric | Value |")
            md_lines.append("|--------|-------|")
            if stats.get("prompt_tokens"):
                md_lines.append(f"| Prompt tokens | {stats['prompt_tokens']} |")
            if stats.get("completion_tokens"):
                md_lines.append(f"| Completion tokens | {stats['completion_tokens']} |")
            if stats.get("total_tokens"):
                md_lines.append(f"| Total tokens | {stats['total_tokens']} |")
            if stats.get("completion_tps"):
                md_lines.append(f"| Completion tok/s | {stats['completion_tps']} |")
            if stats.get("iterations"):
                md_lines.append(f"| Iterations | {stats['iterations']} |")
            if stats.get("llm_calls"):
                md_lines.append(f"| LLM calls | {stats['llm_calls']} |")
            if stats.get("llm_elapsed_sec"):
                md_lines.append(f"| LLM time (s) | {stats['llm_elapsed_sec']} |")
            if stats.get("wall_elapsed_sec"):
                md_lines.append(f"| Wall time (s) | {stats['wall_elapsed_sec']} |")
            # llama.cpp server stats
            server = stats.get("server")
            if server:
                for slot in server.get("slots", []):
                    sid = slot.get("id", "?")
                    if slot.get("n_ctx"):
                        md_lines.append(f"| Slot {sid} n_ctx | {slot['n_ctx']} |")
                    if slot.get("n_past") is not None:
                        md_lines.append(f"| Slot {sid} n_past | {slot['n_past']} |")
                    if slot.get("prompt_tps"):
                        md_lines.append(f"| Slot {sid} prompt tok/s | {slot['prompt_tps']} |")
                    if slot.get("generation_tps"):
                        md_lines.append(f"| Slot {sid} gen tok/s | {slot['generation_tps']} |")
            md_lines.append("")

        if entry["tool_calls"]:
            md_lines.append("**Tool Calls:**")
            for tc in entry["tool_calls"]:
                md_lines.append(f"- `{tc}`")
            md_lines.append("")
        else:
            md_lines.append("**Tool Calls:** *(none)*")
            md_lines.append("")
        md_lines.append("**Response:**")
        md_lines.append("")
        md_lines.append(entry["response"])
        md_lines.append("")
        md_lines.append("---")
        md_lines.append("")

    out_path = RESULTS_DIR / f"eval-{ts}-{safe_model}.md"
    out_path.write_text("\n".join(md_lines))
    print(f"\nResults saved to {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Curunir evals")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--file",
        type=Path,
        default=DEFAULT_EVALS_FILE,
        help="Eval markdown file (default: simple_evals.md)",
    )
    args = parser.parse_args()
    asyncio.run(run(args.host, args.port, args.file))


if __name__ == "__main__":
    main()
