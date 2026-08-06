#!/bin/bash
# chain_mc.sh -- hand the machine from the single-core campaigns to the
# multi-core one without a human in the loop.
#
# Order matters: the multi-core build regenerates .csconfig, so it must not run
# while the single-core binary's campaigns are still in flight. This waits for
# both queues to report done, snapshots the single-core analysis while the data
# is fresh, then builds and launches.
#
# The core count comes from mc_cores.txt, written by whoever ran mc_pilot.sh;
# if it is not there yet we wait rather than guess, because the whole point of
# the pilot is that the number is measured.
set -u
cd "$(dirname "$0")"
HERE=$PWD
ROOT=$(cd ../.. && pwd)
LOG=$HERE/chain_mc.log
: > "$LOG"
say() { echo "[$(date -Is)] $*" >> "$LOG"; }

say "waiting for the single-core solution campaign"
until grep -qs RUN_DONE solution2.log solution.log; do sleep 300; done
say "solution campaign done"

# The auxiliary queue's phase B raced the main campaign's incremental budget
# file and ran only the traces that happened to be derived at that moment.
# Now that every budget exists, re-run it: the runner resumes, so the finished
# work is skipped and only the missing tight runs execute.
say "completing auxiliary phase B against the full budget set"
python3 run_aux.py run >> aux.log 2>&1 || say "aux rerun failed"
say "auxiliary campaign done"

say "snapshotting single-core results"
python3 analyze_solution.py all > single_core_results.txt 2>&1 || say "analyze failed"
python3 run_aux.py report > aux_results.txt 2>&1 || say "aux report failed"

say "waiting for the measured core count (mc_cores.txt)"
until [ -s mc_cores.txt ]; do sleep 300; done
N=$(tr -dc '0-9' < mc_cores.txt)
say "core count = $N"

CFG=$ROOT/cxl_repro_mc${N}_config.json
[ -f "$CFG" ] || { say "FATAL: $CFG missing"; exit 1; }

if [ ! -x "$ROOT/bin/champsim_cxl_mc${N}" ]; then
  say "building the ${N}-core binary"
  ( cd "$ROOT" && ./config.sh "$CFG" > /dev/null 2>&1 && make -j24 > /dev/null 2>&1 )
fi
[ -x "$ROOT/bin/champsim_cxl_mc${N}" ] || { say "FATAL: build failed"; exit 1; }
say "binary ready"

say "launching the multi-core campaign"
MC_CORES=$N python3 run_mc_solution.py run >> mc_solution.log 2>&1
say "multi-core campaign finished"
echo CHAIN_MC_DONE >> "$LOG"
