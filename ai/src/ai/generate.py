# type: ignore

"""Generate one batch of training examples for one category.

Usage (from the ai/ folder):
    uv run python -m ai.generate "Greetings and goodbyes"

The prompt text comes from data/generation_prompt.md, so editing that file (or persona.md,
style.md, gold.md) changes what this script sends. The raw reply is saved to data/raw/.
"""

import re
import sys
from collections import Counter
from pathlib import Path

import anthropic

ROOT = Path(__file__).resolve().parents[3]
DATA = ROOT / "data"
RAW = DATA / "raw"

MODEL = "claude-opus-5-5"
BATCH_SIZE = 20

# An opening goes on the avoid list once it has been used this many times...
MIN_REPEATS = 2
# ...and at most this many are listed, most common first, to keep the request short.
MAX_AVOID = 15

# Categories whose {focus} line rotates through a list in data/focus/:
# category -> (placeholder, list file, items per batch)
FOCUS_LISTS = {
    "Greetings and goodbyes": ("{situations}", "greetings.txt", 3),
}


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


def read_list(path: Path) -> list[str]:
    """One item per line, skipping blank lines and # comments."""
    lines = path.read_text().splitlines()
    return [l.strip() for l in lines if l.strip() and not l.startswith("#")]  # noqa: E741


def rotate(items: list[str], per_batch: int, batch: int) -> list[str]:
    """Batch 1 gets items 0-2, batch 2 gets 3-5, ... wrapping round at the end of the list."""
    start = (batch - 1) * per_batch
    return [items[(start + i) % len(items)] for i in range(per_batch)]


def fill_focus(category: str, focus: str, batch: int) -> str:
    """Put this batch's items from the category's list into its focus line."""
    if category not in FOCUS_LISTS:
        return focus
    placeholder, filename, per_batch = FOCUS_LISTS[category]
    items = rotate(read_list(DATA / "focus" / filename), per_batch, batch)
    return focus.replace(placeholder, "; ".join(items))


def opening(answer: str) -> str:
    """The first three words, lowercase, without punctuation: "Hello! It is..." -> "hello it is"."""
    return " ".join(re.sub(r"[^a-z' ]", "", answer.lower()).split()[:3])


def avoid_openings() -> str:
    """The most repeated answer openings across every batch saved so far, in all categories."""
    counts = Counter()
    for path in sorted(RAW.glob("*.txt")):
        for answer in re.findall(r"^<bot>(.*)$", path.read_text(), re.MULTILINE):
            counts[opening(answer)] += 1

    common = [f'"{o}"' for o, c in counts.most_common(MAX_AVOID) if c >= MIN_REPEATS]
    return ", ".join(common) + "." if common else "none yet."


def build_prompts(category: str, batch: int) -> tuple[str, str]:
    prompt_md = (DATA / "generation_prompt.md").read_text()

    system = fenced_block(prompt_md, "## System prompt")
    system = (
        system.replace("{persona}", (DATA / "persona.md").read_text())
        .replace("{style}", (DATA / "style.md").read_text())
        .replace("{gold}", (DATA / "gold.md").read_text())
        .replace("{eval_prompts}", "\n".join(read_list(ROOT / "eval" / "prompts.txt")))
    )

    focus = fill_focus(category, focus_line(prompt_md, category), batch)
    request = fenced_block(prompt_md, "## Request message")
    request = (
        request.replace("{n}", str(BATCH_SIZE))
        .replace("{category}", category)
        .replace("{focus}", focus)
        .replace("{avoid_openings}", avoid_openings())
    )
    return system, request


def next_batch(category: str) -> tuple[int, Path]:
    """The next unused batch number for this category, and the file it will be saved to."""
    slug = category.lower().split()[0]  # "Greetings and goodbyes" -> "greetings"
    n = 1
    while (RAW / f"{slug}_{n:03}.txt").exists():
        n += 1
    return n, RAW / f"{slug}_{n:03}.txt"


def main() -> None:
    category = sys.argv[1]
    batch, path = next_batch(category)
    system, request = build_prompts(category, batch)
    print(f"Batch {batch}. Focus: {request.splitlines()[2]}")
    print("Avoiding openings:", avoid_openings())

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
    path.write_text(text + "\n")

    u = response.usage
    print(f"Saved {text.count('<end>')} examples to {path.relative_to(ROOT)}")
    print(
        f"Tokens: input {u.input_tokens}, cache write {u.cache_creation_input_tokens}, "
        f"cache read {u.cache_read_input_tokens}, output {u.output_tokens}"
    )


if __name__ == "__main__":
    main()
