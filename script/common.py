import os
import sys
import subprocess
import re
import socket

import exp_env

def is_venv_active():
    return hasattr(sys, 'real_prefix') or (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix)

def activate_venv(venv_path=exp_env.VENV_PATH):
    activate_script = os.path.join(venv_path, "bin", "activate")
    activate_command = f". {activate_script} && exec python3 {' '.join(sys.argv)}"
    if not is_venv_active():
        print("venv is not active. Activating...")
        subprocess.run(activate_command, shell=True, executable="/bin/bash")

def sort_by_execution_time(log_dir_path):
    simulation_time_dict = {}
    # get all log files in log_dir_path
    log_files = [os.path.join(log_dir_path, f) for f in os.listdir(log_dir_path) if f.endswith(".log")]
    # find linen that start with "Simulation complete"
    for log_file in log_files:
        with open(log_file, "r") as f:
            # log_file's basename without extension is workload name
            workload_name = os.path.basename(log_file).replace(".log", "")
            for line in f:
                if line.startswith("Simulation complete"):
                    print(line)
                    print(workload_name)
                    # line example "Simulation complete CPU 0 instructions: 100000004 cycles: 53961855 cumulative IPC: 1.853 (Simulation time: 00 hr 14 min 36 sec)"
                    # parse simulation time "00 hr 14 min 36 sec" by using regex
                    simulation_time = re.search(r"Simulation time: (\d+) hr (\d+) min (\d+) sec", line)
                    if simulation_time:
                        simulation_time_seconds = int(simulation_time.group(1)) * 3600 + int(simulation_time.group(2)) * 60 + int(simulation_time.group(3))
                    else:
                        simulation_time_seconds = 0
                    # add to dictionary with key as log_file and value as simulation_time_seconds
                    simulation_time_dict[workload_name] = simulation_time_seconds
    # sort dictionary by value in descending order
    sorted_simulation_time_dict = sorted(simulation_time_dict.items(), key=lambda x: x[1], reverse=True)

    for workload_name, simulation_time_seconds in sorted_simulation_time_dict:
        print(f"{workload_name}: {simulation_time_seconds}")

    with open("simulation_time.txt", "w") as f:
        for workload_name, simulation_time_seconds in sorted_simulation_time_dict:
            f.write(f"{workload_name}: {simulation_time_seconds}\n")

def sort_df_by_execution_time(df):
    # read simulation_time.txt
    with open("simulation_time.txt", "r") as f:
        for line in f:
            #simulation time should be int
            workload_name, simulation_time_seconds = line.split(": ")
            df.loc[df["workload_name"] == workload_name, "simulation_time_seconds"] = int(simulation_time_seconds)
    sorted_df = df.sort_values(by="simulation_time_seconds", ascending=False)
    return sorted_df

import socket

def submit_command(command: str, host: str = 'localhost', port: int = 5555) -> str:
    """
    Submit a command to the job scheduler daemon via socket.

    Args:
        command (str): The shell command to execute.
        host (str): The hostname where the daemon is running. Default is 'localhost'.
        port (int): The port number on which the daemon is listening. Default is 5555.

    Returns:
        str: Response message from the daemon.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.connect((host, port))
            s.sendall(command.encode())
            response = s.recv(4096)

            return response.decode()
    except ConnectionRefusedError:
        return "❌ Connection failed. Is the job scheduler running?"
    except Exception as e:
        return f"❌ Unexpected error: {e}"
