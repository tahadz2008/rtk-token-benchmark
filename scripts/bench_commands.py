#!/usr/bin/env python3
"""Command-level benchmark for RTK v1.0.1.

The upstream claim is "reduces LLM token consumption by 60-90% on common dev
commands". This measures the commands themselves, and measures information
retention alongside token reduction: 40 output tokens from a 10,000-token
input is only a saving if the 40 tokens still answer the question.

Token counting is a regex approximation (`\\w+|[^\\w\\s]`), applied identically
to both sides, so the ratio is fair even though it is not a real BPE tokenizer.

Usage:
    python bench_commands.py --root DIR --src FILE.py --json-file FILE.json \\
        --corpus DIR --corpus DIR --out results/commands.json

Environment fallbacks: RTK_BIN, BENCH_ROOT.
"""
import argparse
import json
import os
import re
import statistics
import subprocess
import sys
import time

SKIP_DIRS = {"node_modules", ".git", "vendor", "build", "dist", "__pycache__", ".next"}
CORPUS_EXTS = {".py", ".js", ".ts", ".tsx", ".md", ".sh", ".json"}
ENV = None  # set in main(): PATH must contain coreutils for rtk's ls/grep proxies


def tok(text):
    """Approximate token count: words plus individual punctuation marks."""
    return len(re.findall(r"\w+|[^\w\s]", text))


def ids(text):
    """Identifiers a reader (human or model) needs to keep the meaning."""
    s = set(re.findall(r"\b(?:def|function|class|const|let|var|async|export)\s+([A-Za-z_$][\w$]{3,})", text))
    s |= set(re.findall(r"\b([A-Z][A-Z0-9_]{4,})\b", text))
    s |= set(re.findall(r'"([^"\n]{3,40})"\s*:', text))
    s |= set(re.findall(r"'([^'\n]{3,40})'\s*:", text))
    s |= set(re.findall(r"^#+\s+(.+)$", text, re.M))
    return s


def run(cmd, shell=False):
    """Return (stdout, returncode). stderr is captured but NOT counted.

    rtk prints a one-line "no hook installed" banner on stderr; the model only
    ever sees stdout, so counting stderr would inflate the "saving".
    """
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180,
                           encoding="utf-8", errors="replace", shell=shell, env=ENV)
        return (r.stdout or "", r.returncode)
    except Exception as exc:  # noqa: BLE001 - a failed command is data too
        return ("__ERROR__%s" % exc, 1)


def measure(name, raw_cmd, rtk_cmd, shell=False):
    t0 = time.time()
    raw, _ = run(raw_cmd, shell)
    t1 = time.time()
    out, rc = run(rtk_cmd, shell)
    t2 = time.time()
    if raw.startswith("__ERROR__") or out.startswith("__ERROR__"):
        return None
    raw_tok = tok(raw)
    if raw_tok == 0:
        return None
    rtk_tok = tok(out)
    raw_ids, kept_ids = ids(raw), ids(out)
    return {
        "cmd": name,
        "raw_tok": raw_tok,
        "rtk_tok": rtk_tok,
        "saved_pct": (1 - rtk_tok / raw_tok) * 100,
        "retention_pct": (len(raw_ids & kept_ids) / len(raw_ids) * 100) if raw_ids else 100.0,
        "raw_ids": len(raw_ids),
        "kept_ids": len(raw_ids & kept_ids),
        "ms_raw": int((t1 - t0) * 1000),
        "ms_rtk": int((t2 - t1) * 1000),
        "rtk_ok": rc == 0,
        "rtk_stdout_bytes": len(out),
    }


