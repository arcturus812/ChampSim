#!/bin/bash
# mc_pilot.sh -- choose the core count for the multi-core campaign.
#
# The criterion is fixed BEFORE looking at any elide result: pick the smallest
# core count at which the memory-intensive workloads actually reach the
# device-derived transaction budget, because that is the regime the real
# machine is in and the only regime in which the mechanism can be evaluated at
# all. We do not sweep the core count against the elide gain and keep the best.
#
# Budget being targeted (see mc_budget note): 8.33e7 tx/s = 12000 ps period,
# derived by preserving the device's ratio of transaction capacity to aggregate
# byte capacity (4.0e8 / ((18.4+12.3)e9/64) = 0.833) on this model's
# 6.4 GB/s aggregate far link (0.833 x 1.0e8 lines/s).
#
# Runs with the budget OFF and reports the achieved serviced-line rate: the
# budget binds for a workload iff its unconstrained demand exceeds 8.33e7.
set -u
cd "$(dirname "$0")/../.."
ROOT=$PWD
TR=/mnt/ssd0/traces/champsim
OUT=$ROOT/script/cxl_repro/out_mcpilot
mkdir -p "$OUT"

WARM=2000000
SIM=5000000
TRACES="lbm:spec17/619.lbm-s0.trace.xz bc0:gapbs/bc-0.trace.gz mcf:spec17/605.mcf-s0.trace.xz"

for N in 8 16; do
  [ -x "bin/champsim_cxl_mc$N" ] && continue
  echo "== building $N-core binary"
  ./config.sh "cxl_repro_mc${N}_config.json" > /dev/null 2>&1
  make -j24 > /dev/null 2>&1
done

for N in 4 8 16; do
  BIN=$ROOT/bin/champsim_cxl_mc$N
  [ -x "$BIN" ] || { echo "missing $BIN"; continue; }
  for entry in $TRACES; do
    name=${entry%%:*}; rel=${entry#*:}
    out=$OUT/${name}_n${N}.txt
    [ -s "$out" ] && continue
    args=""; for i in $(seq 1 "$N"); do args="$args $TR/$rel"; done
    ( env CXL_ALLOC_POLICY=only_far CXL_STORE_POLICY=allocate CXL_LIVELOCK_IPC=0.0001 \
        "$BIN" --warmup-instructions $WARM --simulation-instructions $SIM $args \
        > "$out" 2>&1 ; echo "pilot $name N=$N rc=$?" ) &
  done
done
wait
echo "MC_PILOT_DONE"
