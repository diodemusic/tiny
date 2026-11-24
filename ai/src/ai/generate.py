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

from ai.validate import opening, validate_file

ROOT = Path(__file__).resolve().parents[3]
DATA = ROOT / "data"
RAW = DATA / "raw"

MODEL = "claude-opus-5-5"
BATCH_SIZE = 20

# An opening goes on the avoid list once it has been used this many times...
MIN_REPEATS = 2
# ...and at most this many are listed, most common first, to keep the request short.
MAX_AVOID = 15

# Categories whose {focus} line rotates through a list:
# category -> (placeholder, list file in data/focus/ or a function that returns the list, items per batch)
FOCUS_LISTS = {
    "Greetings and goodbyes": ("{situations}", "greetings.txt", 3),
    # 24 rows of persona.md / 2 per batch = 12 batches, so 60 batches show every topic 5 times.
    # (A lambda, because about_topics is defined further down the file.)
    "About tiny": ("{topics}", lambda: about_topics(), 2),
    # 100 topics / 4 per batch = 25 batches, so each topic is in exactly one batch.
    "Simple facts": ("{topics}", "fact_topics.txt", 4),
    # 66 inputs / 6 per batch = 11 batches, so 22 batches show every input exactly twice.
    "Compliments and reactions": ("{inputs}", "reactions.txt", 6),
}

# The start of each category's file names in data/raw/: "facts" -> facts_001.txt.
# (The first word of the category isn't enough: "Simple facts" and "Simple stories" would clash.)
FILE_NAMES = {
    "Greetings and goodbyes": "greetings",
    "About tiny": "about",
    "Compliments and reactions": "reactions",
    "Simple stories": "stories",
    "Simple facts": "facts",
    "Things tiny can't do": "cant_do",
    "Gentle redirects": "redirects",
    "Odd inputs": "odd",
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


def section(markdown: str, heading: str) -> str:
    """The text under `heading`, up to the next ## heading."""
    after = markdown.split(heading + "\n", 1)[1]
    return after.split("\n## ", 1)[0]


def table_rows(text: str) -> list[list[str]]:
    """The cells of every row of the first markdown table in `text`, without the header rows."""
    rows = [line for line in text.splitlines() if line.startswith("|")]
    return [[c.strip() for c in row.strip("|").split("|")] for row in rows[2:]]


def about_topics() -> list[str]:
    """One topic per row of persona.md: the canon facts, then the likes, then the dislikes.

    Read straight from persona.md, so editing a fact there changes the prompts too.
    """
    persona = (DATA / "persona.md").read_text()
    facts = [f"{topic}: {fact}" for topic, fact, _ in table_rows(section(persona, "## Canon facts"))]
    likes_text = section(persona, "## What tiny likes")
    likes = [f"What tiny likes. {thing}: {fav}" for thing, fav in table_rows(likes_text)]
    dislikes = [f"What tiny dislikes: {line[2:].rstrip('.')}" for line in likes_text.splitlines() if line.startswith("- ")]
    return facts + likes + dislikes


def rotate(items: list[str], per_batch: int, batch: int) -> list[str]:
    """Batch 1 gets items 0-2, batch 2 gets 3-5, ... wrapping round at the end of the list."""
    start = (batch - 1) * per_batch
    return [items[(start + i) % len(items)] for i in range(per_batch)]


def fill_focus(category: str, focus: str, batch: int) -> str:
    """Put this batch's items from the category's list into its focus line."""
    if category not in FOCUS_LISTS:
        return focus
    placeholder, source, per_batch = FOCUS_LISTS[category]
    all_items = source() if callable(source) else read_list(DATA / "focus" / source)
    items = rotate(all_items, per_batch, batch)
    return focus.replace(placeholder, "; ".join(items))


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
    if category not in FILE_NAMES:
        raise SystemExit(
            f"Unknown category {category!r}. Choose one of: {', '.join(FILE_NAMES)}"
        )
    slug = FILE_NAMES[category]
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

    # Check the new batch straight away, so problems show up before the next run.
    _, problems = validate_file(path)
    if problems:
        print(f"\nValidator found {len(problems)} problem(s) to fix or check:")
        for p in problems:
            print(p)
    else:
        print("Validator: every example passes.")


if __name__ == "__main__":
    main()