def by_extension(rtk, corpus_roots, min_bytes, max_bytes):
    """`rtk read --level aggressive` across a real corpus, grouped by extension."""
    stats = {}
    for root in corpus_roots:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for fn in filenames:
                ext = os.path.splitext(fn)[1].lower()
                if ext not in CORPUS_EXTS:
                    continue
                path = os.path.join(dirpath, fn)
                try:
                    size = os.path.getsize(path)
                except OSError:
                    continue
                if not (min_bytes < size < max_bytes):
                    continue
                try:
                    raw = open(path, "r", encoding="utf-8", errors="replace").read()
                except OSError:
                    continue
                out, rc = run([rtk, "read", "--level", "aggressive", path])
                if rc != 0 or not out.strip():
                    continue
                raw_tok, rtk_tok = tok(raw), tok(out)
                if raw_tok == 0:
                    continue
                raw_ids, kept_ids = ids(raw), ids(out)
                bucket = stats.setdefault(ext, {"n": 0, "saved": [], "ret": []})
                bucket["n"] += 1
                bucket["saved"].append((1 - rtk_tok / raw_tok) * 100)
                bucket["ret"].append((len(raw_ids & kept_ids) / len(raw_ids) * 100) if raw_ids else 100.0)
    return {
        ext: {
            "n": b["n"],
            "median_saved": statistics.median(b["saved"]),
            "median_retention": statistics.median(b["ret"]),
        }
        for ext, b in stats.items()
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rtk", default=os.environ.get("RTK_BIN", "rtk"), help="path to the rtk binary")
    ap.add_argument("--root", default=os.environ.get("BENCH_ROOT", "."), help="directory used for ls/tree")
    ap.add_argument("--src", required=True, help="a real source file, used for `rtk read`")
    ap.add_argument("--json-file", required=True, help="a large .json file, used for json/grep")
    ap.add_argument("--grep", help="word to search inside --json-file (default: first key-ish word)")
    ap.add_argument("--ls-bin", default="ls", help="native ls (on Windows: Git's usr/bin/ls.exe)")
    ap.add_argument("--grep-bin", default="grep", help="native grep (on Windows: Git's usr/bin/grep.exe)")
    ap.add_argument("--corpus", action="append", default=[], help="root of the file corpus (repeatable)")
    ap.add_argument("--min-bytes", type=int, default=800)
    ap.add_argument("--max-bytes", type=int, default=200_000)
    ap.add_argument("--skip-corpus", action="store_true", help="skip the per-extension table (slow)")
    ap.add_argument("--out", default="results/commands.json")
    args = ap.parse_args()

    # rtk proxies the native ls/grep, so their directory must be on PATH.
    global ENV
    ENV = os.environ.copy()
    extra = [os.path.dirname(p) for p in (args.ls_bin, args.grep_bin)
             if os.path.isabs(p)]
    if extra:
        ENV["PATH"] = os.pathsep.join(extra + [ENV.get("PATH", "")])

    if not os.path.isfile(args.rtk) and os.sep in args.rtk:
        sys.exit("rtk binary not found: %s" % args.rtk)

    grep_word = args.grep
    if not grep_word:
        with open(args.json_file, "r", encoding="utf-8", errors="replace") as fh:
            head = fh.read(4000)
        m = re.search(r'"([A-Za-z][\w-]{3,})"\s*:', head)
        grep_word = m.group(1) if m else "name"

    tests = [
        ("ls (plain)", [args.ls_bin, args.root], [args.rtk, "ls", args.root]),
        ("ls -l", [args.ls_bin, "-l", args.root], [args.rtk, "ls", args.root]),
        ("ls -R vs rtk find", [args.ls_bin, "-R", args.root], [args.rtk, "find", args.root]),
        ("grep -n", [args.grep_bin, "-n", grep_word, args.json_file],
         [args.rtk, "grep", grep_word, args.json_file]),
        ("cat vs rtk json", ["cmd", "/c", "type", args.json_file],
         [args.rtk, "json", args.json_file]),
        ("cat vs rtk read", ["cmd", "/c", "type", args.src], [args.rtk, "read", args.src]),
        ("cat vs read aggressive", ["cmd", "/c", "type", args.src],
         [args.rtk, "read", "--level", "aggressive", args.src]),
    ]

    rows = []
    for name, raw_cmd, rtk_cmd in tests:
        row = measure(name, raw_cmd, rtk_cmd)
        if row:
            rows.append(row)

    print("RTK command-level benchmark")
    print("%-24s %10s %10s %9s %12s" % ("command", "raw tok", "rtk tok", "saved", "ids kept"))
    print("-" * 74)
    for r in rows:
        flag = "" if r["rtk_ok"] else "   FAILED (rc != 0)"
        print("%-24s %10d %10d %8.1f%% %7d/%-4d%4.0f%%%s" % (
            r["cmd"], r["raw_tok"], r["rtk_tok"], r["saved_pct"],
            r["kept_ids"], r["raw_ids"], r["retention_pct"], flag))

    ok_saved = [r["saved_pct"] for r in rows if r["rtk_ok"]]
    if ok_saved:
        print("-" * 74)
        print("median saving across successful commands: %.1f%%   mean: %.1f%%" % (
            statistics.median(ok_saved), statistics.mean(ok_saved)))
    failed = [r["cmd"] for r in rows if not r["rtk_ok"]]
    if failed:
        print("commands that failed and were excluded from the median: %s" % ", ".join(failed))

    by_ext = {}
    if args.corpus and not args.skip_corpus:
        print("\nread --level aggressive, by file type")
        by_ext = by_extension(args.rtk, args.corpus, args.min_bytes, args.max_bytes)
        print("%-8s %7s %14s %16s" % ("ext", "files", "median saved", "median ids kept"))
        print("-" * 50)
        for ext, b in sorted(by_ext.items(), key=lambda kv: -kv[1]["median_saved"]):
            print("%-8s %7d %13.1f%% %15.1f%%" % (
                ext, b["n"], b["median_saved"], b["median_retention"]))
        print("total files: %d" % sum(b["n"] for b in by_ext.values()))

    payload = {
        "rtk_version": run([args.rtk, "--version"])[0].strip(),
        "root": os.path.abspath(args.root),
        "src": os.path.abspath(args.src),
        "json_file": os.path.abspath(args.json_file),
        "grep_word": grep_word,
        "ls_bin": args.ls_bin,
        "grep_bin": args.grep_bin,
        "commands": rows,
        "by_ext": by_ext,
    }
    out_dir = os.path.dirname(os.path.abspath(args.out))
    os.makedirs(out_dir, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    print("\n-> %s" % args.out)


if __name__ == "__main__":
    main()
