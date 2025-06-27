import os
import sys
import subprocess

import exp_env

def is_venv_active():
    return hasattr(sys, 'real_prefix') or (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix)

def activate_venv(venv_path=exp_env.VENV_PATH):
    activate_script = os.path.join(venv_path, "bin", "activate")
    activate_command = f". {activate_script} && exec python3 {' '.join(sys.argv)}"
    if not is_venv_active():
        print("venv is not active. Activating...")
        subprocess.run(activate_command, shell=True, executable="/bin/bash")
