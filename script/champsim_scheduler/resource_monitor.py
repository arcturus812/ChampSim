import psutil
import time

class ResourceMonitor:
    def __init__(self, pause_threshold=0.10, kill_threshold=0.05):
        self.max_memory = psutil.virtual_memory().total
        self.pause_threshold = pause_threshold
        self.kill_threshold = kill_threshold
        self.jobs = set()
        self.mem = None
        self.min_memory_per_job = 3 * 1024 * 1024 * 1024  # 3GB in bytes
        self.update()  # Initialize self.mem

    def update(self):
        self.mem = psutil.virtual_memory()
        # Print real-time memory usage
        used_gb = self.mem.used / (1024**3)
        available_gb = self.mem.available / (1024**3)
        total_gb = self.max_memory / (1024**3)
        print(f"[Memory Monitor] Used: {used_gb:.1f}GB, Available: {available_gb:.1f}GB, Total: {total_gb:.1f}GB")

    def should_pause(self):
        # Check if available memory is less than pause threshold OR
        # if adding another job (3GB) would exceed available memory
        if self.mem is None:
            return False
        available_memory = self.mem.available
        pause_memory = self.pause_threshold * self.max_memory
        min_required = self.min_memory_per_job
        
        return available_memory < pause_memory or available_memory < min_required

    def should_kill(self):
        if self.mem is None:
            return False
        return self.mem.available < self.kill_threshold * self.max_memory

    def can_start_new_job(self):
        """Check if there's enough memory to start a new job (3GB minimum)"""
        if self.mem is None:
            return False
        return self.mem.available >= self.min_memory_per_job

    def register_job(self, pid):
        self.jobs.add(pid)

    def unregister_job(self, pid):
        self.jobs.discard(pid)
