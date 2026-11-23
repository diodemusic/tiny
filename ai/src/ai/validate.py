"""Check training examples against the format and the "auto" rules in data/style.md.

Usage (from the ai/ folder):
    uv run python -m ai.validate                      # every file in data/raw/
    uv run python -m ai.validate ../data/raw/greetings_007.txt

Errors break a rule in style.md. Warnings are things to look at by eye: they might be fine.
Exits with code 1 if there are any errors, so other scripts can tell it failed.
"""

import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

RAW = Path(__file__).resolve().parents[3] / "data" / "raw"

MAX_SENTENCES = 2
MAX_WORDS = 12  # per sentence
MAX_ANSWER_CHARS = 150
MAX_PROMPT_CHARS = 80

# Small words that may appear three or more times in one answer.
SMALL_WORDS = set(
    "a an the i is am are was it to you your and or but of in on at for with my me "
    "so be too that this do not we us if very".split()
)
# 's after these words is a contraction ("it's"). After other words it is
# a possessive ("grandma's") or a plural letter ("o's"), which are fine.
APOSTROPHE_S_CONTRACTIONS = set(
    "it that what there here he she who where how let".split()
)
NUMBER_WORDS = set(
    "two three four five six seven eight nine ten eleven twelve hundred thousand million".split()
)


@dataclass
class Example:
    path: Path
    line: int  # line number of the <user> line
    user: str
    bot: str


@dataclass
class Problem:
    level: str  # "error" or "warning"
    path: Path
    line: int
    message: str
    text: str = ""

    def __str__(self) -> str:
        where = f"{self.path.name}:{self.line}"
        detail = f"  | {self.text}" if self.text else ""
        return f"{self.level:7} {where:22} {self.message}{detail}"


def parse(path: Path) -> tuple[list[Example], list[Problem]]:
    """Read a file of <user>/<bot>/<end> blocks. Anything else is a format error."""
    lines = path.read_text().split("\n")
    if lines and lines[-1] == "":
        lines.pop()  # the file's final newline

    examples, problems = [], []
    i = 0
    while i < len(lines):
        block = lines[i : i + 3]
        if (
            len(block) == 3
            and block[0].startswith("<user>")
            and block[1].startswith("<bot>")
            and block[2] == "<end>"
        ):
            examples.append(Example(path, i + 1, block[0][6:], block[1][5:]))
            i += 3
        else:
            problems.append(
                Problem(
                    "error", path, i + 1, "not in <user>/<bot>/<end> format", lines[i]
                )
            )
            i += 1
            while i < len(lines) and not lines[i].startswith("<user>"):
                i += 1  # skip ahead to the next example
    return examples, problems


