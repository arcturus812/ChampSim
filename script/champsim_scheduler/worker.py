import subprocess
import threading
import os
import psutil

class JobWorker(threading.Thread):
    def __init__(self, cmd, resource_monitor, job_queue):
        super().__init__()
        self.cmd = cmd
        self.resource_monitor = resource_monitor
        self.job_queue = job_queue
        self.process = None

    def run(self):
        self.process = subprocess.Popen(self.cmd, shell=True, preexec_fn=os.setsid)
        self.resource_monitor.register_job(self.process.pid)
        self.process.wait()
        self.resource_monitor.unregister_job(self.process.pid)

    def terminate_and_requeue(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.job_queue.put(self.cmd)
