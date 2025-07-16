import pandas as pd
import os
import glob


def get_ipc_cycles_simultime_from_log(log_path):
    # find line start with "Simulation complete"
    with open(log_path, 'r') as f:
        for line in f:
            if line.startswith('Simulation complete'):
                # example line="Simulation complete CPU 0 instructions: 100000003 cycles: 58940373 cumulative IPC: 1.697 (Simulation time: 00 hr 15 min 06 sec)"
                cycles = int(line.split(' ')[7])
                IPC = float(line.split(' ')[10])
                # simulation time located after "Simulation time:"
                simultime = line.split('Simulation time:')[1]
                h = int(simultime.split(' ')[1])
                m = int(simultime.split(' ')[3])
                s = int(simultime.split(' ')[5])
                simultime = h * 3600 + m * 60 + s
                return IPC, cycles, simultime
    return None, None, None

def get_MPKI_from_log(log_path):
    with open(log_path, 'r') as f:
        for line in f:
            if line.startswith('Simulation complete'):
                # example line="Simulation complete CPU 0 instructions: 100000003 cycles: 58940373 cumulative IPC: 1.697 (Simulation time: 00 hr 15 min 06 sec)"
                # get instructions from line
                instructions = int(line.split(' ')[5])
                ki = instructions / 1000
                continue
            if 'LLC TOTAL' in line:
                # cpu0->LLC TOTAL        ACCESS:    2144678 HIT:    1936770 MISS:     207908 MSHR_MERGE:     109454
                # multiple spaces to one space
                line = ' '.join(line.split())
                split_line = line.split(' ')
                miss = int(split_line[7])
                return miss / ki
    return None

def get_avg_timeliness_from_per_page_data(per_page_data_path):
    predefined_header="pfn,vfn,cpu,l1d_hit,l1d_miss,l1d_prefetch,l1d_useful_prefetch_hit,l1d_mshr_pf_hit,l1d_mshr_pf_hit_cycle,l1d_mshr_pf_hit_delay_cycle,l1d_pf_degree_sum,l1d_pf_degree_cnt,l1d_useless_prefetch,l1d_miss_handle_cycle,l1d_miss_handle_cnt,l2c_hit,l2c_miss,l2c_prefetch,l2c_useful_prefetch_hit,l2c_mshr_pf_hit,l2c_mshr_pf_hit_cycle,l2c_mshr_pf_hit_delay_cycle,l2c_pf_degree_sum,l2c_pf_degree_cnt,l2c_useless_prefetch,l2c_miss_handle_cycle,l2c_miss_handle_cnt,llc_hit,llc_miss,llc_prefetch,llc_useful_prefetch_hit,llc_mshr_pf_hit,llc_mshr_pf_hit_cycle,llc_mshr_pf_hit_delay_cycle,llc_pf_degree_sum,llc_pf_degree_cnt,llc_useless_prefetch,llc_miss_handle_cycle,llc_miss_handle_cnt"
    df = pd.read_csv(per_page_data_path)

    # check if the header is the same as predefined_header
    if df.columns.tolist() != predefined_header.split(','):
        print(f"Header mismatch in {per_page_data_path}")
        return None, None

    df['pf_mshr_hit'] = df['l1d_mshr_pf_hit'] + df['l2c_mshr_pf_hit'] + df['llc_mshr_pf_hit']
    df['pf_mshr_arrival_delay_sum'] = df['l1d_mshr_pf_hit_cycle'] + df['l2c_mshr_pf_hit_cycle'] + df['llc_mshr_pf_hit_cycle']
    df['pf_mshr_arrival_delay_avg'] = df['pf_mshr_arrival_delay_sum'] / df['pf_mshr_hit']

    df['pf_mshr_retrive_delay_sum'] = df['l1d_mshr_pf_hit_delay_cycle'] + df['l2c_mshr_pf_hit_delay_cycle'] + df['llc_mshr_pf_hit_delay_cycle']
    df['pf_mshr_retrive_delay_avg'] = df['pf_mshr_retrive_delay_sum'] / df['pf_mshr_hit']

    return df['pf_mshr_arrival_delay_avg'].mean(), df['pf_mshr_retrive_delay_avg'].mean()