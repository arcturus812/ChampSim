#!/bin/bash
# trace_workload.sh -- record a ChampSim trace from a real program with Intel Pin.
#
# The trace is compressed as it is produced. Pin's tracer only ever opens, writes
# sequentially and closes its output, so it can write into a FIFO that gzip drains;
# nothing large ever lands on disk. This is not a micro-optimization: 100M
# instructions is 6.4 GB raw, and on 644_m5 /tmp has under 10 GB free.
#
# Where to trace, and why it is not obvious: a ChampSim trace records instruction
# pointers, branch outcomes, register ids and memory ADDRESSES -- no timing, no
# cache state, no access sizes. The host's cache hierarchy and memory system
# therefore do not appear in it at all; ChampSim supplies its own. What the host
# DOES decide is the instruction stream itself:
#   * ISA. Built with -march=native, an AVX-512 host emits one 64 B store where an
#     AVX2 host emits two 32 B stores. Our reference platform and our microbenchmark
#     are AVX-512 full-line stores, so prefer an AVX-512 host and pin the flags
#     explicitly (-march=skylake-avx512) rather than trusting native.
#   * glibc IFUNC. memset/memcpy pick their implementation from CPU features at
#     RUNTIME, so the same binary traces differently on different hosts. Prefer
#     explicit store loops over libc calls in kernels meant as clean anchors.
# Synthetic traces from streamgen have neither problem and can be built anywhere.
#
# Sizing:
#   * skip past initialization. A setup phase is itself a large write-allocate
#     burst and is not the steady state being measured.
#   * the working set must clear the MODELLED last-level cache (2 MiB), not the
#     host's -- traces carry addresses only.
#
# Usage: trace_workload.sh <name> [skip_instr] [trace_instr] -- <program> [args...]
#   TRACE_OUT=<dir>   where the .champsim.gz goes (default: this script's traces/)
#   PIN_ROOT=<dir>    Pin kit; searched in the usual places if unset
set -eu

HERE="$(cd "$(dirname "$0")" && pwd)"
TOOL="$HERE/../../tracer/pin/obj-intel64/champsim_tracer.so"
OUTDIR="${TRACE_OUT:-$HERE/traces}"

if [ -z "${PIN_ROOT:-}" ]; then
  for cand in "$HOME/workspace/tools/pin-3.22" "$HOME/workspace/CAL/tools/pin-3.22" \
              "$HOME/pin-3.22" /opt/pin-3.22; do
    [ -x "$cand/pin" ] && { PIN_ROOT=$cand; break; }
  done
fi
: "${PIN_ROOT:?no Pin kit found -- set PIN_ROOT}"
[ -x "$PIN_ROOT/pin" ] || { echo "no Pin at $PIN_ROOT" >&2; exit 1; }
[ -f "$TOOL" ] || { echo "tracer not built: $TOOL (PIN_ROOT=$PIN_ROOT make)" >&2; exit 1; }

NAME=${1:?usage: trace_workload.sh <name> [skip] [count] -- <program> [args...]}
shift
SKIP=200000000
COUNT=100000000
[ "${1:-}" != "--" ] && { SKIP=$1; shift; }
[ "${1:-}" != "--" ] && { COUNT=$1; shift; }
[ "${1:-}" = "--" ] && shift
[ $# -gt 0 ] || { echo "no program given" >&2; exit 1; }

mkdir -p "$OUTDIR"
OUT="$OUTDIR/$NAME.champsim.gz"
WORK=$(mktemp -d "$OUTDIR/.trace_$NAME.XXXXXX")
FIFO="$WORK/pipe"
CNT="$WORK/bytes"
mkfifo "$FIFO"
trap 'rm -rf "$WORK"' EXIT

echo "[trace] $NAME: skip $SKIP, record $COUNT  (pin: $PIN_ROOT)"
echo "[trace] program: $*"
START=$(date +%s)

# drain the FIFO into the compressed output, counting raw bytes on the way past
tee >(wc -c > "$CNT") < "$FIFO" | gzip -1 -c > "$OUT" &
DRAIN=$!

"$PIN_ROOT/pin" -t "$TOOL" -o "$FIFO" -s "$SKIP" -t "$COUNT" -- "$@" > /dev/null 2>&1 || {
  echo "[trace] WARNING: program exited non-zero; trace may be short" >&2
}
wait "$DRAIN"

GOT=$(( $(cat "$CNT") / 64 ))
echo "[trace] recorded $GOT instructions in $(( $(date +%s) - START ))s"
if [ "$GOT" -eq 0 ]; then
  echo "[trace] FATAL: empty trace -- did the program end before $SKIP instructions?" >&2
  rm -f "$OUT"
  exit 1
fi
[ "$GOT" -lt "$COUNT" ] && \
  echo "[trace] NOTE: ended early ($GOT < $COUNT); lower -s or use a larger input" >&2
echo "[trace] wrote $OUT ($(du -h "$OUT" | cut -f1))"
