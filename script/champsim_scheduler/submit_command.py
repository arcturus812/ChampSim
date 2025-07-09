import socket
import sys

if len(sys.argv) < 2:
    print("Usage: python submit_command.py '<command>'")
    exit(1)

command = sys.argv[1]
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
    s.connect(('localhost', 5555))
    s.sendall(command.encode())
    response = s.recv(4096)
    print(response.decode())
