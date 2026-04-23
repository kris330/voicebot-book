"""Extract named code blocks from chapter markdown files into voicebot-code/chapterXX/."""

import re
import sys
from pathlib import Path

CHAPTERS_DIR = Path(__file__).parent / "chapters"
OUTPUT_DIR = Path(__file__).parent / "voicebot-code"

# Matches the first line of a code block that is a filename comment.
# Accepts:  # filename.ext   or  // filename.ext  (with optional trailing text)
FILENAME_COMMENT = re.compile(r'^(?:#|//)[ \t]+([A-Za-z0-9_./@\-]+\.[a-zA-Z]+)')

# Chapter filename -> output directory number, e.g. ch02-audio-basics.md -> chapter02
CHAPTER_NUM = re.compile(r'^ch(\d+)-')


def extract_chapter(md_path: Path) -> list[tuple[str, str]]:
    """Return list of (relative_filename, code_content) for all named blocks."""
    m = CHAPTER_NUM.match(md_path.name)
    if not m:
        return []

    text = md_path.read_text(encoding="utf-8")
    results: list[tuple[str, str]] = []

    # Match fenced code blocks: ```lang\n<body>\n```
    for block_match in re.finditer(r'```[a-z]*\n(.*?)\n```', text, re.DOTALL):
        body = block_match.group(1)
        first_line = body.split('\n')[0]
        fm = FILENAME_COMMENT.match(first_line)
        if not fm:
            continue
        filename = fm.group(1)
        # Strip the filename comment line from the saved content
        code_lines = body.split('\n')[1:]
        # Remove trailing blank lines
        while code_lines and not code_lines[-1].strip():
            code_lines.pop()
        code = '\n'.join(code_lines) + '\n'
        results.append((filename, code))

    return results


def main() -> None:
    chapter_files = sorted(CHAPTERS_DIR.glob("ch*.md"))
    total_files = 0

    for md_path in chapter_files:
        m = CHAPTER_NUM.match(md_path.name)
        if not m:
            continue
        chapter_num = m.group(1)
        chapter_dir = OUTPUT_DIR / f"chapter{chapter_num}"

        named_blocks = extract_chapter(md_path)
        if not named_blocks:
            continue

        for rel_filename, code in named_blocks:
            # Strip leading slash so absolute system paths become relative
            rel_filename = rel_filename.lstrip("/")
            out_path = chapter_dir / rel_filename
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(code, encoding="utf-8")
            print(f"  {out_path.relative_to(OUTPUT_DIR.parent)}")
            total_files += 1

        print(f"[{md_path.name}] -> {len(named_blocks)} file(s) written to {chapter_dir.name}/")

    print(f"\nDone. {total_files} source file(s) extracted.")


if __name__ == "__main__":
    main()
