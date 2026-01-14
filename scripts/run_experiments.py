#!/usr/bin/env python3
"""
ChampSim 실험 실행 스크립트
생성된 바이너리들을 여러 trace에 대해 병렬 실행하고 결과를 CSV로 저장합니다.
DRAM과 CXL 두 가지 메모리 타입에 대해 각각 실험을 수행합니다.
"""

import concurrent.futures
import subprocess
import re
from pathlib import Path
import pandas as pd
import sys

# 디렉토리 설정
SCRIPT_DIR = Path(__file__).parent.absolute()
PROJECT_ROOT = SCRIPT_DIR.parent
BIN_DIRS = {
    'dram': PROJECT_ROOT / "bin_experiments_dram",
    'cxl': PROJECT_ROOT / "bin_experiments_cxl"
}
TRACE_DIR = Path("/home/hwpark/workspace/storage/trace/champsim/spec_total")
LOG_DIRS = {
    'dram': SCRIPT_DIR / "logs_dram",
    'cxl': SCRIPT_DIR / "logs_cxl"
}
OUTPUT_CSVS = {
    'dram': PROJECT_ROOT / "result_dram.csv",
    'cxl': PROJECT_ROOT / "result_cxl.csv"
}

# Trace 파일 목록 (11개)
TRACES = [
    "429.mcf-184B.champsimtrace.xz",
    "605.mcf_s-1152B.champsimtrace.xz",
    "470.lbm-1274B.champsimtrace.xz",
    "450.soplex-92B.champsimtrace.xz",
    "649.fotonik3d_s-1B.champsimtrace.xz",
    "403.gcc-16B.champsimtrace.xz",
    "602.gcc_s-734B.champsimtrace.xz",
    "471.omnetpp-188B.champsimtrace.xz",
    "410.bwaves-945B.champsimtrace.xz",
    "481.wrf-816B.champsimtrace.xz"
]

# 실험 파라미터
WARMUP_INSTRUCTIONS = 20000000  # 20M
SIMULATION_INSTRUCTIONS = 100000000  # 100M

# 병렬 실행 최대 워커 수
MAX_WORKERS = 64


def parse_binary_name(binary_name):
    """
    바이너리 이름에서 파라미터 추출
    형식: ROB{rob}_LQ{lq}_SQ{sq}_M1{m1}_M2{m2}_MLLC{mllc}
    """
    pattern = r'ROB(\d+)_LQ(\d+)_SQ(\d+)_M1(\d+)_M2(\d+)_MLLC(\d+)'
    match = re.match(pattern, binary_name)
    if match:
        return {
            'ROB': int(match.group(1)),
            'LQ': int(match.group(2)),
            'SQ': int(match.group(3)),
            'MSHR_L1': int(match.group(4)),
            'MSHR_L2': int(match.group(5)),
            'MSHR_LLC': int(match.group(6))
        }
    return None


def extract_ipc(log_file):
    """
    로그 파일에서 Simulation phase의 cumulative IPC 추출
    Warmup phase는 제외
    """
    try:
        with open(log_file, 'r') as f:
            for line in f:
                # Warmup 제외, Simulation phase만
                if 'finished' in line and 'Warmup' not in line:
                    match = re.search(r'cumulative IPC:\s+([\d.]+)', line)
                    if match:
                        return float(match.group(1))
    except Exception as e:
        print(f"  Error reading log file {log_file}: {e}")
    return None


def run_experiment(binary_path, trace_path, log_path):
    """
    단일 실험 실행
    """
    binary_name = binary_path.stem
    trace_name = trace_path.name
    
    # 로그 디렉토리 생성
    log_path.parent.mkdir(parents=True, exist_ok=True)
    
    # ChampSim 실행 명령
    cmd = [
        str(binary_path),
        "--warmup-instructions", str(WARMUP_INSTRUCTIONS),
        "--simulation-instructions", str(SIMULATION_INSTRUCTIONS),
        str(trace_path)
    ]
    
    # 실행 및 로그 저장
    try:
        with open(log_path, 'w') as log_file:
            result = subprocess.run(
                cmd,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                cwd=PROJECT_ROOT
            )
        
        if result.returncode == 0:
            # IPC 추출
            ipc = extract_ipc(log_path)
            return (binary_name, trace_name, ipc, True)
        else:
            return (binary_name, trace_name, None, False)
    except Exception as e:
        print(f"  Error running {binary_name} with {trace_name}: {e}")
        return (binary_name, trace_name, None, False)


