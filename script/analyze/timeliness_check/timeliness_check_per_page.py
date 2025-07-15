import pandas as pd
import os
import glob
import matplotlib.pyplot as plt
import numpy as np

prefetcher_setup="nextline_spp_stream"
simulation_setup="C1_W20M_S100M"

nfs_path = os.path.expanduser('/mnt/nvme1/nfs')
base_path = os.path.expanduser('~/workspace/storage/data/ChampSim/per_page_data')
dram_path = os.path.join(base_path, 'all_dram', prefetcher_setup, simulation_setup)
cxl_path = os.path.join(base_path, 'all_cxl', prefetcher_setup, simulation_setup)
graph_path = os.path.join('~/workspace/storage/graph', 'timeliness_check_per_page')

src_csv_header="pfn,vfn,cpu,l1d_hit,l1d_miss,l1d_prefetch,l1d_useful_prefetch_hit,l1d_mshr_pf_hit,l1d_mshr_pf_hit_cycle,l1d_mshr_pf_hit_delay_cycle,l1d_pf_degree_sum,l1d_pf_degree_cnt,l1d_useless_prefetch,l1d_miss_handle_cycle,l1d_miss_handle_cnt,l2c_hit,l2c_miss,l2c_prefetch,l2c_useful_prefetch_hit,l2c_mshr_pf_hit,l2c_mshr_pf_hit_cycle,l2c_mshr_pf_hit_delay_cycle,l2c_pf_degree_sum,l2c_pf_degree_cnt,l2c_useless_prefetch,l2c_miss_handle_cycle,l2c_miss_handle_cnt,llc_hit,llc_miss,llc_prefetch,llc_useful_prefetch_hit,llc_mshr_pf_hit,llc_mshr_pf_hit_cycle,llc_mshr_pf_hit_delay_cycle,llc_pf_degree_sum,llc_pf_degree_cnt,llc_useless_prefetch,llc_miss_handle_cycle,llc_miss_handle_cnt"
INSTRUCTION=100000000
KI=INSTRUCTION/1000

# Collect CSV files from both directories
dram_files = glob.glob(os.path.join(dram_path, '*.csv'))
cxl_files = glob.glob(os.path.join(cxl_path, '*.csv'))

def draw_cdf(workload_name, df, MPKI, output_png_path, target_column):
    # Create output directory if it doesn't exist
    os.makedirs(os.path.dirname(output_png_path), exist_ok=True)
    
    # Remove rows with NaN or infinite values in target column
    df_clean = df.dropna(subset=[target_column])
    df_clean = df_clean[np.isfinite(df_clean[target_column])]
    
    if len(df_clean) == 0:
        print(f"No valid data for {workload_name} in column {target_column}")
        return
    
    # Sort values for CDF calculation
    sorted_values = np.sort(df_clean[target_column])
    
    # Calculate CDF
    n = len(sorted_values)
    y_values = np.arange(1, n + 1) / n
    
    # Create the plot
    plt.figure(figsize=(10, 6))
    plt.plot(y_values, sorted_values, linewidth=2)
    
    # Set title with workload name and MPKI
    plt.title(f'{workload_name} MPKI: {MPKI:.2f}', fontsize=14, fontweight='bold')
    
    # Set y-axis (target_column values) with maximum 5 labels
    y_min = sorted_values.min()
    y_max = sorted_values.max()
    plt.ylim(y_min, y_max)
    
    # Create 5 evenly spaced y-axis labels
    y_ticks = np.linspace(y_min, y_max, 5)
    y_labels = [f'{tick:.2f}' for tick in y_ticks]
    plt.yticks(y_ticks, y_labels)
    plt.ylabel(target_column, fontsize=12)
    
    # Set x-axis (page count) with maximum 5 labels
    plt.xlim(0, 1)
    x_ticks = np.linspace(0, 1, 5)
    x_labels = [f'{int(tick * n)}' for tick in x_ticks]
    plt.xticks(x_ticks, x_labels)
    plt.xlabel('Page Count', fontsize=12)
    
    # Add horizontal grid lines at y-axis tick positions
    plt.grid(True, alpha=0.3, axis='y', linestyle='-')
    # Add vertical grid lines at x-axis tick positions
    plt.grid(True, alpha=0.3, axis='x', linestyle='-')
    
    # Adjust layout and save
    plt.tight_layout()
    plt.savefig(output_png_path, dpi=300, bbox_inches='tight')
    plt.close()
    
    print(f"CDF plot saved: {output_png_path}")


