#!/usr/bin/env python3
import os
import sys
import subprocess
from concurrent.futures import ThreadPoolExecutor

import exp_env
import common

WARM_INST = 1000000
SIM_INST = 5000000
# TEST_TRACE=exp_env.TRACE_ROOT + "/spec/410.bwaves-1963B.champsimtrace.xz"
TEST_TRACE=exp_env.TRACE_ROOT + "/micro/sequential_1g_4iter.champsim"

GDB_PREFIX = "gdbserver :9091 "

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

def run_champsim_with_gdbserver(trace, warmup_inst, sim_inst, bin_suffix=""):
    bin_path=exp_env.SIM_BIN + bin_suffix
    base_name = os.path.basename(trace)
    trace_name = base_name.replace(".champsimtrace.xz", ".log")
    log_file_path = exp_env.LOG_ROOT + "/" + trace_name

    cmd = [
        GDB_PREFIX,
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
    run_champsim_with_gdbserver(TEST_TRACE, WARM_INST, SIM_INST)

if __name__ == '__main__':
    main()

