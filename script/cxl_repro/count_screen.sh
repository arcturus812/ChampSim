#!/bin/bash
# count_screen.sh -- does a workload's instruction count survive the move from
# valgrind's coordinate system to Pin's?
#
# SimPoint intervals are indexed in valgrind's instruction count; Pin's tracer takes a
# skip in its own. The two disagree only on rep-prefixed string operations: valgrind
# counts `rep stosb` once, Pin counts every iteration, because for a memory trace every
# iteration is a distinct access. Measured on two probes: an explicit store loop agrees
# to 0.005%, a glibc memset diverges by 212x.
#
# valgrind reports both totals, so the Pin count can be predicted without running Pin:
#
#     pin_count ~= valgrind_instructions + valgrind_reps
#
# verified at 0.005% and 0.014% error on those same two probes. The ratio
# (instructions + reps) / instructions is therefore the screen:
#
#     ~1.00   valgrind skip points transfer to Pin directly
#     >1.05   they do not; the coordinates must be converted or BBVs collected with Pin
#
# This matters here more than usual because the workloads being sought are exactly the
# streaming-write ones, and memset is how a program writes memory it never reads.
#
# Usage: count_screen.sh <name> -- <program> [args...]     (run from the program's cwd)
set -u
OUT="${SCREEN_OUT:-$HOME/workspace/simulator/ChampSim/script/cxl_repro/simpoint}"
mkdir -p "$OUT"

NAME=${1:?usage: count_screen.sh <name> -- <program> [args...]}
shift
[ "${1:-}" = "--" ] && shift

BB="$OUT/$NAME.bb"
LOG="$OUT/$NAME.screen.log"

START=$(date +%s)
valgrind --tool=exp-bbv --interval-size=100000000 --bb-out-file="$BB" -- "$@" \
    > "$OUT/$NAME.stdout" 2> "$LOG"
RC=$?
I=$(grep -oE 'Total instructions: [0-9]+' "$LOG" | head -1 | grep -oE '[0-9]+')
R=$(grep -oE 'Total reps: [0-9]+' "$LOG" | head -1 | grep -oE '[0-9]+')
N=$(grep -c '^T' "$BB" 2>/dev/null); N=${N%%$'\n'*}
I=${I:-0}; R=${R:-0}

python3 - "$NAME" "$I" "$R" "$N" "$(( $(date +%s) - START ))" "$RC" <<'EOF'
import sys
name, i, r, n, secs, rc = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5]), sys.argv[6]
ratio = (i + r) / i if i else 0.0
verdict = 'transfers' if ratio < 1.01 else ('convert' if ratio < 1.5 else 'DO NOT transfer')
print('%-14s instr=%-14d reps=%-14d ratio=%-7.3f pin_pred=%-14d intervals=%-5d %4ds rc=%s  %s'
      % (name, i, r, ratio, i + r, n, secs, rc, verdict))
EOF
