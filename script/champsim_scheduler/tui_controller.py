import threading
import time
import re
from typing import List, Dict, Optional, Tuple
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.layout import Layout
from rich.live import Live
from rich.prompt import Prompt
from rich.text import Text
from rich.align import Align
from rich import box
import queue

class JobInfo:
    def __init__(self, command: str, sequence_number: int):
        self.id = sequence_number
        self.command = command
        self.status = "queued"  # queued, running, paused, completed, killed
        self.start_time = None
        self.end_time = None
        self.progress = 0.0
        self.worker = None
        self.workload_name = self._extract_workload_name(command)
    
    def _extract_workload_name(self, command: str) -> str:
        import re
        match = re.search(r'([\w\d]+\.[\w\d_]+-\d+B)\.champsimtrace\.xz', command)
        return match.group(1) if match else "Unknown"
    
    def __str__(self):
        return f"Job #{self.id}: {self.workload_name} ({self.status})"

class TUIController:
    def __init__(self, scheduler):
        self.scheduler = scheduler
        self.console = Console()
        self.running_jobs: Dict[int, JobInfo] = {}
        self.queued_jobs: Dict[int, JobInfo] = {}
        self.command_queue = queue.Queue()
        self.should_exit = False
        
    def start(self):
        """Start the TUI interface"""
        self.console.clear()
        self.console.print(Panel.fit("ChampSim Scheduler TUI Controller", style="bold blue"))
        
        # Start command input thread
        input_thread = threading.Thread(target=self._input_loop, daemon=True)
        input_thread.start()
        
        # Start the live display
        with Live(self._generate_layout(), refresh_per_second=2, screen=True) as live:
            while not self.should_exit:
                try:
                    # Update job information
                    self._update_job_info()
                    
                    # Process commands
                    self._process_commands()
                    
                    # Update display
                    live.update(self._generate_layout())
                    time.sleep(0.5)
                except KeyboardInterrupt:
                    break
        
        self.console.print("\n[bold red]TUI Controller stopped.[/bold red]")
    
    def _input_loop(self):
        """Handle user input in a separate thread"""
        while not self.should_exit:
            try:
                command = input("\nEnter command (h for help): ").strip()
                if command.lower() == 'q' or command.lower() == 'quit':
                    self.should_exit = True
                    break
                elif command.lower() == 'h' or command.lower() == 'help':
                    self._show_help()
                elif command.lower() == 'c' or command.lower() == 'clear':
                    self.console.clear()
                else:
                    self.command_queue.put(command)
            except (EOFError, KeyboardInterrupt):
                self.should_exit = True
                break
    
    def _show_help(self):
        """Show help information"""
        help_text = """
[bold]Available Commands:[/bold]
- [green]h[/green] or [green]help[/green]: Show this help
- [green]q[/green] or [green]quit[/green]: Exit TUI
- [green]c[/green] or [green]clear[/green]: Clear screen
- [green]kill <job_id>[/green]: Kill a specific job
- [green]kill <start>-<end>[/green]: Kill jobs in range (e.g., kill 1-5)
- [green]pause <job_id>[/green]: Pause a specific job
- [green]resume <job_id>[/green]: Resume a paused job
- [green]status[/green]: Show detailed status

[bold]Examples:[/bold]
- kill 3          (kill job #3)
- kill 1-5        (kill jobs #1 through #5)
- pause 2         (pause job #2)
- resume 2        (resume job #2)
        """
        self.console.print(Panel(help_text, title="Help", border_style="green"))
    
    def _process_commands(self):
        """Process commands from the queue"""
        while not self.command_queue.empty():
            command = self.command_queue.get_nowait()
            self._execute_command(command)
    
    def _execute_command(self, command: str):
        """Execute a user command"""
        parts = command.split()
        if not parts:
            return
        
        action = parts[0].lower()
        
        if action == "kill":
            if len(parts) < 2:
                self.console.print("[red]Error: Please specify job ID(s)[/red]")
                return
            
            job_spec = parts[1]
            self._kill_jobs(job_spec)
        
        elif action == "pause":
            if len(parts) < 2:
                self.console.print("[red]Error: Please specify job ID[/red]")
                return
            
            try:
                job_id = int(parts[1])
                self._pause_job(job_id)
            except ValueError:
                self.console.print("[red]Error: Invalid job ID[/red]")
        
        elif action == "resume":
            if len(parts) < 2:
                self.console.print("[red]Error: Please specify job ID[/red]")
                return
            
            try:
                job_id = int(parts[1])
                self._resume_job(job_id)
            except ValueError:
                self.console.print("[red]Error: Invalid job ID[/red]")
        
        elif action == "status":
            self._show_detailed_status()
        
        else:
            self.console.print(f"[red]Unknown command: {action}[/red]")
    
    def _kill_jobs(self, job_spec: str):
        """Kill jobs based on specification (single ID or range)"""
        job_ids = self._parse_job_spec(job_spec)
        if not job_ids:
            self.console.print("[red]Error: Invalid job specification[/red]")
            return
        
        killed_count = 0
        for job_id in job_ids:
            if self._kill_job(job_id):
                killed_count += 1
        
        if killed_count > 0:
            self.console.print(f"[green]Successfully killed {killed_count} job(s)[/green]")
        else:
            self.console.print("[yellow]No jobs were killed[/yellow]")
    
    def _parse_job_spec(self, job_spec: str) -> List[int]:
        """Parse job specification (single ID or range)"""
        if '-' in job_spec:
            # Range specification (e.g., "1-5")
            try:
                start, end = job_spec.split('-')
                start_id = int(start)
                end_id = int(end)
                return list(range(start_id, end_id + 1))
            except ValueError:
                return []
        else:
            # Single ID
            try:
                return [int(job_spec)]
            except ValueError:
                return []
    
    def _update_job_info(self):
        """Update job information from scheduler"""
        # Update running jobs
        self.running_jobs = self.scheduler.get_running_jobs()
        
        # Update queued jobs
        self.queued_jobs = self.scheduler.get_queued_jobs()
    
    def _kill_job(self, job_id: int) -> bool:
        """Kill a specific job"""
        return self.scheduler.kill_job(job_id)
    
    def _pause_job(self, job_id: int):
        """Pause a specific job"""
        if self.scheduler.pause_job(job_id):
            self.console.print(f"[yellow]Paused job #{job_id}[/yellow]")
        else:
            self.console.print(f"[red]Cannot pause job #{job_id}[/red]")
    
    def _resume_job(self, job_id: int):
        """Resume a paused job"""
        if self.scheduler.resume_job(job_id):
            self.console.print(f"[green]Resumed job #{job_id}[/green]")
        else:
            self.console.print(f"[red]Cannot resume job #{job_id}[/red]")
    
    def _show_detailed_status(self):
        """Show detailed status information"""
        # Get core usage
        with self.scheduler.lock:
            current_running_jobs = len(self.scheduler.running_jobs)
        
        status_text = f"""
[bold]System Status:[/bold]
- Running Jobs: {len(self.running_jobs)}
- Queued Jobs: {len(self.queued_jobs)}
- Total Jobs: {len(self.running_jobs) + len(self.queued_jobs)}

[bold]Core Usage:[/bold]
- Current: {current_running_jobs}/{self.scheduler.max_cores}
- Available: {self.scheduler.max_cores - current_running_jobs}

[bold]Resource Monitor:[/bold]
- Should Pause: {self.scheduler.resource_monitor.should_pause()}
- Can Start New Job: {self.scheduler.resource_monitor.can_start_new_job()}
        """
        self.console.print(Panel(status_text, title="Detailed Status", border_style="blue"))
    
    def _generate_layout(self) -> Layout:
        """Generate the main layout for the TUI"""
        layout = Layout()
        
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="body"),
            Layout(name="footer", size=3)
        )
        
        layout["body"].split_row(
            Layout(name="running", ratio=1),
            Layout(name="queued", ratio=1)
        )
        
        # Header with core usage info
        with self.scheduler.lock:
            current_running_jobs = len(self.scheduler.running_jobs)
        
        header_text = f"ChampSim Scheduler - Cores: {current_running_jobs}/{self.scheduler.max_cores}"
        layout["header"].update(Panel(header_text, style="bold blue"))
        
        # Body content
        layout["running"].update(self._create_jobs_table(self.running_jobs, "Running Jobs", "green"))
        layout["queued"].update(self._create_jobs_table(self.queued_jobs, "Queued Jobs", "yellow"))
        
        # Footer with status
        footer_text = f"Memory: {self.scheduler.resource_monitor.mem.used / (1024**3):.1f}GB / {self.scheduler.resource_monitor.max_memory / (1024**3):.1f}GB"
        layout["footer"].update(Panel(footer_text, style="bold cyan"))
        
        return layout
    
    def _create_jobs_table(self, jobs: Dict[int, JobInfo], title: str, color: str) -> Table:
        """Create a table for displaying jobs"""
        table = Table(title=title, box=box.ROUNDED, border_style=color)
        table.add_column("ID", style="bold", width=8)
        table.add_column("Workload", style="cyan", width=30)
        table.add_column("Status", style="magenta", width=12)
        table.add_column("Progress", style="green", width=12)
        table.add_column("Start Time", style="blue", width=20)
        
        if not jobs:
            table.add_row("", "No jobs", "", "", "")
        else:
            for job_id, job in sorted(jobs.items()):
                start_time = job.start_time.strftime("%H:%M:%S") if job.start_time else "N/A"
                progress = f"{job.progress:.1f}%" if job.progress > 0 else "N/A"
                
                status_color = {
                    "running": "green",
                    "paused": "yellow",
                    "completed": "blue",
                    "killed": "red",
                    "queued": "cyan"
                }.get(job.status, "white")
                
                table.add_row(
                    str(job_id),
                    job.workload_name[:28] + "..." if len(job.workload_name) > 30 else job.workload_name,
                    f"[{status_color}]{job.status}[/{status_color}]",
                    progress,
                    start_time
                )
        
        return table 