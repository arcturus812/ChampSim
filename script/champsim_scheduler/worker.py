import subprocess
import threading
import os
import psutil

class JobWorker(threading.Thread):
    def __init__(self, cmd, resource_monitor, job_queue, scheduler=None):
        super().__init__()
        self.cmd = cmd
        self.resource_monitor = resource_monitor
        self.job_queue = job_queue
        self.scheduler = scheduler
        self.process = None
        self.job_info = None

    def run(self):
        try:
            self.process = subprocess.Popen(self.cmd, shell=True, preexec_fn=os.setsid)
            self.resource_monitor.register_job(self.process.pid)
            
            # Wait for process to complete
            return_code = self.process.wait()
            
            # Update job status based on return code
            if self.job_info:
                if return_code == 0:
                    self.job_info.status = "completed"
                else:
                    self.job_info.status = "failed"
                
                # Remove from scheduler's running jobs list
                if self.scheduler:
                    with self.scheduler.lock:
                        if self in self.scheduler.running_jobs:
                            self.scheduler.running_jobs.remove(self)
                            print(f"[Worker] Job #{self.job_info.id} completed and removed from running jobs")
            
        except Exception as e:
            print(f"[Worker] Error running job: {e}")
            if self.job_info:
                self.job_info.status = "failed"
        finally:
            self.resource_monitor.unregister_job(self.process.pid)

    def terminate_and_requeue(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
        
        # Update job status
        if self.job_info:
            self.job_info.status = "killed"
        
        # Requeue the job
        self.job_queue.put(self.cmd)
        
        # Remove from scheduler's running jobs list
        if self.scheduler:
            with self.scheduler.lock:
                if self in self.scheduler.running_jobs:
                    self.scheduler.running_jobs.remove(self)

    def terminate(self):
        """Terminate the job without requeuing"""
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
        
        # Update job status
        if self.job_info:
            self.job_info.status = "killed"
        
        # Remove from scheduler's running jobs list
        if self.scheduler:
            with self.scheduler.lock:
                if self in self.scheduler.running_jobs:
                    self.scheduler.running_jobs.remove(self)
