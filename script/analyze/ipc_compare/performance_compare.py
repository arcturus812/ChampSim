import pandas as pd
import os
import glob
import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from common import get_ipc_cycles_simultime_from_log, get_MPKI_from_log, get_avg_timeliness_from_per_page_data

pf_setting = 'nextline_spp_stream'
sim_setting = 'C1_W20M_S100M'

nfs_path = os.path.expanduser('/mnt/nvme1/nfs')
per_page_data_path = os.path.expanduser('/home/hwpark/workspace/storage/data/ChampSim/per_page_data')

log_path = '/home/hwpark/workspace/storage/log/ChampSim'

dram_path = f'all_dram/{pf_setting}/{sim_setting}'
cxl_path = f'all_cxl/{pf_setting}/{sim_setting}'


def main():
    performance_df = pd.DataFrame(columns=['workload_name', 'dram_ipc', 'cxl_ipc', 'dram_MPKI', 'cxl_MPKI', 'dram_req_arrival_delay_avg', 'cxl_req_arrival_delay_avg', 'dram_req_retrive_delay_avg', 'cxl_req_retrive_delay_avg', 'ipc_diff'])
    dram_logs = glob.glob(os.path.join(log_path, dram_path, '*.log'))
    for dram_log in dram_logs:
        workload_name = os.path.basename(dram_log).replace('.log', '')
        dram_ipc, dram_cycles, dram_simultime = get_ipc_cycles_simultime_from_log(dram_log)
        if dram_ipc is None:
            print(f"{dram_log} is wrong")
            continue
        cxl_log = dram_log.replace(dram_path, cxl_path)
        if not os.path.exists(cxl_log):
            print(f"corresponding {cxl_log} is not found")
            continue
        cxl_ipc, cxl_cycles, cxl_simultime = get_ipc_cycles_simultime_from_log(cxl_log)
        if cxl_ipc is None:
            print(f"{cxl_log} is wrong")
            continue

        # get ipc difference
        ipc_diff = dram_ipc - cxl_ipc

        # get MPKI
        dram_MPKI = get_MPKI_from_log(dram_log)
        cxl_MPKI = get_MPKI_from_log(cxl_log)
        if dram_MPKI is None or cxl_MPKI is None:
            print(f"{workload_name} MPKI is wrong")
            continue

        # get avg timeliness(delay) from per_page_data
        dram_per_page_data_path = os.path.join(per_page_data_path, 'all_dram', pf_setting, sim_setting, f'{workload_name}.csv')
        dram_req_arrival_delay_avg, dram_req_retrive_delay_avg = get_avg_timeliness_from_per_page_data(dram_per_page_data_path)
        cxl_per_page_data_path = os.path.join(per_page_data_path, 'all_cxl', pf_setting, sim_setting, f'{workload_name}.csv')
        cxl_req_arrival_delay_avg, cxl_req_retrive_delay_avg = get_avg_timeliness_from_per_page_data(cxl_per_page_data_path)
        if dram_req_arrival_delay_avg is None or dram_req_retrive_delay_avg is None:
            print(f"{workload_name} dram_req_arrival_delay_avg or dram_req_retrive_delay_avg is wrong")
            continue
        cxl_per_page_data_path = os.path.join(per_page_data_path, 'all_cxl', pf_setting, sim_setting, f'{workload_name}.csv')
        if cxl_req_arrival_delay_avg is None or cxl_req_retrive_delay_avg is None:
            print(f"{workload_name} cxl_req_arrival_delay_avg or cxl_req_retrive_delay_avg is wrong")
            continue

        # Do not use append, use loc to add a new row
        performance_df.loc[len(performance_df)] = {'workload_name': workload_name, 'dram_ipc': dram_ipc, 'cxl_ipc': cxl_ipc, 'dram_MPKI': dram_MPKI, 'cxl_MPKI': cxl_MPKI, 'dram_req_arrival_delay_avg': dram_req_arrival_delay_avg, 'cxl_req_arrival_delay_avg': cxl_req_arrival_delay_avg, 'dram_req_retrive_delay_avg': dram_req_retrive_delay_avg, 'cxl_req_retrive_delay_avg': cxl_req_retrive_delay_avg, 'ipc_diff': ipc_diff}
    
    # Round numeric columns to 4 decimal places
    numeric_columns = ['dram_ipc', 'cxl_ipc', 'dram_MPKI', 'cxl_MPKI', 'ipc_diff']
    performance_df[numeric_columns] = performance_df[numeric_columns].round(4)
    
    # save to csv
    performance_df.to_csv('performance.csv', index=False)

if __name__ == "__main__":
    main()