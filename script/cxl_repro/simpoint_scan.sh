#!/bin/bash
# simpoint_scan.sh -- find which slices of a program are worth tracing.
#
# Pipeline: valgrind exp-bbv records a basic-block vector per interval, SimPoint
# clusters those vectors and picks one representative interval per cluster with a
# weight, and this script converts the chosen interval indices into the instruction
# skip counts trace_workload.sh wants.
#
# Two warnings that matter more here than in a normal SimPoint study.
#
# 1. Instruction counts do not transfer exactly. Valgrind counts what it
#    instruments and Pin counts what it instruments, and they disagree -- notably
#    on rep-prefixed string operations, where one instruction can be millions of
#    iterations of work. A memset-heavy program can therefore show far fewer
#    "instructions" than its memory traffic suggests. Treat a skip point from here
#    as approximate, and confirm the traced slice by its measured behaviour, not by
#    its instruction number.
#
# 2. SimPoint clusters on WHICH CODE RUNS, which is a proxy for behaviour, not the
#    behaviour we care about. Two intervals executing the same basic blocks can have
#    completely different write-allocate character once the working set outgrows the
#    cache. So use this to enumerate candidate slices, then verify each one: trace
#    it, run it through the simulator, and check the write-allocate share matches
#    what perf measured on the real machine over the same region. A slice that does
#    not reproduce it is not representative, whatever its BBV said.
#
# Usage: simpoint_scan.sh <name> [interval_size] [maxK] -- <program> [args...]
#   interval_size   instructions per interval (default 100M, the SimPoint convention)
#   maxK            largest number of clusters to consider (default 8)
set -eu

HERE="$(cd "$(dirname "$0")" && pwd)"
SIMPOINT="${SIMPOINT_BIN:-$HOME/workspace/tools/SimPoint.3.2/bin/simpoint}"
OUTDIR="${SCAN_OUT:-$HERE/simpoint}"

command -v valgrind > /dev/null || { echo "valgrind not installed" >&2; exit 1; }
[ -x "$SIMPOINT" ] || { echo "no simpoint at $SIMPOINT" >&2; exit 1; }

NAME=${1:?usage: simpoint_scan.sh <name> [interval] [maxK] -- <program> [args...]}
shift
INTERVAL=100000000
MAXK=8
[ "${1:-}" != "--" ] && { INTERVAL=$1; shift; }
[ "${1:-}" != "--" ] && { MAXK=$1; shift; }
[ "${1:-}" = "--" ] && shift
[ $# -gt 0 ] || { echo "no program given" >&2; exit 1; }

mkdir -p "$OUTDIR"
BBV="$OUTDIR/$NAME.bb"
SP="$OUTDIR/$NAME.simpoints"
WT="$OUTDIR/$NAME.weights"

echo "[scan] $NAME: interval $INTERVAL instructions, maxK $MAXK"
echo "[scan] program: $*"

START=$(date +%s)
valgrind --tool=exp-bbv --interval-size="$INTERVAL" --bb-out-file="$BBV" -- "$@" \
  > "$OUTDIR/$NAME.stdout" 2>&1 || echo "[scan] WARNING: program exited non-zero" >&2
NINT=$(grep -c '^T' "$BBV" || true)
TOTAL=$(grep -oE 'Total instructions: [0-9]+' "$BBV" | head -1 | grep -oE '[0-9]+' || echo 0)
echo "[scan] $NINT intervals, $TOTAL instructions, $(( $(date +%s) - START ))s"

if [ "$NINT" -lt 2 ]; then
  echo "[scan] too few intervals to cluster -- lower the interval size or lengthen the run" >&2
  exit 1
fi

"$SIMPOINT" -loadFVFile "$BBV" -maxK "$MAXK" \
  -saveSimpoints "$SP" -saveSimpointWeights "$WT" > "$OUTDIR/$NAME.simpoint.log" 2>&1

echo
echo "[scan] representative slices (skip = interval x $INTERVAL):"
printf '  %-10s %-12s %-8s %s\n' cluster interval weight "skip for trace_workload.sh"
paste <(sort -k2 -n "$SP") <(sort -k2 -n "$WT") | while read -r iv c1 w c2; do
  printf '  %-10s %-12s %-8s %s\n' "$c1" "$iv" "$w" "$(( iv * INTERVAL ))"
done
echo
echo "[scan] files: $BBV  $SP  $WT"
