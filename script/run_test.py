#!/usr/bin/env python3
import os
import sys
import subprocess
from concurrent.futures import ThreadPoolExecutor

import exp_env
import common

# WARM_INST = 1000000 #1M
# SIM_INST = 5000000 #5M
# WARM_INST = 2000000 # 2M
# SIM_INST = 10000000 # 10M
WARM_INST = 20000000 # 20M
SIM_INST = 100000000 # 100M
# TEST_TRACE=exp_env.TRACE_ROOT + "/spec/410.bwaves-1963B.champsimtrace.xz"
# TEST_TRACE=exp_env.TRACE_ROOT + "/micro/sequential_1g_4iter.champsim"
TEST_TRACE=exp_env.TRACE_ROOT + "/micro/sequential_1g_4iter_app64.champsim"

def run_champsim_with_log(trace, warmup_inst, sim_inst, bin_suffix=""):
    bin_path=exp_env.SIM_BIN + bin_suffix
    base_name = os.path.basename(trace)
    trace_name = base_name.replace(".champsimtrace.xz", ".log")
    log_file_path = exp_env.LOG_ROOT + "/" + trace_name

    cmd = [
        bin_path,
        "--warmup-instructions", str(warmup_inst),
        "--simulation-instructions", str(sim_inst),
        trace,
        ">",
        log_file_path,
        "2>&1"
    ]
    try:
        subprocess.run(" ".join(cmd), shell=True, check=True)
    except subprocess.CalledProcessError as e:
        print(f"Error running {bin_path}: {e}")

def run_champsim_wo_log(trace, warmup_inst, sim_inst, bin_suffix=""):
    bin_path=exp_env.SIM_BIN + bin_suffix
    base_name = os.path.basename(trace)
    trace_name = base_name.replace(".champsimtrace.xz", ".log")
    log_file_path = exp_env.LOG_ROOT + "/" + trace_name

    cmd = [
        bin_path,
        "--warmup-instructions", str(warmup_inst),
        "--simulation-instructions", str(sim_inst),
        trace
    ]
    try:
        print(" ".join(cmd))
        subprocess.run(" ".join(cmd), shell=True, check=True)
    except subprocess.CalledProcessError as e:
        print(f"Error running {bin_path}: {e}")


def main():
    common.activate_venv()
    # 20M warmup, 100M sim
    # run_champsim_with_log(TEST_TRACE, WARM_INST, SIM_INST)
    # if there are arguments, run_champsim_wo_log with suffix arg
    if len(sys.argv) > 1:
        run_champsim_wo_log(TEST_TRACE, WARM_INST, SIM_INST, "_" + sys.argv[1])
    else:
        run_champsim_wo_log(TEST_TRACE, WARM_INST, SIM_INST, "")

if __name__ == '__main__':
    main()

