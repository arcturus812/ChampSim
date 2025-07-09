#!/usr/bin/env python3
"""
ChampSim Scheduler with TUI Controller
"""

import argparse
import time
import sys
import os

# Add current directory to path for imports
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from champsim_scheduler import ChampScheduler, MAX_CORES, MAX_MEMORY_GB

def main():
    parser = argparse.ArgumentParser(description='ChampSim Scheduler with TUI Controller')
    parser.add_argument('--cores', type=int, default=MAX_CORES)
    parser.add_argument('--memory', type=int, default=MAX_MEMORY_GB)
    parser.add_argument('--pause-threshold', type=float, default=0.10, 
                       help='Pause if available memory below this fraction')
    parser.add_argument('--kill-threshold', type=float, default=0.05, 
                       help='Kill last job if below this fraction')
    parser.add_argument('--port', type=int, default=5555, 
                       help='Port for socket communication')
    parser.add_argument('--no-tui', action='store_true', 
                       help='Run without TUI interface')
    
    args = parser.parse_args()
    
    # Create scheduler
    scheduler = ChampScheduler(
        max_cores=args.cores,
        max_memory_gb=args.memory,
        port=args.port,
        pause_threshold=args.pause_threshold,
        kill_threshold=args.kill_threshold
    )
    
    if not args.no_tui:
        try:
            # Try to import and use TUI controller
            from tui_controller import TUIController
            
            # Create TUI controller
            tui = TUIController(scheduler)
            scheduler.set_tui_controller(tui)
            
            # Start scheduler in background
            import threading
            scheduler_thread = threading.Thread(target=scheduler.start, daemon=True)
            scheduler_thread.start()
            
            # Start TUI
            print("Starting ChampSim Scheduler with TUI Controller...")
            tui.start()
            
        except ImportError as e:
            print(f"TUI dependencies not available: {e}")
            print("Install rich library: pip install rich")
            print("Running without TUI...")
            scheduler.start()
            while True:
                time.sleep(60)
    else:
        # Run without TUI
        print("Starting ChampSim Scheduler without TUI...")
        scheduler.start()
        while True:
            time.sleep(60)

if __name__ == "__main__":
    main() 