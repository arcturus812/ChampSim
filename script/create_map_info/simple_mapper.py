import pandas as pd
import glob
import os
from enum import Enum

pf_setup="nextline_spp_stream"
exp_setup="C1_W20M_S100M"

per_page_info_base_path = '/mnt/nvme1/data/ChampSim/per_page_data'
feed_base_path = '/mnt/nvme1/feed/champsim/spec'

dram_path = os.path.join(per_page_info_base_path, 'all_dram', pf_setup, exp_setup)
cxl_path = os.path.join(per_page_info_base_path, 'all_cxl', pf_setup, exp_setup)
feed_path = os.path.join(feed_base_path, pf_setup)

# available column from per_page_info
# pfn,vfn,cpu,l1d_hit,l1d_miss,l1d_prefetch,l1d_useful_prefetch_hit,l1d_mshr_pf_hit,l1d_pf_degree_sum,l1d_pf_degree_cnt,l1d_useless_prefetch,l1d_mshr_prefetch_hit,l1d_miss_handle_cycle,l1d_miss_handle_cnt,l2c_hit,l2c_miss,l2c_prefetch,l2c_useful_prefetch_hit,l2c_mshr_pf_hit,l2c_pf_degree_sum,l2c_pf_degree_cnt,l2c_useless_prefetch,l2c_mshr_prefetch_hit,l2c_miss_handle_cycle,l2c_miss_handle_cnt,llc_hit,llc_miss,llc_prefetch,llc_useful_prefetch_hit,llc_mshr_pf_hit,llc_pf_degree_sum,llc_pf_degree_cnt,llc_useless_prefetch,llc_mshr_prefetch_hit,llc_miss_handle_cycle,llc_miss_handle_cnt


# MAP_INFO format
# vfn(int), far(bool)
FINAL_COLUMN=['vfn', 'far']

# enum alloc far_mem triggers (TEMP_LOCALITY, SPATIAL_LOCALITY, PF_TIMELINESS, LOW_TEMP_LOCALITY_HIGH_SPATIAL_LOCALITY)
class AllocFarMemTrigger(Enum):
    TEMP_LOCALITY = 0
    SPATIAL_LOCALITY = 1
    LOW_TEMP_LOCALITY_HIGH_SPATIAL_LOCALITY = 2
    PF_TIMELINESS = 3
    ALL_TRIGGERS = 4

def LOW_TEMP_LOCALITY_HIGH_SPATIAL_LOCALITY(df, threshold_temp, percentile_spatial):
    far_mapped_df = pd.DataFrame()
    df['temporal_locality'] = (df['l1d_hit'] + df['l2c_hit'] + df['llc_hit']) / (df['l1d_hit'] + df['l2c_hit'] + df['llc_hit'] + df['llc_miss'])
    # df['spatial_locality'] = (df['l1d_useful_prefetch_hit'] + df['l2c_useful_prefetch_hit'] + df['llc_useful_prefetch_hit']) / (df['l1d_hit'] + df['l2c_hit'] + df['llc_hit'] + df['llc_miss'])
    df['pf_useful_hit'] = df['l1d_useful_prefetch_hit'] + df['l2c_useful_prefetch_hit'] + df['llc_useful_prefetch_hit']
    min_pf_useful_hit = df['pf_useful_hit'].min()
    max_pf_useful_hit = df['pf_useful_hit'].max()
    threshold_spatial = min_pf_useful_hit + (max_pf_useful_hit - min_pf_useful_hit) * percentile_spatial

    df = df.sort_values(by='temporal_locality', ascending=False)
    far_memory_df = df[df['temporal_locality'] < threshold_temp].copy()
    far_memory_df = far_memory_df[far_memory_df['pf_useful_hit'] >= threshold_spatial].copy()
    if far_memory_df.empty:
        return pd.DataFrame(columns=FINAL_COLUMN)
    far_memory_df.loc[:, 'far'] = 1
    far_memory_df = far_memory_df.reset_index()  # Reset index to include vfn as column
    far_memory_df = far_memory_df[FINAL_COLUMN]
    return far_memory_df

def SPATIAL_LOCALITY(df, percentile):
    far_mapped_df = pd.DataFrame()
    df['pf_useful_hit'] = df['l1d_useful_prefetch_hit'] + df['l2c_useful_prefetch_hit'] + df['llc_useful_prefetch_hit']
    # df['access'] = df['l1d_hit'] + df['l2c_hit'] + df['llc_hit'] + df['llc_miss']
    # df['pf_coverage'] = df['pf_useful_hit'] / df['access']
    df = df.sort_values(by='pf_useful_hit', ascending=False)
    min_pf_useful_hit = df['pf_useful_hit'].min()
    max_pf_useful_hit = df['pf_useful_hit'].max()
    threshold = min_pf_useful_hit + (max_pf_useful_hit - min_pf_useful_hit) * percentile
    far_memory_df = df[df['pf_useful_hit'] >= threshold].copy()
    far_memory_df.loc[:, 'far'] = 1
    far_memory_df = far_memory_df.reset_index()  # Reset index to include vfn as column
    far_memory_df = far_memory_df[FINAL_COLUMN]
    return far_memory_df

