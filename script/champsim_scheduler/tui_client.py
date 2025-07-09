#!/usr/bin/env python3
"""
ChampSim Scheduler TUI Client
Connects to an already running scheduler service
"""

import argparse
import time
import sys
import os
import socket
import json
import threading
from typing import Dict, List, Optional

# Add current directory to path for imports
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.layout import Layout
    from rich.live import Live
    from rich.text import Text
    from rich.align import Align
    from rich import box
    import queue
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False
    print("Warning: rich library not available. Install with: pip install rich")

class JobInfo:
    def __init__(self, job_id: int, workload_name: str, status: str, start_time: str = None, progress: float = 0.0):
        self.id = job_id
        self.workload_name = workload_name
        self.status = status
        self.start_time = start_time
        self.progress = progress

class SchedulerClient:
    def __init__(self, host: str = 'localhost', port: int = 5555):
        self.host = host
        self.port = port
        self.console = Console() if RICH_AVAILABLE else None
        self.running_jobs: Dict[int, JobInfo] = {}
        self.queued_jobs: Dict[int, JobInfo] = {}
        self.command_queue = queue.Queue()
        self.should_exit = False
        self.connected = False
        
    def connect(self) -> bool:
        """Connect to the scheduler service"""
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.connect((self.host, self.port))
            self.connected = True
            self.console.print(f"[green]Connected to scheduler at {self.host}:{self.port}[/green]")
            return True
        except ConnectionRefusedError:
            self.console.print(f"[red]Failed to connect to scheduler at {self.host}:{self.port}[/red]")
            self.console.print("[yellow]Make sure the scheduler service is running[/yellow]")
            return False
        except Exception as e:
            self.console.print(f"[red]Connection error: {e}[/red]")
            return False
    
    def disconnect(self):
        """Disconnect from the scheduler service"""
        if hasattr(self, 'socket'):
            self.socket.close()
        self.connected = False
    
    def send_command(self, command: str) -> Optional[str]:
        """Send a command to the scheduler and get response"""
        if not self.connected:
            return None
        
        try:
            self.socket.sendall(command.encode())
            response = self.socket.recv(4096).decode()
            return response
        except Exception as e:
            self.console.print(f"[red]Communication error: {e}[/red]")
            self.connected = False
            return None
    
    def get_status(self) -> bool:
        """Get current status from scheduler"""
        if not self.connected:
            return False
        
        try:
            # Send status request
            status_cmd = "STATUS"
            response = self.send_command(status_cmd)
            
            if response:
                # Parse response (assuming JSON format)
                try:
                    data = json.loads(response)
                    self.running_jobs = {}
                    self.queued_jobs = {}
                    
                    # Parse running jobs
                    for job_data in data.get('running_jobs', []):
                        job = JobInfo(
                            job_id=job_data['id'],
                            workload_name=job_data['workload'],
                            status=job_data['status'],
                            start_time=job_data.get('start_time'),
                            progress=job_data.get('progress', 0.0)
                        )
                        self.running_jobs[job.id] = job
                    
                    # Parse queued jobs
                    for job_data in data.get('queued_jobs', []):
                        job = JobInfo(
                            job_id=job_data['id'],
                            workload_name=job_data['workload'],
                            status=job_data['status']
                        )
                        self.queued_jobs[job.id] = job
                    
                    return True
                except json.JSONDecodeError:
                    self.console.print("[red]Invalid response format from scheduler[/red]")
                    return False
            else:
                return False
        except Exception as e:
            self.console.print(f"[red]Status request failed: {e}[/red]")
            return False
    
    def kill_job(self, job_id: int) -> bool:
        """Kill a specific job"""
        if not self.connected:
            return False
        
        cmd = f"KILL {job_id}"
        response = self.send_command(cmd)
        return response and "SUCCESS" in response
    
    def kill_jobs_range(self, start_id: int, end_id: int) -> int:
        """Kill jobs in a range"""
        killed_count = 0
        for job_id in range(start_id, end_id + 1):
            if self.kill_job(job_id):
                killed_count += 1
        return killed_count
    
    def pause_job(self, job_id: int) -> bool:
        """Pause a specific job"""
        if not self.connected:
            return False
        
        cmd = f"PAUSE {job_id}"
        response = self.send_command(cmd)
        return response and "SUCCESS" in response
    
    def resume_job(self, job_id: int) -> bool:
        """Resume a specific job"""
        if not self.connected:
            return False
        
        cmd = f"RESUME {job_id}"
        response = self.send_command(cmd)
        return response and "SUCCESS" in response

