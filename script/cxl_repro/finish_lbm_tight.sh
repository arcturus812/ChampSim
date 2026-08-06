#!/bin/bash
# finish_lbm_tight.sh -- complete lbm's budget-sensitivity triple once the machine frees up.
#
# Phase B asks whether the elide gain is an artifact of the 40%-of-baseline budget the
# main campaign uses, by adding a 20% point. Of the four traces it covers only lbm can
# answer it: bc0 (2% write-allocate), mcf (0.1%) and cactu (12%) have little or nothing
# to eliminate, so their curves are flat at zero by construction. lbm is 43%.
#
# lbm_elide_tight already completed; this fills in allocate and nt so the three policies
# are comparable at OFF / 40% / 20%.
#
# No timeout anywhere: a 20% budget throttles the modelled machine to a fifth of its
# unconstrained rate, so the same 50M instructions cost roughly five times the simulation
# time of the 40% runs. That is expected, not a hang.
set -u
cd "$(dirname "$0")"
LOG=finish_lbm_tight.log
: > "$LOG"
say() { echo "[$(date -Is)] $*" | tee -a "$LOG"; }

BIN=../../bin/champsim_cxl_repro
TR=/home/hwpark/workspace/storage/trace/champsim/spec17/619.lbm-s0.trace.xz
PERIOD=134214          # 20% of lbm's unconstrained serviced-line rate
WARMUP=10000000
SIM=50000000

say "waiting for the six in-flight simulations to finish"
while [ "$(pgrep -c -f 'champsim_cxl_repro --warmup' || true)" -gt 0 ]; do sleep 300; done
say "machine is free"

for pol in allocate nt; do
  OUT=out_aux/lbm_${pol}_tight.txt
  if grep -q "ChampSim completed all CPUs" "$OUT" 2>/dev/null; then
    say "lbm_${pol}_tight already complete, skipping"
    continue
  fi
  say "starting lbm_${pol}_tight (period ${PERIOD} ps)"
  env CXL_STORE_POLICY=$pol CXL_ALLOC_POLICY=only_far CXL_LIVELOCK_IPC=0.0001 \
      CXL_TX_PERIOD_PS=$PERIOD \
      $BIN --warmup-instructions $WARMUP --simulation-instructions $SIM "$TR" \
      > "$OUT" 2>&1 &
done
wait

for pol in allocate nt elide; do
  OUT=out_aux/lbm_${pol}_tight.txt
  if grep -q "ChampSim completed all CPUs" "$OUT" 2>/dev/null; then
    say "lbm_${pol}_tight: $(grep -oE 'cumulative IPC: [0-9.]+' "$OUT" | tail -1)"
  else
    say "lbm_${pol}_tight: DID NOT COMPLETE"
  fi
done
say "LBM_TIGHT_DONE"
