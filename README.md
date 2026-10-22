# RTK token-savings benchmark

Measurements behind **[EP01 — الأدوات الحرة](https://youtu.be/cv2KM2u1j9c)**: a
hands-on test of [rtk-ai/rtk](https://github.com/rtk-ai/rtk) v1.0.1, the tool
that claims to cut LLM token usage by **60–90%**.

Everything in `results/` was produced by the scripts in `scripts/`, on one
Windows 11 machine, in October 2026.

---

## TL;DR

| command | raw tokens | rtk tokens | saved |
|---|---:|---:|---:|
| `cat` → `rtk json` (package-lock.json) | 41,956 | 2,640 | **93.7%** |
| `ls -R` → `rtk find` (whole project tree) | 3,430 | 254 | **92.6%** |
| `grep -n` → `rtk grep` (one large file) | 2,827 | 468 | **83.4%** |
| `ls -l` → `rtk ls` | 188 | 56 | 70.2% |
| `cat` → `rtk read` (a .py source file) | 13,847 | 13,847 | **0.0%** |
| `ls` → `rtk ls` (plain listing) | 46 | 56 | **−21.7%** (it *adds* tokens) |

Two findings matter more than the headline:

1. **`rtk read` is a passthrough by default.** 0% saved, 100% of the file
   returned verbatim. The savings only exist at `--level minimal` /
   `--level aggressive` — and aggressive compression drops identifiers.
2. **On Windows, `ls`, `tree` and `grep` do not work out of the box.** They
   proxy the native Unix binaries, so without GNU coreutils on `PATH` they fail
   and print an error. Measuring the *error message* instead of the output is
   how you end up quoting a fake "99% saving".

---

## Method

- **Token count** — `len(re.findall(r"\w+|[^\w\s]", text))`. A regex
  approximation of a BPE tokenizer, applied **identically to both sides**, so
  the ratio is meaningful even though the absolute count is not a real token
  count. Same function in both scripts.
- **What the model receives** — `stdout` only. `rtk` writes a one-line
  "no hook installed" banner to `stderr`; counting it would inflate the
  measured saving. The banner is also why the same command can look like it
  "gained" tokens.
- **Information retention** — the share of *identifiers* from the input that
  survive in the output: `def`/`function`/`class`/`const`/`let`/`var` names,
  `UPPER_CASE` constants, JSON keys, and markdown headings. A tool that
  returns 40 tokens from a 10,000-token input is only a win if those 40 tokens
  still answer the question.
- **Like for like** — the raw command and the rtk command do the *same task*
  on the *same input*. `ls -R` is compared with `rtk find` (a full tree), not
  with `rtk ls` (top level only).
- **Failed commands are excluded** from the medians and flagged `FAILED
  (rc != 0)` in the output, so an error message can never be counted as a
  saving.

## Corpus

277 real files, 800 B – 200 KB, from three real project trees
(`products/`, `video-ad-editor/scripts/`, `remotion-best-practices/`):

| ext | files | median saved | median identifiers kept |
|---|---:|---:|---:|
| `.py` | 85 | 87.2% | 55.6% |
| `.tsx` | 16 | 79.1% | 52.4% |
| `.ts` | 8 | 79.1% | 55.6% |
| `.js` | 25 | 70.8% | 78.6% |
| `.md` | 109 | 0.0% | 100.0% |
| `.json` | 25 | 0.0% | 100.0% |
| `.sh` | 9 | 0.0% | 100.0% |

`rtk read --level aggressive` per file type. Markdown, JSON and shell are
returned **untouched** — 0%, not "low".

## Filter levels (70 files, all three levels measured)

| level | median saved | mean saved | median identifiers kept | files in the advertised 60–90% band |
|---|---:|---:|---:|---:|
| `none` (default) | 0.0% | 0.0% | 100.0% | 0% |
| `minimal` | 0.0% | 3.3% | 100.0% | 0% |
| `aggressive` | 0.0% | 14.2% | 100.0% | 11.4% |

At `--level aggressive`, 11.4% of files land inside the "60–90%" band and
17.1% of files are compressed at all. The rest are passed through.

## Caveats

- One machine, one rtk version (1.0.1), one corpus, one day.
- The token count is a regex approximation, not a real tokenizer.
- Retention is a proxy: it measures whether *names* survive, not whether the
  output is *useful*.
- `rtk grep` truncates long match lists; the saving is real but it drops
  matches.
- Percentages will differ on your machine, on your files, and with a
  different rtk version. The shape of the result should not.

## Reproduce

```bash
# 1. get rtk (v1.0.1 used here)
#    https://github.com/rtk-ai/rtk

# 2. command-level benchmark
python scripts/bench_commands.py \
  --rtk      /path/to/rtk \
  --root     /path/to/some/project \
  --src      /path/to/some/project/src/main.py \
  --json-file /path/to/some/project/package-lock.json \
  --grep     remotion \
  --corpus   /path/to/corpus/a \
  --corpus   /path/to/corpus/b \
  --out      results/commands.json

# 3. file-level benchmark, all three filter levels
python scripts/bench_files.py \
  --rtk    /path/to/rtk \
  --corpus /path/to/corpus/a \
  --corpus /path/to/corpus/b \
  --limit  70 \
  --out    results/files_by_level.json
```

**On Windows**, `rtk ls` / `rtk tree` / `rtk grep` need GNU coreutils on
`PATH`. Point `--ls-bin` / `--grep-bin` at Git's copy and the script puts its
directory on `PATH` for the rtk child process:

```powershell
python scripts/bench_commands.py `
  --rtk       "C:\path\to\rtk.exe" `
  --ls-bin    "C:\Program Files\Git\usr\bin\ls.exe" `
  --grep-bin  "C:\Program Files\Git\usr\bin\grep.exe" `
  ...
```

Environment fallbacks: `RTK_BIN` for the binary, `BENCH_ROOT` for `--root`.

## Files

```
scripts/bench_commands.py   command-level benchmark + per-extension table
scripts/bench_files.py      file-level benchmark, three filter levels
results/commands.json       raw output of bench_commands.py
results/files_by_level.json raw output of bench_files.py
```

## Video

**EP01 — أداة توفّر 93.7% على JSON… و0% على كودك | RTK**
https://youtu.be/cv2KM2u1j9c

English voiceover, Arabic captions on screen. The video walks through these
same numbers and shows where the 60–90% claim holds — and where it does not.

---

MIT licensed. Measurements and scripts © الأدوات الحرة.
