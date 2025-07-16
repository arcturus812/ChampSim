import pandas as pd
import os
import glob
import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from common import get_ipc_cycles_simultime_from_log

pf_setting = 'nextline_spp_stream'
sim_setting = 'C1_W20M_S100M'

per_page_data_path = os.path.expanduser('/home/hwpark/workspace/storage/data/ChampSim/per_page_data')
log_path = '/home/hwpark/workspace/storage/log/ChampSim'

dram_path = f'all_dram/{pf_setting}/{sim_setting}'
cxl_path = f'all_cxl/{pf_setting}/{sim_setting}'

# Expected CSV header for per_page_data
predefined_header = "pfn,vfn,cpu,l1d_hit,l1d_miss,l1d_prefetch,l1d_useful_prefetch_hit,l1d_mshr_pf_hit,l1d_mshr_pf_hit_cycle,l1d_mshr_pf_hit_delay_cycle,l1d_pf_degree_sum,l1d_pf_degree_cnt,l1d_useless_prefetch,l1d_miss_handle_cycle,l1d_miss_handle_cnt,l2c_hit,l2c_miss,l2c_prefetch,l2c_useful_prefetch_hit,l2c_mshr_pf_hit,l2c_mshr_pf_hit_cycle,l2c_mshr_pf_hit_delay_cycle,l2c_pf_degree_sum,l2c_pf_degree_cnt,l2c_useless_prefetch,l2c_miss_handle_cycle,l2c_miss_handle_cnt,llc_hit,llc_miss,llc_prefetch,llc_useful_prefetch_hit,llc_mshr_pf_hit,llc_mshr_pf_hit_cycle,llc_mshr_pf_hit_delay_cycle,llc_pf_degree_sum,llc_pf_degree_cnt,llc_useless_prefetch,llc_miss_handle_cycle,llc_miss_handle_cnt"

def get_metrics_from_per_page_data(per_page_data_path):
    """
    Extract metrics from per_page_data CSV file
    Returns: llc_miss, useful_prefetch_hit, mshr_pf_hit
    """
    if not os.path.exists(per_page_data_path):
        return None, None, None
    
    try:
        df = pd.read_csv(per_page_data_path)
        
        # Check if the header matches expected format
        if df.columns.tolist() != predefined_header.split(','):
            print(f"Header mismatch in {per_page_data_path}")
            return None, None, None
        
        # Calculate metrics by summing across all cache layers
        llc_miss = df['llc_miss'].sum()
        useful_prefetch_hit = (df['l1d_useful_prefetch_hit'] + 
                              df['l2c_useful_prefetch_hit'] + 
                              df['llc_useful_prefetch_hit']).sum()
        mshr_pf_hit = (df['l1d_mshr_pf_hit'] + 
                      df['l2c_mshr_pf_hit'] + 
                      df['llc_mshr_pf_hit']).sum()
        
        return llc_miss, useful_prefetch_hit, mshr_pf_hit
        
    except Exception as e:
        print(f"Error processing {per_page_data_path}: {e}")
        return None, None, None

def main():
    # Create DataFrame with required columns
    metrics_df = pd.DataFrame(columns=[
        'workload_name', 
        'dram_ipc', 'dram_llc_miss', 'dram_useful_prefetch_hit', 'dram_mshr_pf_hit',
        'cxl_ipc', 'cxl_llc_miss', 'cxl_useful_prefetch_hit', 'cxl_mshr_pf_hit'
    ])
    
    # Get all DRAM log files
    dram_logs = glob.glob(os.path.join(log_path, dram_path, '*.log'))
    
    for dram_log in dram_logs:
        workload_name = os.path.basename(dram_log).replace('.log', '')
        
        # Get DRAM IPC
        dram_ipc, _, _ = get_ipc_cycles_simultime_from_log(dram_log)
        if dram_ipc is None:
            print(f"Failed to get IPC from {dram_log}")
            continue
        
        # Check if corresponding CXL log exists
        cxl_log = dram_log.replace(dram_path, cxl_path)
        if not os.path.exists(cxl_log):
            print(f"Corresponding CXL log not found: {cxl_log}")
            continue
        
        # Get CXL IPC
        cxl_ipc, _, _ = get_ipc_cycles_simultime_from_log(cxl_log)
        if cxl_ipc is None:
            print(f"Failed to get IPC from {cxl_log}")
            continue
        
        # Get DRAM metrics from per_page_data
        dram_per_page_data_path = os.path.join(per_page_data_path, 'all_dram', pf_setting, sim_setting, f'{workload_name}.csv')
        dram_llc_miss, dram_useful_prefetch_hit, dram_mshr_pf_hit = get_metrics_from_per_page_data(dram_per_page_data_path)
        
        if dram_llc_miss is None:
            print(f"Failed to get DRAM metrics from {dram_per_page_data_path}")
            continue
        
        # Get CXL metrics from per_page_data
        cxl_per_page_data_path = os.path.join(per_page_data_path, 'all_cxl', pf_setting, sim_setting, f'{workload_name}.csv')
        cxl_llc_miss, cxl_useful_prefetch_hit, cxl_mshr_pf_hit = get_metrics_from_per_page_data(cxl_per_page_data_path)
        
        if cxl_llc_miss is None:
            print(f"Failed to get CXL metrics from {cxl_per_page_data_path}")
            continue
        
        # Add row to DataFrame
        metrics_df.loc[len(metrics_df)] = {
            'workload_name': workload_name,
            'dram_ipc': dram_ipc,
            'dram_llc_miss': dram_llc_miss,
            'dram_useful_prefetch_hit': dram_useful_prefetch_hit,
            'dram_mshr_pf_hit': dram_mshr_pf_hit,
            'cxl_ipc': cxl_ipc,
            'cxl_llc_miss': cxl_llc_miss,
            'cxl_useful_prefetch_hit': cxl_useful_prefetch_hit,
            'cxl_mshr_pf_hit': cxl_mshr_pf_hit
        }
    
    # Round numeric columns to 4 decimal places for IPC
    metrics_df['dram_ipc'] = metrics_df['dram_ipc'].round(4)
    metrics_df['cxl_ipc'] = metrics_df['cxl_ipc'].round(4)
    
    # Save to CSV
    output_file = 'requested_metrics.csv'
    metrics_df.to_csv(output_file, index=False)
    print(f"Metrics saved to {output_file}")
    print(f"Total workloads processed: {len(metrics_df)}")

if __name__ == "__main__":
    main()