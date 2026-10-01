#!/usr/bin/env python3
"""Install the managed ignore block without touching app data or Git history."""
import argparse
from pathlib import Path
import sys

START = "# BEGIN STREAMS-AIO PUBLIC SYNC POLICY"
END = "# END STREAMS-AIO PUBLIC SYNC POLICY"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("/home/pi/streams-aio"))
    args = parser.parse_args()
    target = args.source / ".gitignore"
    if not args.source.is_absolute() or not args.source.is_dir() or any(p.is_symlink() for p in (target, *target.parents)):
        raise ValueError()
    original = target.read_text() if target.exists() else ""
    if START in original or END in original:
        if original.count(START) != 1 or original.count(END) != 1 or original.index(START) > original.index(END):
            raise ValueError()
        begin, end = original.index(START), original.index(END) + len(END)
        original = original[:begin].rstrip() + "\n" + original[end:].lstrip("\n")
    policy = (Path(__file__).resolve().parents[1] / ".gitignore").read_text()
    updated = START + "\n" + policy.rstrip() + "\n" + END + "\n\n" + original.strip() + "\n"
    if not target.exists() or target.read_text() != updated:
        target.write_text(updated)
    print("PUBLIC_IGNORE_POLICY PASS; existing custom rules preserved; tracked files unchanged")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("PUBLIC_IGNORE_POLICY FAIL: no configuration values logged", file=sys.stderr)
        sys.exit(1)
