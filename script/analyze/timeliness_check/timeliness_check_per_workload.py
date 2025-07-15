import pandas as pd
import os
import glob

# Define paths
nfs_path = os.path.expanduser('/mnt/nvme1/nfs')
base_path = os.path.expanduser('~/workspace/storage/data/ChampSim/per_page_data')
dram_path = os.path.join(base_path, 'all_dram/nextline_spp_stream/C1_W20M_S100M')
cxl_path = os.path.join(base_path, 'all_cxl/nextline_spp_stream/C1_W20M_S100M')

# Collect CSV files from both directories
dram_files = glob.glob(os.path.join(dram_path, '*.csv'))
cxl_files = glob.glob(os.path.join(cxl_path, '*.csv'))

# Map workload name to file paths (assuming filenames match)
workloads = {}
for df in dram_files:
    name = os.path.basename(df)
    workloads[name] = {'dram': df}
for cf in cxl_files:
    name = os.path.basename(cf)
    if name in workloads:
        workloads[name]['cxl'] = cf

# Define metrics to compare
metrics = [
    'l1d_mshr_pf_hit', 'l2c_mshr_pf_hit', 'llc_mshr_pf_hit',
    'l1d_useless_prefetch', 'l2c_useless_prefetch', 'llc_useless_prefetch',
    'l1d_useful_prefetch_hit', 'l2c_useful_prefetch_hit', 'llc_useful_prefetch_hit'
]

# Define metrics that indicate timeliness degradation when decreased
decrease_metrics = [
    'l1d_useful_prefetch_hit', 'l2c_useful_prefetch_hit', 'llc_useful_prefetch_hit'
]

# Filter workloads that have both DRAM and CXL files
complete_workloads = {name: paths for name, paths in workloads.items() if 'dram' in paths and 'cxl' in paths}
if not complete_workloads:
    print("No matching workload files found between DRAM and CXL directories")
    exit(1)

# Determine maximum workload name length for alignment
max_name_len = max(len(name) for name in complete_workloads)

# Prepare header and row formatting strings
name_col = 'Workload'
metric_col = 'Metric'
count_col = 'Count'
percent_col = '%'
avg_col = 'AvgChange'
header_fmt = f"{{:{max_name_len}}}\t{{:25}}\t{{:>7}}\t{{:>7}}\t{{:>10}}"
row_fmt    = f"{{:{max_name_len}}}\t{{:25}}\t{{:7d}}\t{{:7.2f}}\t{{:10.2f}}"

# Print header
print(header_fmt.format(name_col, metric_col, count_col, percent_col, avg_col))

# Process each workload and print aligned results
for name, paths in sorted(complete_workloads.items()):
    dram_df = pd.read_csv(paths['dram'])
    cxl_df  = pd.read_csv(paths['cxl'])
    total_pages = len(dram_df)

    for metric in metrics:
        merged = dram_df[['vfn', metric]].merge(
            cxl_df[['vfn', metric]], on='vfn', suffixes=('_dram', '_cxl')
        )

        # Determine condition and compute delta
        if metric in decrease_metrics:
            cond = merged[f"{metric}_cxl"] < merged[f"{metric}_dram"]
            delta = merged[f"{metric}_dram"] - merged[f"{metric}_cxl"]
        else:
            cond = merged[f"{metric}_cxl"] > merged[f"{metric}_dram"]
            delta = merged[f"{metric}_cxl"] - merged[f"{metric}_dram"]

        cnt = cond.sum()
        pct = (cnt / total_pages) * 100
        avg_change = delta[cond].mean() if cnt > 0 else 0.0

        # Print formatted row
        print(row_fmt.format(name, metric, cnt, pct, avg_change))