def TEMP_LOCALITY(df, threshold):
    far_mapped_df = pd.DataFrame()
    df['temporal_locality'] = (df['l1d_hit'] + df['l2c_hit'] + df['llc_hit']) / (df['l1d_hit'] + df['l2c_hit'] + df['llc_hit'] + df['llc_miss'])
    df = df.sort_values(by='temporal_locality', ascending=False)
    # min_temporal_locality = df['temporal_locality'].min()
    # max_temporal_locality = df['temporal_locality'].max()

    # remain only high enough temporal locality
    # threshold = min_temporal_locality + (max_temporal_locality - min_temporal_locality) * percentile
    # print min/max
    far_memory_df = df[df['temporal_locality'] >= threshold].copy()
    far_memory_df.loc[:, 'far'] = 1
    far_memory_df = far_memory_df.reset_index()  # Reset index to include vfn as column
    far_memory_df = far_memory_df[FINAL_COLUMN]
    return far_memory_df

def save_df_as_map_file(df, trigger, workkload_name, trigger_suffix=""):
    output_file_path = os.path.join(feed_path, trigger.name+trigger_suffix, exp_setup, f"{workkload_name}.csv")
    os.makedirs(os.path.dirname(output_file_path), exist_ok=True)
    print(output_file_path)
    df.to_csv(output_file_path, index=False)

def create_map_info(dram_df, cxl_df, trigger, workkload_name):
    if trigger == AllocFarMemTrigger.TEMP_LOCALITY:
        TEMP_LOCALITY_THD=[0.9, 0.8, 0.7, 0.6]
        len_before_locality = len(dram_df)
        for thd in TEMP_LOCALITY_THD:
            ret_df = TEMP_LOCALITY(dram_df, thd)
            len_after_locality = len(ret_df)
            if ret_df.empty:
                print(f"[{workkload_name}] {trigger.name} {thd} empty!")
                continue
            else:
                print(f"[{workkload_name}] {trigger.name} {thd} {len_before_locality} {len_after_locality} {len_after_locality/len_before_locality*100:.2f}%")
            save_df_as_map_file(ret_df, trigger, workkload_name, f"_thd{thd}")
            del ret_df
    elif trigger == AllocFarMemTrigger.SPATIAL_LOCALITY:
        SPATIAL_LOCALITY_PERCENTILE=[0.9, 0.8, 0.7, 0.6]
        len_before_pf_hit_rate = len(dram_df)
        for percentile in SPATIAL_LOCALITY_PERCENTILE:
            ret_df = SPATIAL_LOCALITY(dram_df, percentile)
            len_after_pf_hit_rate = len(ret_df)
            if ret_df.empty:
                print(f"[{workkload_name}] {trigger.name} {percentile} empty!")
                continue
            else:
                print(f"[{workkload_name}] {trigger.name} {percentile} {len_before_pf_hit_rate} {len_after_pf_hit_rate} {len_after_pf_hit_rate/len_before_pf_hit_rate*100:.2f}%")
            save_df_as_map_file(ret_df, trigger, workkload_name, f"_percentile{percentile}")
            del ret_df
    elif trigger == AllocFarMemTrigger.LOW_TEMP_LOCALITY_HIGH_SPATIAL_LOCALITY:
        TEMP_LOCALITY_THD=[0.9, 0.8, 0.7]
        SPATIAL_LOCALITY_PERCENTILE=[0.9, 0.8, 0.7, 0.6]
        for thd_temp in TEMP_LOCALITY_THD:
            for percentile in SPATIAL_LOCALITY_PERCENTILE:
                len_before_locality = len(dram_df)
                ret_df = LOW_TEMP_LOCALITY_HIGH_SPATIAL_LOCALITY(dram_df, thd_temp, percentile)
                len_after_locality = len(ret_df)
                print(f"[{workkload_name}] {trigger.name} {thd_temp} {percentile} {len_before_locality} {len_after_locality} {len_after_locality/len_before_locality*100:.2f}%")
                save_df_as_map_file(ret_df, trigger, workkload_name, f"_thd_temp{thd_temp}_percentile{percentile}")
                del ret_df
    elif trigger == AllocFarMemTrigger.PF_TIMELINESS: # this will use dram df and cxl df both
        pass
    else:
        raise ValueError(f"Invalid trigger: {trigger}")

def main():
    base_files = glob.glob(os.path.join(dram_path, '*.csv')) # for now, dram is base of map_info
    print("Select trigger:")
    print("0. TEMP_LOCALITY")
    print("1. SPATIAL_LOCALITY")
    print("2. LOW_TEMP_LOCALITY_HIGH_SPATIAL_LOCALITY")
    print("3. PF_TIMELINESS")
    print("4. ALL_TRIGGERS")
    trigger = input("Enter the number of the trigger: ")
    trigger = AllocFarMemTrigger(int(trigger))

    for dram_file in base_files:
        cxl_file = dram_file.replace('all_dram', 'all_cxl')
        # check if cxl_file exist
        if not os.path.exists(cxl_file):
            print(f"[{workload_name}] CXL file not found")
            continue
        dram_df = pd.read_csv(dram_file)
        cxl_df = pd.read_csv(cxl_file)
        # set vfn is index
        dram_df = dram_df.set_index('vfn')
        cxl_df = cxl_df.set_index('vfn')
        # workload name is until meet .csv
        workload_name = os.path.basename(dram_file).replace('.csv', '')
        if trigger == AllocFarMemTrigger.ALL_TRIGGERS:
            for trigger in AllocFarMemTrigger:
                create_map_info(dram_df, cxl_df, trigger, workload_name)
        else:
            create_map_info(dram_df, cxl_df, trigger, workload_name)
        del dram_df
        del cxl_df

if __name__ == "__main__":
    main()