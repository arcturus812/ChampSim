#!/bin/bash

PIN_ROOT=/home/caslab/workspace/sim/pin-3.22-98547-g7a303a835-gcc-linux
TRACER=/home/caslab/workspace/sim/ChampSim/tracer/pin/obj-intel64/champsim_tracer.so

## option
# -o
# Specify the output file for your trace.
# The default is default_trace.champsim

# -s <number>
# Specify the number of instructions to skip in the program before tracing begins.
# The default value is 0.

# -t <number>
# The number of instructions to trace, after -s instructions have been skipped.
# The default value is 1,000,000.

$PIN_ROOT/pin -t $TRACER -- $1
