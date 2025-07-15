#!/usr/bin/env python3
import os
import sys
import subprocess
from concurrent.futures import ThreadPoolExecutor

import exp_env
import common

WARM_INST = 1000000 #1M
# SIM_INST = 5000000 #5M
SIM_INST = 1000000 #1M
# WARM_INST = 2000000 # 2M
#  SIM_INST = 10000000 # 10M
# WARM_INST = 20000000 # 20M
# SIM_INST = 100000000 # 100M
# TEST_TRACE=exp_env.TRACE_ROOT + "/spec/410.bwaves-1963B.champsimtrace.xz"
# TEST_TRACE=exp_env.TRACE_ROOT + "/micro/sequential_1g_4iter.champsim"
TEST_TRACE=exp_env.TRACE_ROOT + "/micro/sequential_1g_4iter_app64_16B.champsim"
TEST_FEED=exp_env.FEED_ROOT + "/micro/sequential_1g_4iter_app64_16B.csv"
NUM_CORE = 1  # Number of cores

def run_champsim_with_log(trace, warmup_inst, sim_inst, bin_suffix=""):
    bin_path=exp_env.SIM_BIN + bin_suffix
    base_name = os.path.basename(trace)
    trace_name = base_name.replace(".champsimtrace.xz", ".log")
    log_file_path = exp_env.LOG_ROOT + "/" + trace_name

    # Duplicate trace file for each core
    trace_files = [trace] * NUM_CORE

    cmd = [
        bin_path,
        "--warmup-instructions", str(warmup_inst),
        "--simulation-instructions", str(sim_inst)
    ] + trace_files + [
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

    # Duplicate trace file for each core
    trace_files = [trace] * NUM_CORE

    cmd = [
        bin_path,
        "--warmup-instructions", str(warmup_inst),
        "--simulation-instructions", str(sim_inst)
    ] + trace_files
    try:
        print(" ".join(cmd))
        subprocess.run(" ".join(cmd), shell=True, check=True)
    except subprocess.CalledProcessError as e:
        print(f"Error running {bin_path}: {e}")

def run_champsim_with_feed(trace, warmup_inst, sim_inst, bin_suffix=""):
    bin_path=exp_env.SIM_BIN + bin_suffix
    base_name = os.path.basename(trace)
    trace_name = base_name.replace(".champsimtrace.xz", ".log")
    log_file_path = exp_env.LOG_ROOT + "/" + trace_name

    trace_files = [trace] * NUM_CORE

    cmd = [
        bin_path,
        "--warmup-instructions", str(warmup_inst),
        "--simulation-instructions", str(sim_inst),
    ] + trace_files
    cmd += ["--feeds", TEST_FEED]
    try: 
        print(" ".join(cmd))
        subprocess.run(" ".join(cmd), shell=True, check=True)
    except subprocess.CalledProcessError as e:
        print(f"Error running {bin_path}: {e}")

def main():
    # Check if virtual environment is active
    if not common.is_venv_active():
        print("Warning: Virtual environment is not active.")
        print("Please activate the virtual environment before running this script:")
        print(f"source {exp_env.VENV_PATH}/bin/activate")
        print("Then run the script again.")
        sys.exit(1)
    
    # 20M warmup, 100M sim
    # run_champsim_with_log(TEST_TRACE, WARM_INST, SIM_INST)
    # if there are arguments, run_champsim_wo_log with suffix arg
    if len(sys.argv) > 1:
        # run_champsim_with_feed(TEST_TRACE, WARM_INST, SIM_INST, "_" + sys.argv[1])
        run_champsim_wo_log(TEST_TRACE, WARM_INST, SIM_INST, "_" + sys.argv[1])
    else:
        # run_champsim_with_feed(TEST_TRACE, WARM_INST, SIM_INST, "")
        run_champsim_wo_log(TEST_TRACE, WARM_INST, SIM_INST, "")

if __name__ == '__main__':
    main()

