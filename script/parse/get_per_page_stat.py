import os
import sys
import csv
import re
import glob

real_header="pfn,vfn,cpu,l1d_hit,l1d_miss,l1d_prefetch,l1d_useful_prefetch_hit,l1d_mshr_pf_hit,l1d_mshr_pf_hit_cycle,l1d_mshr_pf_hit_delay_cycle,l1d_pf_degree_sum,l1d_pf_degree_cnt,l1d_useless_prefetch,l1d_miss_handle_cycle,l1d_miss_handle_cnt,l2c_hit,l2c_miss,l2c_prefetch,l2c_useful_prefetch_hit,l2c_mshr_pf_hit,l2c_mshr_pf_hit_cycle,l2c_mshr_pf_hit_delay_cycle,l2c_pf_degree_sum,l2c_pf_degree_cnt,l2c_useless_prefetch,l2c_miss_handle_cycle,l2c_miss_handle_cnt,llc_hit,llc_miss,llc_prefetch,llc_useful_prefetch_hit,llc_mshr_pf_hit,llc_mshr_pf_hit_cycle,llc_mshr_pf_hit_delay_cycle,llc_pf_degree_sum,llc_pf_degree_cnt,llc_useless_prefetch,llc_miss_handle_cycle,llc_miss_handle_cnt"

def parse_log_file(filepath):
    data_started = False
    header = []
    rows = []

    with open(filepath, 'r') as f:
        for line in f:
            if line.startswith("[START_PAGE_STAT]"):
                data_started = True
                header = next(f).strip().split(',') # just for skipping the first line
                header = real_header.split(',')
                continue

            if data_started:
                if line.startswith("[END_PAGE_STAT]"):
                    break
                row = line.strip().split(',')
                if len(row) == len(header):
                    rows.append(row)
                else:
                    print(f"len diff: {len(row)} {len(header)}")

    return header, rows

def write_csv(output_filepath, header, rows):
    with open(output_filepath, 'w', newline='') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(header)
        writer.writerows(rows)

def main(input_dir, output_dir):
    log_files = glob.glob(os.path.join(input_dir, '*.log'))
    os.makedirs(output_dir, exist_ok=True)

    for log_file in log_files:
        workload_name = os.path.basename(log_file).replace('.log', '')
        output_filepath = os.path.join(output_dir, f'{workload_name}.csv')

        header, rows = parse_log_file(log_file)

        if header and rows:
            write_csv(output_filepath, header, rows)
            print(f"Saved CSV: {output_filepath}")
        else:
            print(f"No data found in: {log_file}")

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(f"Usage: python {sys.argv[0]} <log_directory> <output_directory>")
        sys.exit(1)

    input_directory = sys.argv[1]
    output_directory = sys.argv[2]

    main(input_directory, output_directory)