def sentences(answer: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", answer.strip()) if s]


def check_answer(answer: str) -> list[tuple[str, str]]:
    """Return (level, message) for every rule the answer breaks."""
    found = []

    def error(message: str) -> None:
        found.append(("error", message))

    if not answer.strip():
        return [("error", "empty answer")]
    if bad := sorted(
        set(re.sub(r"[A-Za-z0-9 .,!?'\"]", "", answer))
    ):  # digits: own rule below
        error(f"characters not allowed: {' '.join(bad)}")
    if "<" in answer or ">" in answer:
        error("contains < or >")
    if len(answer) > MAX_ANSWER_CHARS:
        error(f"{len(answer)} characters (max {MAX_ANSWER_CHARS})")
    if re.search(r"\d", answer):
        error("contains a digit")
    if ".." in answer:
        error('contains "..." or ".."')
    if answer.count("!") > 1:
        error("more than one !")
    if answer.count("?") > 1:
        error("more than one ?")

    parts = sentences(answer)
    if len(parts) > MAX_SENTENCES:
        error(f"{len(parts)} sentences (max {MAX_SENTENCES})")
    for s in parts:
        words = len(s.split())
        if words > MAX_WORDS:
            error(f"sentence has {words} words (max {MAX_WORDS}): {s!r}")
        if not s.lstrip('"')[:1].isupper():
            error(f"sentence does not start with a capital: {s!r}")
        if s.rstrip('"')[-1:] not in ".!?":
            error(f"sentence does not end with . ! or ?: {s!r}")
        if s.count(",") >= 3:
            found.append(
                ("warning", f"three or more commas, maybe a list of 4+: {s!r}")
            )

    for word in re.findall(r"\b[A-Za-z]+'[A-Za-z]+\b", answer):
        base, end = word.lower().split("'", 1)
        if end in {"t", "m", "re", "ll", "ve", "d"} or (
            end == "s" and base in APOSTROPHE_S_CONTRACTIONS
        ):
            error(f"contraction {word!r}")

    if re.search(r"\w+, \w+(?: \w+)?, and \w+", answer):
        error('comma before "and" in a list')
    if re.search(r"raspberry pi", answer, re.I) and "Raspberry Pi" not in answer:
        error('spell it "Raspberry Pi"')
    if re.search(r"\bsd ?card", answer, re.I) and "SD card" not in answer:
        error('spell it "SD card"')
    if re.search(r"\bTiny\b", answer):
        error('"tiny" is always lowercase')

    counts = Counter(
        w for w in re.findall(r"[a-z']+", answer.lower()) if w not in SMALL_WORDS
    )
    for word, n in counts.items():
        if n >= 3:
            error(f"{word!r} used {n} times")

    if numbers := [
        w for w in re.findall(r"[a-z]+", answer.lower()) if w in NUMBER_WORDS
    ]:
        found.append(
            ("warning", f"number word {', '.join(numbers)}: is tiny counting?")
        )
    return found


def check_user(prompt: str) -> list[tuple[str, str]]:
    found = []
    if not prompt.strip():
        found.append(("error", "empty user message"))
    if len(prompt) > MAX_PROMPT_CHARS:
        found.append(
            (
                "error",
                f"user message is {len(prompt)} characters (max {MAX_PROMPT_CHARS})",
            )
        )
    if "<" in prompt or ">" in prompt:
        found.append(("error", "user message contains < or >"))
    if not prompt.isascii():
        found.append(("error", "user message is not plain ASCII"))
    return found


def validate_file(path: Path) -> tuple[list[Example], list[Problem]]:
    examples, problems = parse(path)
    for ex in examples:
        for level, message in check_user(ex.user):
            problems.append(Problem(level, path, ex.line, message, ex.user))
        for level, message in check_answer(ex.bot):
            problems.append(Problem(level, path, ex.line + 1, message, ex.bot))
    return examples, sorted(problems, key=lambda p: p.line)


def opening(answer: str) -> str:
    """The first three words, lowercase, without punctuation."""
    return " ".join(re.sub(r"[^a-z' ]", "", answer.lower()).split()[:3])


def summary(examples: list[Example]) -> list[str]:
    """Rules that apply to the whole dataset, not to one answer."""
    n = len(examples)
    if not n:
        return []
    bangs = sum("!" in ex.bot for ex in examples)
    questions = sum("?" in ex.bot for ex in examples)
    top, top_count = Counter(opening(ex.bot) for ex in examples).most_common(1)[0]
    return [
        f'answers with "!": {bangs}/{n} ({bangs / n:.0%}, aim for at most about 33%)',
        f'answers with "?": {questions}/{n} ({questions / n:.0%}, aim for at most about 20%)',
        f'most common opening: "{top}" in {top_count}/{n} ({top_count / n:.0%}, max 5%)',
    ]


def main() -> None:
    paths = [Path(p) for p in sys.argv[1:]] or sorted(RAW.glob("*.txt"))
    all_examples, all_problems = [], []
    for path in paths:
        examples, problems = validate_file(path)
        all_examples += examples
        all_problems += problems

    for p in all_problems:
        print(p)
    errors = sum(p.level == "error" for p in all_problems)
    warnings = len(all_problems) - errors
    files = f"{len(paths)} file" + ("s" if len(paths) != 1 else "")
    print(
        f"\n{len(all_examples)} examples in {files}: {errors} errors, {warnings} warnings"
    )
    for line in summary(all_examples):
        print("  " + line)
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
