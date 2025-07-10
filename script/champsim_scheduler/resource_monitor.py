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
        self.scheduler = None  # Reference to scheduler for job counts
        self.update()  # Initialize self.mem

    def set_scheduler(self, scheduler):
        """Set reference to scheduler for job count access"""
        self.scheduler = scheduler

    def update(self):
        self.mem = psutil.virtual_memory()
        # Print real-time memory usage with job counts
        used_gb = self.mem.used / (1024**3)
        available_gb = self.mem.available / (1024**3)
        total_gb = self.max_memory / (1024**3)
        
        # Get job counts if scheduler is available
        queued_count = 0
        running_count = 0
        completed_count = 0
        
        if self.scheduler:
            # Get queued jobs count
            queued_jobs = self.scheduler.get_queued_jobs()
            queued_count = len(queued_jobs)
            
            # Get running jobs count
            running_jobs = self.scheduler.get_running_jobs()
            running_count = len(running_jobs)
            
            # Calculate completed jobs (total jobs created - queued - running)
            total_jobs_created = self.scheduler.job_queue.counter
            completed_count = total_jobs_created - queued_count - running_count
            completed_count = max(0, completed_count)  # Ensure non-negative
        
        print(f"[Memory Monitor] Used: {used_gb:.1f}GB, Available: {available_gb:.1f}GB, Total: {total_gb:.1f}GB Jobs: ({queued_count}/{running_count}/{completed_count})")

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
