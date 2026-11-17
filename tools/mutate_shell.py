"""Mutation testing for kindle/kindle-sky/skyd.sh.

Applies one small change at a time (flip a comparison, swap && and ||, change
a number, delete a command), runs the Kindle tests against that mutant, and
reports mutants the tests did NOT catch ("survivors").

    python tools/mutate_shell.py [--jobs 6] [--shell dash] [--only LINE[,LINE...]]

A mutant counts as caught if any test fails or the run times out (a hang).
Writes a report to mutation-shell-report.txt.
"""
import argparse
import os
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "kindle" / "kindle-sky" / "skyd.sh"

SWAPS = [
    (r" -ge ", " -gt "), (r" -gt ", " -ge "), (r" -le ", " -lt "), (r" -lt ", " -le "),
    (r" -eq ", " -ne "), (r" -ne ", " -eq "), (r'" = "', '" != "'), (r'" != "', '" = "'),
    (r" = ([\w\"])", r" != \1"), (r" && ", " || "), (r" \|\| ", " && "),
    (r"return 1\b", "return 0"), (r"return 0\b", "return 1"), (r"\bif ! ", "if "),
    (r"exit 0\b", "exit 1"), (r"=https", "=http"), (r" -f ", " -e "), (r" -s ", " -e "),
    (r"\+ 1\)", "+ 2)"), (r"- 1 -", "- 0 -"), (r"\* 8 \+", "* 10 +"),
]
COMMANDS = ("lipc-set-prop", "start ", "stop ", "kill ", "rm ", "mv ", "show_image", "request_exit",
            "trap ", "mkdir ", "echo ")
NUMBER = re.compile(r"(?<![\w$.{-])(\d+)(?![\w.])")
SKIP_LINE = re.compile(r"^\s*(#|$|log |esac|;;|fi$|done$|else$|\}$|then$|do$)")


def mutants(lines):
    # The user-settings block holds defaults that config.sh overrides (and the
    # tests always supply config.sh), so mutating it is untestable by design.
    start = next(i for i, ln in enumerate(lines) if ln.startswith("# ---- settings"))
    end = next(i for i, ln in enumerate(lines) if "config.sh\" ] && ." in ln)
    for i, line in enumerate(lines):
        if start <= i <= end:
            continue
        if SKIP_LINE.match(line) or line.lstrip().startswith(("echo \"usage", "PNG_HEADER=")):
            continue
        code = line.split(" #")[0]
        for pat, rep in SWAPS:
            for m in re.finditer(pat, code):
                new = code[:m.start()] + m.expand(rep) + code[m.end():]
                yield i, f"{pat.strip()} -> {rep.strip()}", new + line[len(code):]
        for m in NUMBER.finditer(code):
            if "$((" not in code and not re.match(r"^\s*[A-Z_]+=", code) and "[ " not in code and "-y" not in code:
                continue
            n = int(m.group(1))
            new = code[:m.start()] + str(n + 1) + code[m.end():]
            yield i, f"{n} -> {n + 1}", new + line[len(code):]
        stripped = code.strip()
        if stripped.startswith(COMMANDS):
            yield i, "delete command", line[:len(line) - len(line.lstrip())] + ":"


def run_one(job, shell, timeout):
    idx, (line_no, desc, new_line), lines = job
    with tempfile.TemporaryDirectory(prefix="mut-") as d:
        path = Path(d) / "skyd.sh"
        mutated = list(lines)
        mutated[line_no] = new_line
        path.write_text("\n".join(mutated) + "\n")
        env = dict(os.environ, KSKY_SCRIPT=str(path))
        cmd = [sys.executable, "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider", "tests/test_kindle.py",
               "-k", shell, "--basetemp", str(Path(d) / "t")]
        t0 = time.time()
        try:
            res = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True, timeout=timeout,
                                 check=False)
            caught = res.returncode != 0
            how = "killed" if caught else "SURVIVED"
        except subprocess.TimeoutExpired:
            caught, how = True, "killed (timeout)"
        finally:
            # A mutant can break skyd.sh's own cleanup and leave sky-mode processes
            # running; kill everything that runs from this mutant's directory.
            subprocess.run(["pkill", "-9", "-f", d], check=False)
        return idx, line_no + 1, desc, new_line.strip(), caught, how, time.time() - t0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=6)
    ap.add_argument("--shell", default="dash")
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--only", help="comma-separated line numbers")
    ap.add_argument("--report", default=str(ROOT / "mutation-shell-report.txt"))
    args = ap.parse_args(argv)
    lines = SCRIPT.read_text().splitlines()
    all_m = list(mutants(lines))
    if args.only:
        keep = {int(x) for x in args.only.split(",")}
        all_m = [m for m in all_m if m[0] + 1 in keep]
    print(f"{len(all_m)} mutants, {args.jobs} at a time", flush=True)
    jobs = [(i, m, lines) for i, m in enumerate(all_m)]
    results = []
    with ThreadPoolExecutor(args.jobs) as ex:
        for r in ex.map(lambda j: run_one(j, args.shell, args.timeout), jobs):
            results.append(r)
            print(f"[{len(results)}/{len(jobs)}] line {r[1]:4d} {r[2]:22s} {r[5]} ({r[6]:.0f}s)", flush=True)
    survivors = [r for r in results if not r[4]]
    with open(args.report, "w") as fh:
        fh.write(f"skyd.sh mutation testing: {len(results)} mutants, {len(results) - len(survivors)} caught, "
                 f"{len(survivors)} survived ({100 * (len(results) - len(survivors)) / max(1, len(results)):.1f}% "
                 "caught)\n\nSurvivors:\n")
        for r in survivors:
            fh.write(f"  line {r[1]}: {r[2]}: {r[3]}\n")
    print(Path(args.report).read_text())
    return 0


if __name__ == "__main__":
    sys.exit(main())
