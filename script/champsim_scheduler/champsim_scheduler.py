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
        self.job_info_map = {}  # Track job info by sequence number

    def put(self, command: str):
        workload = self._extract_workload_name(command)
        if workload and workload in self.sim_times:
            priority = -self.sim_times[workload]  # longer time = higher priority (max-heap)
        else:
            priority = float('inf')  # unknown = FIFO after known jobs
        with self.lock:
            sequence_number = self.counter + 1
            heapq.heappush(self.heap, (priority, sequence_number, command))
            self.counter += 1
            
            # Create job info for tracking
            from tui_controller import JobInfo
            job_info = JobInfo(command, sequence_number)
            self.job_info_map[sequence_number] = job_info
            
            print(f"[Queue] Job #{sequence_number} queued: {self._extract_workload_name(command) or 'Unknown workload'}")

    def get(self):
        with self.lock:
            if self.heap:
                priority, sequence_number, command = heapq.heappop(self.heap)
                print(f"[Queue] Job #{sequence_number} dequeued for execution")
                return command, sequence_number
            return None, None

    def empty(self):
        with self.lock:
            return len(self.heap) == 0

    def get_job_info(self, sequence_number: int):
        """Get job info by sequence number"""
        with self.lock:
            return self.job_info_map.get(sequence_number)

    def get_all_queued_jobs(self):
        """Get all queued jobs info"""
        with self.lock:
            return {seq_num: self.job_info_map[seq_num] 
                   for seq_num in self.job_info_map.keys() 
                   if seq_num not in [job[1] for job in self.heap]}

    def remove_job(self, sequence_number: int):
        """Remove a job from queue"""
        with self.lock:
            # Remove from heap if present
            new_heap = []
            for priority, seq_num, command in self.heap:
                if seq_num != sequence_number:
                    new_heap.append((priority, seq_num, command))
            self.heap = new_heap
            
            # Remove from job info map
            if sequence_number in self.job_info_map:
                self.job_info_map[sequence_number].status = "killed"
                return True
            return False

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
        self.tui_controller = None

    def set_tui_controller(self, tui_controller):
        """Set the TUI controller for this scheduler"""
        self.tui_controller = tui_controller

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
                        response = self._handle_command(cmd)
                        conn.sendall(response.encode())

    def _handle_command(self, command: str) -> str:
        """Handle incoming commands from clients"""
        parts = command.strip().split()
        if not parts:
            return "ERROR: Empty command"
        
        action = parts[0].upper()
        
        if action == "STATUS":
            return self._get_status_response()
        elif action == "KILL":
            if len(parts) < 2:
                return "ERROR: Missing job ID"
            try:
                job_id = int(parts[1])
                if self.kill_job(job_id):
                    return "SUCCESS: Job killed"
                else:
                    return "ERROR: Job not found or already killed"
            except ValueError:
                return "ERROR: Invalid job ID"
        elif action == "PAUSE":
            if len(parts) < 2:
                return "ERROR: Missing job ID"
            try:
                job_id = int(parts[1])
                if self.pause_job(job_id):
                    return "SUCCESS: Job paused"
                else:
                    return "ERROR: Job not found or cannot pause"
            except ValueError:
                return "ERROR: Invalid job ID"
        elif action == "RESUME":
            if len(parts) < 2:
                return "ERROR: Missing job ID"
            try:
                job_id = int(parts[1])
                if self.resume_job(job_id):
                    return "SUCCESS: Job resumed"
                else:
                    return "ERROR: Job not found or cannot resume"
            except ValueError:
                return "ERROR: Invalid job ID"
        else:
            # Assume it's a job command to be queued
            self.job_queue.put(command)
            return "Command queued"

    def _get_status_response(self) -> str:
        """Get status response in JSON format"""
        import json
        
        # Get running jobs
        running_jobs = []
        with self.lock:
            for worker in self.running_jobs:
                if hasattr(worker, 'job_info') and worker.job_info:
                    job_info = worker.job_info
                    running_jobs.append({
                        'id': job_info.id,
                        'workload': job_info.workload_name,
                        'status': job_info.status,
                        'start_time': job_info.start_time.isoformat() if job_info.start_time else None,
                        'progress': job_info.progress
                    })
        
        # Get queued jobs
        queued_jobs = []
        queued_jobs_dict = self.job_queue.get_all_queued_jobs()
        for job_id, job_info in queued_jobs_dict.items():
            queued_jobs.append({
                'id': job_info.id,
                'workload': job_info.workload_name,
                'status': job_info.status
            })
        
        status_data = {
            'running_jobs': running_jobs,
            'queued_jobs': queued_jobs,
            'resource_monitor': {
                'should_pause': self.resource_monitor.should_pause(),
                'can_start_new_job': self.resource_monitor.can_start_new_job()
            }
        }
        
        return json.dumps(status_data)

    def _scheduler_loop(self):
        while True:
            if self.resource_monitor.should_pause() or not self.resource_monitor.can_start_new_job():
                print("[Scheduler] Pausing due to insufficient memory")
                time.sleep(1)
                continue

            if not self.job_queue.empty():
                cmd, sequence_number = self.job_queue.get()
                if cmd:
                    print(f"[Scheduler] Starting job #{sequence_number}")
                    
                    # Get job info and update status
                    job_info = self.job_queue.get_job_info(sequence_number)
                    if job_info:
                        job_info.status = "running"
                        job_info.start_time = time.time()
                    
                    worker = JobWorker(cmd, self.resource_monitor, self.job_queue)
                    worker.job_info = job_info  # Attach job info to worker
                    
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

    def kill_job(self, job_id: int) -> bool:
        """Kill a specific job by ID"""
        # Check running jobs
        with self.lock:
            for i, worker in enumerate(self.running_jobs):
                if hasattr(worker, 'job_info') and worker.job_info and worker.job_info.id == job_id:
                    worker.terminate()
                    worker.job_info.status = "killed"
                    self.running_jobs.pop(i)
                    return True
        
        # Check queued jobs
        return self.job_queue.remove_job(job_id)

    def pause_job(self, job_id: int) -> bool:
        """Pause a specific job by ID"""
        with self.lock:
            for worker in self.running_jobs:
                if hasattr(worker, 'job_info') and worker.job_info and worker.job_info.id == job_id:
                    if hasattr(worker, 'pause'):
                        worker.pause()
                        worker.job_info.status = "paused"
                        return True
        return False

    def resume_job(self, job_id: int) -> bool:
        """Resume a specific job by ID"""
        with self.lock:
            for worker in self.running_jobs:
                if hasattr(worker, 'job_info') and worker.job_info and worker.job_info.id == job_id:
                    if hasattr(worker, 'resume'):
                        worker.resume()
                        worker.job_info.status = "running"
                        return True
        return False

    def get_running_jobs(self):
        """Get all running jobs info"""
        with self.lock:
            running_jobs = {}
            for worker in self.running_jobs:
                if hasattr(worker, 'job_info') and worker.job_info:
                    running_jobs[worker.job_info.id] = worker.job_info
            return running_jobs

    def get_queued_jobs(self):
        """Get all queued jobs info"""
        return self.job_queue.get_all_queued_jobs()


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
