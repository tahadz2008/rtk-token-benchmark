#!/usr/bin/env python3
"""File-level benchmark for RTK v1.0.1: does `rtk read` actually save tokens?

v1 of this benchmark revealed the core trap: `rtk read` defaults to
`--level none`, which is a byte-for-byte passthrough. Savings only appear when
the user adds `--level minimal` or `--level aggressive` - and aggressive
compression keeps fewer identifiers.

This measures all three levels on a deterministic sample of real source files:
  1. token reduction per level
  2. identifier retention (do the names that carry meaning survive?)
  3. how many files land inside the repo's advertised 60-90% band

Token counting is a regex approximation (`\\w+|[^\\w\\s]`) applied identically
to both sides, so the ratio is fair even though it is not a real BPE tokenizer.

Usage:
    python bench_files.py --corpus DIR --corpus DIR --out results/files_by_level.json

Environment fallbacks: RTK_BIN.
"""
import argparse
import json
import os
import re
import statistics
import subprocess
import sys

SKIP_DIRS = {"node_modules", ".git", "vendor", "build", "dist", "__pycache__", ".next", "tests"}
EXTS = {".py", ".js", ".ts", ".tsx", ".md", ".sh"}
LEVELS = [("none", []), ("minimal", ["--level", "minimal"]), ("aggressive", ["--level", "aggressive"])]


def tokens(text):
    return len(re.findall(r"\w+|[^\w\s]", text))


def sigs(text):
    """Semantically meaningful identifiers - if these vanish, information was lost."""
    s = set(re.findall(r"\b(?:def|function|class|const|let|var|async|export|import)\s+([A-Za-z_$][\w$]{3,})", text))
    s |= set(re.findall(r"\b([A-Z][A-Z0-9_]{4,})\b", text))
    s |= set(re.findall(r"^import\s+([\w.]+)", text, re.M))
    return s


def run(cmd):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120,
                           encoding="utf-8", errors="replace")
        return r.stdout or ""
    except Exception:  # noqa: BLE001
        return ""


def collect(roots, limit, min_bytes, max_bytes):
    files = []
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for fn in filenames:
                if os.path.splitext(fn)[1].lower() not in EXTS:
                    continue
                path = os.path.join(dirpath, fn)
                try:
                    size = os.path.getsize(path)
                except OSError:
                    continue
                if min_bytes < size < max_bytes:
                    files.append(path)
    files.sort()
    return files[:limit] if limit else files


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rtk", default=os.environ.get("RTK_BIN", "rtk"))
    ap.add_argument("--corpus", action="append", required=True, help="root of the corpus (repeatable)")
    ap.add_argument("--limit", type=int, default=70, help="0 = every matching file")
    ap.add_argument("--min-bytes", type=int, default=800)
    ap.add_argument("--max-bytes", type=int, default=250_000)
    ap.add_argument("--out", default="results/files_by_level.json")
    args = ap.parse_args()

    files = collect(args.corpus, args.limit, args.min_bytes, args.max_bytes)
    if not files:
        sys.exit("no matching files under: %s" % ", ".join(args.corpus))

    rows = []
    for path in files:
        try:
            raw = open(path, "r", encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        raw_tok = tokens(raw)
        if raw_tok < 50:
            continue
        raw_sigs = sigs(raw)
        row = {"file": os.path.basename(path), "raw_tok": raw_tok, "levels": {}}
        for name, extra in LEVELS:
            out = run([args.rtk, "read", *extra, path])
            if not out.strip():
                row["levels"][name] = None
                continue
            out_tok = tokens(out)
            kept = len(raw_sigs & sigs(out))
            row["levels"][name] = {
                "tok": out_tok,
                "saved_pct": (1 - out_tok / raw_tok) * 100,
                "retention_pct": (kept / len(raw_sigs) * 100) if raw_sigs else 100.0,
                "sig_kept": kept,
                "sig_total": len(raw_sigs),
            }
        rows.append(row)

    if not rows:
        sys.exit("no data")

    summary = {}
    for name, _ in LEVELS:
        vals = [r["levels"][name] for r in rows if r["levels"].get(name)]
        if not vals:
            continue
        saved = [v["saved_pct"] for v in vals]
        ret = [v["retention_pct"] for v in vals]
        summary[name] = {
            "n": len(vals),
            "median_saved": statistics.median(saved),
            "mean_saved": statistics.mean(saved),
            "median_retention": statistics.median(ret),
            "min_retention": min(ret),
            "pct_claim_met": sum(1 for s in saved if 60 <= s <= 90) / len(saved) * 100,
            "pct_any_saving": sum(1 for s in saved if s > 5) / len(saved) * 100,
        }

    print("RTK read - %d real source files" % len(rows))
    print("%-12s %12s %12s %16s %14s" % ("level", "median save", "mean save", "median ids kept", "in 60-90% band"))
    print("-" * 70)
    for name, s in summary.items():
        print("%-12s %11.1f%% %11.1f%% %15.1f%% %13.0f%%" % (
            name, s["median_saved"], s["mean_saved"], s["median_retention"], s["pct_claim_met"]))
    print("\nfiles where any saving occurred (>5%):")
    for name, s in summary.items():
        print("  %-12s %5.1f%%" % (name, s["pct_any_saving"]))

    out_dir = os.path.dirname(os.path.abspath(args.out))
    os.makedirs(out_dir, exist_ok=True)
    payload = {
        "rtk_version": run([args.rtk, "--version"]).strip(),
        "n": len(rows),
        "corpora": [os.path.abspath(c) for c in args.corpus],
        "summary": summary,
        "rows": rows,
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    print("\n-> %s" % args.out)


if __name__ == "__main__":
    main()
