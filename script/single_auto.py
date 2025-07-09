#!/usr/bin/env python3
import os
import sys
import subprocess

from urllib3 import response
import pandas as pd
from concurrent.futures import ThreadPoolExecutor

import exp_env
import common

# Set pandas display options to show all rows and columns
pd.set_option('display.max_rows', None)
pd.set_option('display.max_columns', None)
pd.set_option('display.width', None)
pd.set_option('display.max_colwidth', None)

PARALLEL_LEVEL = 25

NUM_CORE = 1
WARM_INST = 20000000 # 20M
SIM_INST = 100000000 # 100M

TRACE_ROOT = exp_env.TRACE_ROOT

def make_command(traces, warmup_inst, sim_inst, bin_suffix="", log_name="", log_dir=""):
    bin_path=exp_env.SIM_BIN + bin_suffix
    log_file_path=""
    if log_dir == "":
        log_file_path = exp_env.LOG_ROOT + "/last_exp"
    else:
        log_file_path = exp_env.LOG_ROOT + "/" + log_dir

    if not os.path.exists(log_file_path):
        os.makedirs(log_file_path)

    if isinstance(traces, str):
        traces = [traces]
        log_file_path = log_file_path + "/" + os.path.basename(traces[0]).replace(".champsimtrace.xz", ".log")
    else:
        if log_name == "":
            raise ValueError("log_name must be specified")
        else:
            log_file_path = log_file_path + "/" + log_name

    cmd = [
        bin_path,
        "--warmup-instructions", str(warmup_inst),
        "--simulation-instructions", str(sim_inst)
    ]
    for trace in traces:
        cmd.append(trace)
    cmd.append(">")
    cmd.append(log_file_path)
    cmd.append("2>&1")
    return " ".join(cmd)

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

def main():
    # get all trace files in TRACE_ROOT
    spec_root = os.path.join(TRACE_ROOT, "spec")
    spec_trace_files = [os.path.join(spec_root, f) for f in os.listdir(spec_root) if f.endswith(".champsimtrace.xz")]

    # make dataframe workload_name, trace_file
    df = pd.DataFrame(columns=["workload_name", "trace_file"])
    for trace_file in spec_trace_files:
        new_row = pd.DataFrame([{
            "workload_name": trace_file.split("/")[-1].replace(".champsimtrace.xz", ""), 
            "trace_file": trace_file
        }])
        df = pd.concat([df, new_row], ignore_index=True)

    for _, row in df.iterrows():
        # cmd = make_command(row["trace_file"], WARM_INST, SIM_INST, bin_suffix="_dram_no_pf", log_dir="all_dram/no_no_no/C1_W20M_S100M") 
        # cmd = make_command(row["trace_file"], WARM_INST, SIM_INST, bin_suffix="_cxl_no_pf", log_dir="all_cxl/no_no_no/C1_W20M_S100M") 
        cmd = make_command(row["trace_file"], WARM_INST, SIM_INST, bin_suffix="_cxl_all_pf", log_dir="all_cxl/nextline_spp_stream/C1_W20M_S100M") 

        print(cmd)
        response = common.submit_command(cmd)
        print(response)

    # df = common.sort_by_execution_time(df)
    # run champsim with log for each trace file
    # with ThreadPoolExecutor(max_workers=PARALLEL_LEVEL) as executor:
    #     futures = [executor.submit(run_champsim_with_log, row["trace_file"], WARM_INST, SIM_INST) for _, row in df.iterrows()]
    #     for future in futures:
    #         future.result()

    


if __name__ == "__main__":
    main()