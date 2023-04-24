#!/bin/bash
CHAMP_HOME=/home/caslab/workspace/sim/ChampSim
TRACE_HOME=/home/caslab/workspace/sim/traces/speccpu_by_champsim
INST_WARMUP=$1
INST_SIMUL=$2
TRACE=$3

echo "$CHAMP_HOME/bin/champsim --warmup_instructions $INST_WARMUP --simulation_instructions $INST_SIMUL/ $TRACE_HOME$TRACE"
echo "start simulation for $TRACE"
$CHAMP_HOME/bin/champsim --warmup_instructions $INST_WARMUP --simulation_instructions $INST_SIMUL $TRACE_HOME/$TRACE