class TUIClient:
    def __init__(self, host: str = 'localhost', port: int = 5555):
        self.client = SchedulerClient(host, port)
        self.console = self.client.console
        self.command_queue = queue.Queue()
        self.should_exit = False
        
    def start(self):
        """Start the TUI client interface"""
        if not RICH_AVAILABLE:
            self.console.print("[red]rich library is required for TUI interface[/red]")
            self.console.print("Install with: pip install rich")
            return
        
        # Connect to scheduler
        if not self.client.connect():
            return
        
        self.console.clear()
        self.console.print(Panel.fit("ChampSim Scheduler TUI Client", style="bold blue"))
        
        # Start command input thread
        input_thread = threading.Thread(target=self._input_loop, daemon=True)
        input_thread.start()
        
        # Start the live display
        with Live(self._generate_layout(), refresh_per_second=2, screen=True) as live:
            while not self.should_exit:
                try:
                    # Update job information
                    self.client.get_status()
                    
                    # Process commands
                    self._process_commands()
                    
                    # Update display
                    live.update(self._generate_layout())
                    time.sleep(0.5)
                except KeyboardInterrupt:
                    break
        
        self.client.disconnect()
        self.console.print("\n[bold red]TUI Client stopped.[/bold red]")
    
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
                elif command.lower() == 'r' or command.lower() == 'reconnect':
                    self.client.disconnect()
                    if self.client.connect():
                        self.console.print("[green]Reconnected successfully[/green]")
                    else:
                        self.console.print("[red]Reconnection failed[/red]")
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
- [green]q[/green] or [green]quit[/green]: Exit TUI client
- [green]c[/green] or [green]clear[/green]: Clear screen
- [green]r[/green] or [green]reconnect[/green]: Reconnect to scheduler
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
                if self.client.pause_job(job_id):
                    self.console.print(f"[yellow]Paused job #{job_id}[/yellow]")
                else:
                    self.console.print(f"[red]Failed to pause job #{job_id}[/red]")
            except ValueError:
                self.console.print("[red]Error: Invalid job ID[/red]")
        
        elif action == "resume":
            if len(parts) < 2:
                self.console.print("[red]Error: Please specify job ID[/red]")
                return
            
            try:
                job_id = int(parts[1])
                if self.client.resume_job(job_id):
                    self.console.print(f"[green]Resumed job #{job_id}[/green]")
                else:
                    self.console.print(f"[red]Failed to resume job #{job_id}[/red]")
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
            if self.client.kill_job(job_id):
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
    
    def _show_detailed_status(self):
        """Show detailed status information"""
        status_text = f"""
[bold]Connection Status:[/bold]
- Connected: {self.client.connected}
- Host: {self.client.host}:{self.client.port}

[bold]Job Status:[/bold]
- Running Jobs: {len(self.client.running_jobs)}
- Queued Jobs: {len(self.client.queued_jobs)}
- Total Jobs: {len(self.client.running_jobs) + len(self.client.queued_jobs)}
        """
        self.console.print(Panel(status_text, title="Detailed Status", border_style="blue"))
    
    def _generate_layout(self):
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
        
        # Header
        connection_status = "[green]Connected[/green]" if self.client.connected else "[red]Disconnected[/red]"
        header = Panel(
            Align.center(f"[bold blue]ChampSim Scheduler TUI Client[/bold blue] - {connection_status}"),
            border_style="blue"
        )
        
        # Running jobs table
        running_table = self._create_jobs_table(self.client.running_jobs, "Running Jobs", "green")
        running_panel = Panel(running_table, title="Running Jobs", border_style="green")
        
        # Queued jobs table
        queued_table = self._create_jobs_table(self.client.queued_jobs, "Queued Jobs", "yellow")
        queued_panel = Panel(queued_table, title="Queued Jobs", border_style="yellow")
        
        # Footer with help
        footer_text = "[bold]Commands:[/bold] h=help, q=quit, r=reconnect, kill <id>, pause <id>, resume <id>"
        footer = Panel(Align.center(footer_text), border_style="cyan")
        
        layout["header"].update(header)
        layout["running"].update(running_panel)
        layout["queued"].update(queued_panel)
        layout["footer"].update(footer)
        
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
                start_time = job.start_time if job.start_time else "N/A"
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

def main():
    parser = argparse.ArgumentParser(description='ChampSim Scheduler TUI Client')
    parser.add_argument('--host', type=str, default='localhost', 
                       help='Scheduler host address')
    parser.add_argument('--port', type=int, default=5555, 
                       help='Scheduler port number')
    
    args = parser.parse_args()
    
    # Create and start TUI client
    tui_client = TUIClient(args.host, args.port)
    tui_client.start()

if __name__ == "__main__":
    main() 