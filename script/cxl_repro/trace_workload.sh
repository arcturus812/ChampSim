#!/bin/bash
# trace_workload.sh -- record a ChampSim trace from a real program with Intel Pin.
#
# Setup this depends on (done once, see mds/report_brief.md):
#   ~/workspace/CAL/tools/pin-3.22          Pin 3.22 kit, the version the bundled
#                                           tracer was tested against
#   tracer/pin/obj-intel64/champsim_tracer.so   built with PIN_ROOT set to the above
#
# Sizing rules that matter for this study, so they are defaults rather than advice:
#   * skip past initialization before recording. A program's setup phase is a huge
#     write-allocate burst that is not the steady state we are measuring, and it
#     would dominate a short trace.
#   * the working set must clear the MODELLED last-level cache (2 MiB here), not the
#     host's. Traces carry addresses only, so host cache size is irrelevant.
#   * do not worry about the compiler emitting non-temporal stores: GCC will not do
#     it unprompted, and even where glibc takes an NT path the recorded addresses are
#     identical, so the simulator still sees an ordinary full-line store stream.
#
# Usage: trace_workload.sh <name> [skip_instr] [trace_instr] -- <program> [args...]
#   name          output becomes traces/<name>.champsim.gz
#   skip_instr    instructions to skip first (default 200M, past most init phases)
#   trace_instr   instructions to record    (default 100M)
set -eu

HERE="$(cd "$(dirname "$0")" && pwd)"
export PIN_ROOT="${PIN_ROOT:-$HOME/workspace/CAL/tools/pin-3.22}"
TOOL="$HERE/../../tracer/pin/obj-intel64/champsim_tracer.so"
OUTDIR="$HERE/traces"

[ -x "$PIN_ROOT/pin" ] || { echo "no Pin at $PIN_ROOT -- set PIN_ROOT" >&2; exit 1; }
[ -f "$TOOL" ] || { echo "tracer not built: $TOOL" >&2; exit 1; }

NAME=${1:?usage: trace_workload.sh <name> [skip] [count] -- <program> [args...]}
shift
SKIP=200000000
COUNT=100000000
[ "${1:-}" != "--" ] && { SKIP=$1; shift; }
[ "${1:-}" != "--" ] && { COUNT=$1; shift; }
[ "${1:-}" = "--" ] && shift
[ $# -gt 0 ] || { echo "no program given" >&2; exit 1; }

mkdir -p "$OUTDIR"
RAW=$(mktemp "${TMPDIR:-/tmp}/champsim_trace.XXXXXX")
trap 'rm -f "$RAW"' EXIT

echo "[trace] $NAME: skip $SKIP, record $COUNT"
echo "[trace] program: $*"
START=$(date +%s)
"$PIN_ROOT/pin" -t "$TOOL" -o "$RAW" -s "$SKIP" -t "$COUNT" -- "$@" > /dev/null 2>&1 || {
  echo "[trace] WARNING: program exited non-zero; trace may be short" >&2
}
GOT=$(( $(stat -c %s "$RAW") / 64 ))
echo "[trace] recorded $GOT instructions in $(( $(date +%s) - START ))s"
[ "$GOT" -gt 0 ] || { echo "[trace] FATAL: empty trace -- did the program finish before $SKIP?" >&2; exit 1; }
if [ "$GOT" -lt "$COUNT" ]; then
  echo "[trace] NOTE: program ended early ($GOT < $COUNT); shorten -s or use a larger input" >&2
fi

gzip -1 -c "$RAW" > "$OUTDIR/$NAME.champsim.gz"
echo "[trace] wrote $OUTDIR/$NAME.champsim.gz ($(du -h "$OUTDIR/$NAME.champsim.gz" | cut -f1))"