def draw_cdf_comparison(workload_name, cxl_df, cxl_MPKI, dram_df, dram_MPKI, output_png_path, target_column):
    # Create output directory if it doesn't exist
    os.makedirs(os.path.dirname(output_png_path), exist_ok=True)
    
    # Clean data for both datasets
    cxl_clean = cxl_df.dropna(subset=[target_column])
    cxl_clean = cxl_clean[np.isfinite(cxl_clean[target_column])]
    
    dram_clean = dram_df.dropna(subset=[target_column])
    dram_clean = dram_clean[np.isfinite(dram_clean[target_column])]
    
    if len(cxl_clean) == 0 or len(dram_clean) == 0:
        print(f"No valid data for {workload_name} in column {target_column}")
        return
    
    # Sort values for CDF calculation
    cxl_sorted = np.sort(cxl_clean[target_column])
    dram_sorted = np.sort(dram_clean[target_column])
    
    # Calculate CDF for both datasets
    cxl_n = len(cxl_sorted)
    dram_n = len(dram_sorted)
    
    cxl_y_values = np.arange(1, cxl_n + 1) / cxl_n
    dram_y_values = np.arange(1, dram_n + 1) / dram_n
    
    # Determine y-axis range based on pf_mshr_delay_avg from both datasets
    y_min = min(cxl_sorted.min(), dram_sorted.min())
    y_max = max(cxl_sorted.max(), dram_sorted.max())
    
    # Create the plot
    plt.figure(figsize=(12, 8))
    
    # Plot both CDFs
    plt.plot(cxl_y_values, cxl_sorted, linewidth=2, label=f'CXL (MPKI: {cxl_MPKI:.2f})', color='red')
    plt.plot(dram_y_values, dram_sorted, linewidth=2, label=f'DRAM (MPKI: {dram_MPKI:.2f})', color='blue')
    
    # Set title with workload name
    plt.title(f'{workload_name} - CDF Comparison', fontsize=14, fontweight='bold')
    
    # Set y-axis with maximum 5 labels
    plt.ylim(y_min, y_max)
    y_ticks = np.linspace(y_min, y_max, 5)
    y_labels = [f'{tick:.2f}' for tick in y_ticks]
    plt.yticks(y_ticks, y_labels)
    plt.ylabel(target_column, fontsize=12)
    
    # Set x-axis (page count) with maximum 5 labels
    plt.xlim(0, 1)
    x_ticks = np.linspace(0, 1, 5)
    # Use the maximum page count between both datasets for x-axis labels
    max_pages = max(cxl_n, dram_n)
    x_labels = [f'{int(tick * max_pages)}' for tick in x_ticks]
    plt.xticks(x_ticks, x_labels)
    plt.xlabel('Page Count', fontsize=12)
    
    # Add grid lines
    plt.grid(True, alpha=0.3, axis='y', linestyle='-')
    plt.grid(True, alpha=0.3, axis='x', linestyle='-')
    
    # Add legend
    plt.legend(loc='best', fontsize=10)
    
    # Adjust layout and save
    plt.tight_layout()
    plt.savefig(output_png_path, dpi=300, bbox_inches='tight')
    plt.close()
    
    print(f"CDF comparison plot saved: {output_png_path}")


def calculate_timeliness(workload_name, file_path):
    df = pd.read_csv(file_path)
    # check if the header is the same as src_csv_header
    if df.columns.tolist() != src_csv_header.split(','):
        print(f"Header mismatch in {workload_name}")
        return

    # calculate MPKI(all_llc_miss)
    all_llc_miss = df['llc_miss'].sum()
    MPKI = all_llc_miss / KI
    
    # calculate the timeliness
    df['pf_useless'] = df['l1d_useless_prefetch'] + df['l2c_useless_prefetch'] + df['llc_useless_prefetch']
    df['pf_ontime_hit'] = df['l1d_useful_prefetch_hit'] + df['l2c_useful_prefetch_hit'] + df['llc_useful_prefetch_hit']
    df['pf_mshr_hit'] = df['l1d_mshr_pf_hit'] + df['l2c_mshr_pf_hit'] + df['llc_mshr_pf_hit']

    df['pf_mshr_arrival_delay_sum'] = df['l1d_mshr_pf_hit_cycle'] + df['l2c_mshr_pf_hit_cycle'] + df['llc_mshr_pf_hit_cycle']
    df['pf_mshr_arrival_delay_avg'] = df['pf_mshr_arrival_delay_sum'] / df['pf_mshr_hit']

    df['pf_mshr_retrive_delay_sum'] = df['l1d_mshr_pf_hit_delay_cycle'] + df['l2c_mshr_pf_hit_delay_cycle'] + df['llc_mshr_pf_hit_delay_cycle']
    df['pf_mshr_retrive_delay_avg'] = df['pf_mshr_retrive_delay_sum'] / df['pf_mshr_hit']

    return df, MPKI


def main():
    cxl_file_name_list = []
    dram_file_name_list = []
    
    for cxl_file in cxl_files:
        cxl_file_name_list.append(os.path.basename(cxl_file))
    for dram_file in dram_files:
        dram_file_name_list.append(os.path.basename(dram_file))
    
    # Find files that exist in both directories
    common_files = []
    for cxl_file_name in cxl_file_name_list:
        if cxl_file_name in dram_file_name_list:
            common_files.append(cxl_file_name)

    for file_name in common_files:
        workload_name = os.path.basename(file_name).replace('.csv', '')
        
        # Process CXL data
        cxl_file_path = os.path.join(cxl_path, file_name)
        cxl_result = calculate_timeliness(workload_name, cxl_file_path)
        
        # Process DRAM data
        dram_file_path = os.path.join(dram_path, file_name)
        dram_result = calculate_timeliness(workload_name, dram_file_path)
        
        if cxl_result is not None and dram_result is not None:
            cxl_df, cxl_MPKI = cxl_result
            dram_df, dram_MPKI = dram_result
            
            # Create comparison graph
            target_column = ['pf_mshr_arrival_delay_avg', 'pf_mshr_retrive_delay_avg', 'pf_useless', 'pf_ontime_hit', 'pf_mshr_hit']
            for column in target_column:
                output_png_path = os.path.join(nfs_path, 'timeliness_check_per_page', 'comparison', prefetcher_setup, simulation_setup, column, f'{workload_name}.png')
                draw_cdf_comparison(workload_name, cxl_df, cxl_MPKI, dram_df, dram_MPKI, output_png_path, column)
            
            # Clean up memory
            del cxl_df, dram_df
        else:
            print(f"Skip drawing for {workload_name} due to header mismatch or error")

if __name__ == "__main__":
    main()