#!/usr/bin/env python3
"""
Simple ChampSim execution example script
Usage: python3 run_test.py [trace_file]
"""

import os
import sys
import subprocess
from pathlib import Path

# Get the script directory and project root
SCRIPT_DIR = Path(__file__).parent.absolute()
PROJECT_ROOT = SCRIPT_DIR.parent
BIN_DIR = PROJECT_ROOT / "bin"
EXECUTABLE = BIN_DIR / "champsim"
CONFIG_FILE = PROJECT_ROOT / "champsim_config.json"

def main():
    # Check if executable exists
    if not EXECUTABLE.exists():
        print(f"Error: ChampSim executable not found at {EXECUTABLE}")
        print("Please build ChampSim first:")
        print("  ./config.sh champsim_config.json")
        print("  make")
        sys.exit(1)
    
    # Get trace file from command line or use default
    if len(sys.argv) > 1:
        trace_file = Path(sys.argv[1])
    else:
        print("Usage: python3 run_test.py <trace_file>")
        print("Example: python3 run_test.py /path/to/trace.champsimtrace.xz")
        sys.exit(1)
    
    # Check if trace file exists
    if not trace_file.exists():
        print(f"Error: Trace file not found: {trace_file}")
        sys.exit(1)
    
    # Default simulation parameters
    # warmup_instructions = 20000000 # 20M
    # simulation_instructions = 100000000 # 100M
    warmup_instructions = 100000 
    simulation_instructions = 1000000
    
    # Build command
    cmd = [
        str(EXECUTABLE),
        "--warmup-instructions", str(warmup_instructions),
        "--simulation-instructions", str(simulation_instructions),
        str(trace_file)
    ]
    
    print("=" * 60)
    print("ChampSim Simple Test Run")
    print("=" * 60)
    print(f"Executable: {EXECUTABLE}")
    print(f"Trace file: {trace_file}")
    print(f"Warmup instructions: {warmup_instructions:,}")
    print(f"Simulation instructions: {simulation_instructions:,}")
    print("=" * 60)
    print()
    
    # Run simulation
    try:
        subprocess.run(cmd, check=True, cwd=PROJECT_ROOT)
    except subprocess.CalledProcessError as e:
        print(f"Error: Simulation failed with exit code {e.returncode}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nSimulation interrupted by user")
        sys.exit(1)

if __name__ == "__main__":
    main()

