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
FEED_ROOT = "/mnt/nvme1/feed/champsim/spec"

TEST_TRACE=exp_env.TRACE_ROOT + "/spec/623.xalancbmk_s-592B.champsimtrace.xz"
TEST_WARM_INST = 200000 # 20M /100
TEST_SIM_INST = 1000000 # 100M /100


pf_setup="nextline_spp_stream"
exp_setup="C1_W20M_S100M"
feed_base_path = '/mnt/nvme1/feed/champsim/spec'
feed_path = os.path.join(feed_base_path, pf_setup)

def make_command_test(trace, warmup_inst, sim_inst, bin_suffix="", log_name="", log_dir=""):
    bin_path=exp_env.SIM_BIN + bin_suffix
    log_file_path=""
    if log_dir == "":
        log_file_path = exp_env.LOG_ROOT + "/last_exp"
    else:
        log_file_path = exp_env.LOG_ROOT + "/" + log_dir

    log_file_path = log_file_path + "/" + log_name + ".log"

    cmd = [
        bin_path,
        "--warmup-instructions", str(warmup_inst),
        "--simulation-instructions", str(sim_inst)
    ]
    cmd.append(trace)
    cmd.append(">")
    cmd.append(log_file_path)
    cmd.append("2>&1")
    return " ".join(cmd)

def make_command(traces, warmup_inst, sim_inst, bin_suffix="", log_name="", log_dir="", feed_path=""):
    # Get the first trace file name for feed file generation
    if isinstance(traces, str):
        trace_file = traces
    else:
        trace_file = traces[0]  # Use first trace if it's a list
    
    feed_file_name = os.path.basename(trace_file).replace(".champsimtrace.xz", ".csv")
    feed_file_path = ""
    if feed_path != "":
        feed_file_path = FEED_ROOT + "/" + pf_setup + "/" + feed_path + "/" + exp_setup + "/" + feed_file_name
        #check if feed_file_path exists
        if not os.path.exists(feed_file_path):
            print(f"Feed file not found: {feed_file_path}")
            return "false"

    bin_path=exp_env.SIM_BIN + bin_suffix
    # check bin is exist
    if not os.path.exists(bin_path):
        print(f"Bin not found: {bin_path}")
        return "false"

    log_file_path=""
    if log_dir == "":
        log_file_path = exp_env.LOG_ROOT + "/last_exp"
    else:
        log_file_path = exp_env.LOG_ROOT + "/" + log_dir

    if not os.path.exists(log_file_path):
        os.makedirs(log_file_path)

    if isinstance(traces, str):
        traces = [traces]
        if feed_path != "":
            # add feed_path to log_file_path
            log_file_path = log_file_path + "/" + feed_path
            if not os.path.exists(log_file_path):
                os.makedirs(log_file_path)
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
    if feed_path != "":
        cmd.append("--feeds")
        cmd.append(feed_file_path)
    cmd.append(">")
    cmd.append(log_file_path)
    cmd.append("2>&1")
    return " ".join(cmd)

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


    # cmd = make_command_test(TEST_TRACE, TEST_WARM_INST, TEST_SIM_INST, bin_suffix="_dram_all_pf", log_name="test", log_dir="") 
    # print(cmd)
    # response = common.submit_command(cmd)
    # print(response)
    # exit()

    cnt=0
    for _, row in df.iterrows():
        cmd = make_command(row["trace_file"], WARM_INST, SIM_INST, bin_suffix="_stat_all_pf_cxl", log_dir="all_cxl/nextline_spp_stream/C1_W20M_S100M", feed_path="") 
        # cmd = make_command(row["trace_file"], WARM_INST, SIM_INST, bin_suffix="_stat_all_pf_dram", log_dir="all_dram/nextline_spp_stream/C1_W20M_S100M", feed_path="") 
        # cmd = make_command(row["trace_file"], WARM_INST, SIM_INST, bin_suffix="_cxl_legacy_pf", log_dir="all_cxl/nextline_spp_stream/C1_W20M_S100M", feed_path="") 
        # cmd = make_command(row["trace_file"], WARM_INST, SIM_INST, bin_suffix="_cxl_sota_pf", log_dir="all_cxl/nextline_spp_stream/C1_W20M_S100M", feed_path="") 
        # cmd = make_command(row["trace_file"], WARM_INST, SIM_INST, bin_suffix="_stat_cxl_all_pf", log_dir="all_cxl/nextline_spp_stream/C1_W20M_S100M", feed_path="") 
        # cmd = make_command(row["trace_file"], WARM_INST, SIM_INST, bin_suffix="_stat_dram_all_pf", log_dir="all_dram/nextline_spp_stream/C1_W20M_S100M", feed_path="") 
        # for trigger in ["TEMP_LOCALITY", "SPATIAL_LOCALITY"]:
        #     for thd in [0.9, 0.8, 0.7, 0.6]:
        #         if trigger == "TEMP_LOCALITY":
        #             trig="thd"
        #             cmd = make_command(row["trace_file"], WARM_INST, SIM_INST, bin_suffix="_feedback_all_pf", log_dir="feedback/nextline_spp_stream/C1_W20M_S100M", feed_path=trigger+"_"+trig+str(thd)) 
        #         else:
        #             trig="percentile"
        #             cmd = make_command(row["trace_file"], WARM_INST, SIM_INST, bin_suffix="_feedback_all_pf", log_dir="feedback/nextline_spp_stream/C1_W20M_S100M", feed_path=trigger+"_"+trig+str(thd)) 
        #         if cmd != "false":
        #             response = common.submit_command(cmd)
        #             print(response)
        #             cnt+=1
        #         else:
        #             print(f"CMD ERROR: {cmd}")

        if cmd != "false":
            response = common.submit_command(cmd)
            print(response)
            cnt+=1
        else:
            print(f"CMD ERROR: {cmd}")

    print(f"Total commands: {cnt}")



    


if __name__ == "__main__":
    main()