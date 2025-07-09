import threading
import heapq
import time
import socket
import os
import re
from typing import Union
from worker import JobWorker
from resource_monitor import ResourceMonitor

MAX_CORES = 64
MAX_MEMORY_GB = 128

class PriorityJobQueue:
    def __init__(self, sim_times):
        self.sim_times = sim_times  # dict[str, float]
        self.counter = 0
        self.heap = []
        self.lock = threading.Lock()

    def put(self, command: str):
        workload = self._extract_workload_name(command)
        if workload and workload in self.sim_times:
            priority = -self.sim_times[workload]  # longer time = higher priority (max-heap)
        else:
            priority = float('inf')  # unknown = FIFO after known jobs
        with self.lock:
            heapq.heappush(self.heap, (priority, self.counter, command))
            self.counter += 1

    def get(self):
        with self.lock:
            if self.heap:
                return heapq.heappop(self.heap)[2]
            return None

    def empty(self):
        with self.lock:
            return len(self.heap) == 0

    def _extract_workload_name(self, command: str) -> Union[str, None]:
        # Match something like 605.mcf_s-782B.champsimtrace.xz
        match = re.search(r'([\w\d]+\.[\w\d_]+-\d+B)\.champsimtrace\.xz', command)
        return match.group(1) if match else None


def load_simulation_times() -> dict:
    """
    Looks for simulation_time.txt in parent directory and loads it.
    Format: workload_name: time
    """
    base_dir = os.path.dirname(os.path.abspath(__file__))
    sim_time_path = os.path.join(os.path.dirname(base_dir), 'simulation_time.txt')
    sim_times = {}

    if os.path.isfile(sim_time_path):
        with open(sim_time_path) as f:
            for line in f:
                if ':' in line:
                    name, value = line.strip().split(':', 1)
                    try:
                        sim_times[name.strip()] = float(value.strip())
                    except ValueError:
                        continue
        print(f"[Champsim Scheduler] Loaded simulation_time.txt with {len(sim_times)} workload entries.")
    else:
        print("[Champsim Scheduler] simulation_time.txt not found. Running without workload time hints.")
    return sim_times


class ChampScheduler:
    def __init__(self, max_cores, max_memory_gb, port, pause_threshold, kill_threshold):
        self.max_cores = max_cores
        self.max_memory_gb = max_memory_gb
        self.sim_times = load_simulation_times()
        self.job_queue = PriorityJobQueue(self.sim_times)
        self.running_jobs = []
        self.lock = threading.Lock()
        self.resource_monitor = ResourceMonitor(pause_threshold, kill_threshold)
        self.port = port

    def start(self):
        threading.Thread(target=self._socket_server, daemon=True).start()
        threading.Thread(target=self._scheduler_loop, daemon=True).start()
        self._resource_monitor_loop()

    def _socket_server(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('localhost', self.port))
            s.listen()
            print(f"[Daemon] Listening on port {self.port}")
            while True:
                conn, _ = s.accept()
                with conn:
                    cmd = conn.recv(4096).decode()
                    if cmd:
                        self.job_queue.put(cmd)
                        conn.sendall(b"Command queued\n")

    def _scheduler_loop(self):
        while True:
            if self.resource_monitor.should_pause() or not self.resource_monitor.can_start_new_job():
                print("[Scheduler] Pausing due to insufficient memory")
                time.sleep(1)
                continue

            if not self.job_queue.empty():
                cmd = self.job_queue.get()
                worker = JobWorker(cmd, self.resource_monitor, self.job_queue)
                with self.lock:
                    self.running_jobs.append(worker)
                worker.start()

            time.sleep(5)  # Execute one job every 10 seconds

    def _resource_monitor_loop(self):
        while True:
            self.resource_monitor.update()
            if self.resource_monitor.should_kill():
                with self.lock:
                    if self.running_jobs:
                        victim = self.running_jobs.pop()
                        victim.terminate_and_requeue()
            time.sleep(1)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--cores', type=int, default=MAX_CORES)
    parser.add_argument('--memory', type=int, default=MAX_MEMORY_GB)
    parser.add_argument('--pause-threshold', type=float, default=0.10, help='Pause if available memory below this fraction')
    parser.add_argument('--kill-threshold', type=float, default=0.05, help='Kill last job if below this fraction')
    args = parser.parse_args()

    scheduler = ChampScheduler(
        max_cores=args.cores,
        max_memory_gb=args.memory,
        port=5555,
        pause_threshold=args.pause_threshold,
        kill_threshold=args.kill_threshold
    )
    scheduler.start()
    while True:
        time.sleep(60)