def run_experiments_for_memory_type(memory_type, bin_dir, log_dir, output_csv):
    """
    특정 메모리 타입에 대한 실험 실행
    """
    print("\n" + "=" * 70)
    print(f"Running experiments for {memory_type.upper()}")
    print("=" * 70)
    
    # 바이너리 목록 스캔
    if not bin_dir.exists():
        print(f"Warning: Binary directory not found: {bin_dir}")
        print(f"Skipping {memory_type.upper()} experiments.")
        return
    
    binaries = sorted([b for b in bin_dir.iterdir() if b.is_file() and b.stat().st_mode & 0o111])
    if not binaries:
        print(f"Warning: No executable binaries found in {bin_dir}")
        print(f"Skipping {memory_type.upper()} experiments.")
        return
    
    print(f"Found {len(binaries)} binaries")
    print(f"Trace files: {len(TRACES)}")
    print(f"Total experiments: {len(binaries) * len(TRACES)}")
    print(f"Max parallel workers: {MAX_WORKERS}")
    print("=" * 70)
    
    # Trace 파일 확인
    trace_paths = []
    for trace_name in TRACES:
        trace_path = TRACE_DIR / trace_name
        if not trace_path.exists():
            print(f"Warning: Trace file not found: {trace_path}")
        else:
            trace_paths.append((trace_name, trace_path))
    
    if not trace_paths:
        print(f"Error: No valid trace files found for {memory_type.upper()}")
        return
    
    # 실험 조합 생성
    experiments = []
    for binary in binaries:
        binary_name = binary.stem
        for trace_name, trace_path in trace_paths:
            # 로그 파일명: {binary_name}_{trace_name_without_ext}.log
            trace_base = trace_name.replace('.champsimtrace.xz', '')
            log_name = f"{binary_name}_{trace_base}.log"
            log_path = log_dir / log_name
            experiments.append((binary, trace_path, log_path))
    
    print(f"\nStarting {len(experiments)} experiments...")
    print(f"Log directory: {log_dir}")
    print()
    
    # 병렬 실행
    results = []
    completed = 0
    
    with concurrent.futures.ProcessPoolExecutor(max_workers=MAX_WORKERS) as executor:
        # 모든 실험 제출
        future_to_exp = {
            executor.submit(run_experiment, binary, trace, log): (binary.stem, trace.name)
            for binary, trace, log in experiments
        }
        
        # 결과 수집
        for future in concurrent.futures.as_completed(future_to_exp):
            completed += 1
            binary_name, trace_name = future_to_exp[future]
            
            try:
                result = future.result()
                results.append(result)
                
                if result[3]:  # success
                    ipc_str = f"{result[2]:.4f}" if result[2] is not None else "N/A"
                    print(f"[{memory_type.upper()}] [{completed}/{len(experiments)}] ✓ {binary_name} × {trace_name}: IPC={ipc_str}")
                else:
                    print(f"[{memory_type.upper()}] [{completed}/{len(experiments)}] ✗ {binary_name} × {trace_name}: FAILED")
            except Exception as e:
                print(f"[{memory_type.upper()}] [{completed}/{len(experiments)}] ✗ {binary_name} × {trace_name}: ERROR - {e}")
                results.append((binary_name, trace_name, None, False))
    
    print(f"\n[{memory_type.upper()}] Experiments completed!")
    print("=" * 70)
    
    # 결과 정리 및 CSV 생성
    print(f"\n[{memory_type.upper()}] Generating CSV...")
    
    # 바이너리별 파라미터 추출
    binary_params = {}
    for binary in binaries:
        binary_name = binary.stem
        params = parse_binary_name(binary_name)
        if params:
            binary_params[binary_name] = params
    
    # DataFrame 생성 (롱 형식: 각 행이 바이너리-trace 조합)
    rows = []
    for binary_name, trace_name, ipc, success in results:
        params = binary_params.get(binary_name)
        if params:
            trace_base = trace_name.replace('.champsimtrace.xz', '')
            row = {
                'workload': trace_base,
                'ROB': params['ROB'],
                'LQ': params['LQ'],
                'SQ': params['SQ'],
                'MSHR_L1': params['MSHR_L1'],
                'MSHR_L2': params['MSHR_L2'],
                'MSHR_LLC': params['MSHR_LLC'],
                'IPC': ipc if success and ipc is not None else None
            }
            rows.append(row)
    
    # DataFrame 생성 및 저장
    df = pd.DataFrame(rows)
    
    # 컬럼 순서: workload, ROB, LQ, SQ, MSHR_L1, MSHR_L2, MSHR_LLC, IPC
    columns = ['workload', 'ROB', 'LQ', 'SQ', 'MSHR_L1', 'MSHR_L2', 'MSHR_LLC', 'IPC']
    df = df[columns]
    
    df.to_csv(output_csv, index=False)
    
    print(f"[{memory_type.upper()}] Results saved to: {output_csv}")
    
    # 통계 출력
    total_experiments = len(experiments)
    successful = sum(1 for r in results if r[3] and r[2] is not None)
    failed = total_experiments - successful
    
    print(f"\n[{memory_type.upper()}] Summary")
    print("=" * 70)
    print(f"Total experiments: {total_experiments}")
    print(f"Successful: {successful}")
    print(f"Failed: {failed}")
    print(f"Success rate: {successful/total_experiments*100:.1f}%")
    print("=" * 70)


def main():
    """메인 실행 함수"""
    print("=" * 70)
    print("ChampSim Experiment Runner (DRAM & CXL)")
    print("=" * 70)
    
    # 각 메모리 타입에 대해 실험 실행
    for memory_type in ['dram', 'cxl']:
        bin_dir = BIN_DIRS[memory_type]
        log_dir = LOG_DIRS[memory_type]
        output_csv = OUTPUT_CSVS[memory_type]
        
        run_experiments_for_memory_type(memory_type, bin_dir, log_dir, output_csv)
    
    print("\n" + "=" * 70)
    print("All experiments completed!")
    print("=" * 70)
    print(f"DRAM results: {OUTPUT_CSVS['dram']}")
    print(f"CXL results: {OUTPUT_CSVS['cxl']}")
    print("=" * 70)


if __name__ == "__main__":
    main()

