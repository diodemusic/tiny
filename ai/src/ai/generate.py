# type: ignore

"""Generate one batch of training examples for one category.

Usage (from the ai/ folder):
    uv run python -m ai.generate "Greetings and goodbyes"

The prompt text comes from data/generation_prompt.md, so editing that file (or persona.md,
style.md, gold.md) changes what this script sends. The raw reply is saved to data/raw/.
"""

import re
import sys
from pathlib import Path

import anthropic

ROOT = Path(__file__).resolve().parents[3]
DATA = ROOT / "data"
RAW = DATA / "raw"

MODEL = "claude-opus-5-5"
BATCH_SIZE = 20


def fenced_block(markdown: str, heading: str) -> str:
    """Return the first ```txt block that comes after `heading`."""
    after = markdown.split(heading, 1)[1]
    return re.search(r"```txt\n(.*?)\n```", after, re.DOTALL).group(1)


def focus_line(markdown: str, category: str) -> str:
    """Return the {focus} cell for `category` from the focus table."""
    for line in markdown.splitlines():
        cells = [c.strip() for c in line.strip("|").split("|")]
        if cells[0] == category:
            return cells[1]
    raise SystemExit(f"No focus line for category {category!r}")


def eval_prompts() -> str:
    lines = (ROOT / "eval" / "prompts.txt").read_text().splitlines()
    return "\n".join(l for l in lines if l.strip() and not l.startswith("#"))  # noqa: E741


def build_prompts(category: str) -> tuple[str, str]:
    prompt_md = (DATA / "generation_prompt.md").read_text()

    system = fenced_block(prompt_md, "## System prompt")
    system = (
        system.replace("{persona}", (DATA / "persona.md").read_text())
        .replace("{style}", (DATA / "style.md").read_text())
        .replace("{gold}", (DATA / "gold.md").read_text())
        .replace("{eval_prompts}", eval_prompts())
    )

    request = fenced_block(prompt_md, "## Request message")
    request = (
        request.replace("{n}", str(BATCH_SIZE))
        .replace("{category}", category)
        .replace("{focus}", focus_line(prompt_md, category))
        .replace("{avoid_openings}", "none yet, this is the first batch.")
    )
    return system, request


def next_path(category: str) -> Path:
    slug = category.lower().split()[0]  # "Greetings and goodbyes" -> "greetings"
    n = 1
    while (RAW / f"{slug}_{n:03}.txt").exists():
        n += 1
    return RAW / f"{slug}_{n:03}.txt"


def main() -> None:
    category = sys.argv[1]
    system, request = build_prompts(category)

    client = anthropic.Anthropic()
    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        system=[
            {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
        ],
        messages=[{"role": "user", "content": request}],
        output_config={"effort": "medium"},
        # If a safety check declines the request, the API retries it on another model.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )

    if response.stop_reason != "end_turn":
        raise SystemExit(f"Stopped early: {response.stop_reason}")

    text = "".join(b.text for b in response.content if b.type == "text").strip()
    RAW.mkdir(exist_ok=True)
    path = next_path(category)
    path.write_text(text + "\n")

    u = response.usage
    print(f"Saved {text.count('<end>')} examples to {path.relative_to(ROOT)}")
    print(
        f"Tokens: input {u.input_tokens}, cache write {u.cache_creation_input_tokens}, "
        f"cache read {u.cache_read_input_tokens}, output {u.output_tokens}"
    )


if __name__ == "__main__":
    main()
