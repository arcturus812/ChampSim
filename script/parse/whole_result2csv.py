#!/usr/bin/env python3
"""
Parse ChampSim log files and extract metrics to CSV
Usage: python whole_result2csv.py <directory_path>
"""

import os
import sys
import glob
import re
import csv
import argparse

def extract_simulation_stats(log_file_path):
    """
    Extract simulation statistics from ChampSim log file
    Returns: dict with extracted metrics
    """
    metrics = {
        'workload_name': os.path.basename(log_file_path).replace('.log', ''),
        'instructions': None,
        'cycles': None,
        'cumulative_ipc': None
    }
    
    # Initialize cache metrics for each layer
    cache_layers = ['l1d', 'l2c', 'llc']
    for layer in cache_layers:
        metrics[f'{layer}_total_hit'] = None
        metrics[f'{layer}_total_miss'] = None
        metrics[f'{layer}_total_mshr_merge'] = None
        metrics[f'{layer}_prefetch_hit'] = None
        metrics[f'{layer}_prefetch_miss'] = None
        metrics[f'{layer}_prefetch_mshr_merge'] = None
        metrics[f'{layer}_average_miss_latency'] = None
    
    try:
        with open(log_file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            
        skip_mode = False
        
        for line in lines:
            line = line.strip()
            
            # Skip [START_PAGE_STAT] to [END_PAGE_STAT] section
            if line.startswith('[START_PAGE_STAT]'):
                skip_mode = True
                continue
            elif line.startswith('[END_PAGE_STAT]'):
                skip_mode = False
                continue
            elif skip_mode:
                continue
            
            # Extract simulation completion stats
            if line.startswith('Simulation complete'):
                # Pattern: "Simulation complete CPU 0 instructions: 100000002 cycles: 48927311 cumulative IPC: 2.044"
                match = re.search(r'instructions:\s+(\d+)\s+cycles:\s+(\d+)\s+cumulative IPC:\s+([\d.]+)', line)
                if match:
                    metrics['instructions'] = int(match.group(1))
                    metrics['cycles'] = int(match.group(2))
                    metrics['cumulative_ipc'] = float(match.group(3))
            
            # Extract cache statistics
            # L1D TOTAL stats
            elif 'cpu0->cpu0_L1D TOTAL' in line:
                # Pattern: "cpu0->cpu0_L1D TOTAL        ACCESS:   41804003 HIT:   41592264 MISS:     211739 MSHR_MERGE:     176308"
                match = re.search(r'HIT:\s+(\d+)\s+MISS:\s+(\d+)\s+MSHR_MERGE:\s+(\d+)', line)
                if match:
                    metrics['l1d_total_hit'] = int(match.group(1))
                    metrics['l1d_total_miss'] = int(match.group(2))
                    metrics['l1d_total_mshr_merge'] = int(match.group(3))
            
            # L1D PREFETCH stats
            elif 'cpu0->cpu0_L1D PREFETCH' in line and 'ACCESS:' in line:
                # Pattern: "cpu0->cpu0_L1D PREFETCH     ACCESS:   17944473 HIT:   17935761 MISS:       8712 MSHR_MERGE:       2722"
                match = re.search(r'HIT:\s+(\d+)\s+MISS:\s+(\d+)\s+MSHR_MERGE:\s+(\d+)', line)
                if match:
                    metrics['l1d_prefetch_hit'] = int(match.group(1))
                    metrics['l1d_prefetch_miss'] = int(match.group(2))
                    metrics['l1d_prefetch_mshr_merge'] = int(match.group(3))
            
            # L1D AVERAGE MISS LATENCY
            elif 'cpu0->cpu0_L1D AVERAGE MISS LATENCY:' in line:
                # Pattern: "cpu0->cpu0_L1D AVERAGE MISS LATENCY: 259.9 cycles"
                match = re.search(r'LATENCY:\s+([\d.]+)\s+cycles', line)
                if match:
                    metrics['l1d_average_miss_latency'] = float(match.group(1))
            
            # L2C TOTAL stats
            elif 'cpu0->cpu0_L2C TOTAL' in line:
                match = re.search(r'HIT:\s+(\d+)\s+MISS:\s+(\d+)\s+MSHR_MERGE:\s+(\d+)', line)
                if match:
                    metrics['l2c_total_hit'] = int(match.group(1))
                    metrics['l2c_total_miss'] = int(match.group(2))
                    metrics['l2c_total_mshr_merge'] = int(match.group(3))
            
            # L2C PREFETCH stats
            elif 'cpu0->cpu0_L2C PREFETCH' in line and 'ACCESS:' in line:
                match = re.search(r'HIT:\s+(\d+)\s+MISS:\s+(\d+)\s+MSHR_MERGE:\s+(\d+)', line)
                if match:
                    metrics['l2c_prefetch_hit'] = int(match.group(1))
                    metrics['l2c_prefetch_miss'] = int(match.group(2))
                    metrics['l2c_prefetch_mshr_merge'] = int(match.group(3))
            
            # L2C AVERAGE MISS LATENCY
            elif 'cpu0->cpu0_L2C AVERAGE MISS LATENCY:' in line:
                match = re.search(r'LATENCY:\s+([\d.]+)\s+cycles', line)
                if match:
                    metrics['l2c_average_miss_latency'] = float(match.group(1))
            
            # LLC TOTAL stats
            elif 'cpu0->LLC TOTAL' in line:
                match = re.search(r'HIT:\s+(\d+)\s+MISS:\s+(\d+)\s+MSHR_MERGE:\s+(\d+)', line)
                if match:
                    metrics['llc_total_hit'] = int(match.group(1))
                    metrics['llc_total_miss'] = int(match.group(2))
                    metrics['llc_total_mshr_merge'] = int(match.group(3))
            
            # LLC PREFETCH stats
            elif 'cpu0->LLC PREFETCH' in line and 'ACCESS:' in line:
                match = re.search(r'HIT:\s+(\d+)\s+MISS:\s+(\d+)\s+MSHR_MERGE:\s+(\d+)', line)
                if match:
                    metrics['llc_prefetch_hit'] = int(match.group(1))
                    metrics['llc_prefetch_miss'] = int(match.group(2))
                    metrics['llc_prefetch_mshr_merge'] = int(match.group(3))
            
            # LLC AVERAGE MISS LATENCY
            elif 'cpu0->LLC AVERAGE MISS LATENCY:' in line:
                match = re.search(r'LATENCY:\s+([\d.]+)\s+cycles', line)
                if match:
                    metrics['llc_average_miss_latency'] = float(match.group(1))
        
        return metrics
        
    except Exception as e:
        print(f"Error processing {log_file_path}: {e}")
        return None

def main():
    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Parse ChampSim log files and extract metrics to CSV')
    parser.add_argument('directory', help='Directory path containing *.log files to parse')
    args = parser.parse_args()
    
    directory_path = args.directory
    
    if not os.path.isdir(directory_path):
        print(f"Error: '{directory_path}' is not a valid directory")
        sys.exit(1)
    
    # Find all *.log files in the directory
    log_pattern = os.path.join(directory_path, '*.log')
    log_files = glob.glob(log_pattern)
    
    if not log_files:
        print(f"No *.log files found in directory: {directory_path}")
        sys.exit(1)
    
    print(f"Found {len(log_files)} log files to process...")
    
    # Extract metrics from all log files
    results = []
    for log_file in log_files:
        print(f"Processing: {os.path.basename(log_file)}")
        metrics = extract_simulation_stats(log_file)
        if metrics:
            results.append(metrics)
    
    if not results:
        print("No valid metrics extracted from log files")
        sys.exit(1)
    
    # Define CSV columns
    columns = [
        'workload_name',
        'instructions', 'cycles', 'cumulative_ipc',
        'l1d_total_hit', 'l1d_total_miss', 'l1d_total_mshr_merge', 'l1d_prefetch_hit', 'l1d_prefetch_miss', 'l1d_prefetch_mshr_merge', 'l1d_average_miss_latency',
        'l2c_total_hit', 'l2c_total_miss', 'l2c_total_mshr_merge', 'l2c_prefetch_hit', 'l2c_prefetch_miss', 'l2c_prefetch_mshr_merge', 'l2c_average_miss_latency',
        'llc_total_hit', 'llc_total_miss', 'llc_total_mshr_merge', 'llc_prefetch_hit', 'llc_prefetch_miss', 'llc_prefetch_mshr_merge', 'llc_average_miss_latency'
    ]
    
    # Write results to CSV file
    output_file = 'results.csv'
    with open(output_file, 'w', newline='', encoding='utf-8') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=columns)
        writer.writeheader()
        
        for result in results:
            writer.writerow(result)
    
    print(f"\nResults saved to {output_file}")
    print(f"Successfully processed {len(results)} workloads")

if __name__ == "__main__":
    main()
