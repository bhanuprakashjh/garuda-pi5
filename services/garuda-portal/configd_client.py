import json, socket

def call(op: str, args: dict, sock_path: str = "/run/garuda/configd.sock") -> dict:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(10)
    try:
        s.connect(sock_path)
        s.sendall((json.dumps({"op": op, "args": args}) + "\n").encode())
        return json.loads(s.makefile("r").readline())
    finally:
        s.close()
