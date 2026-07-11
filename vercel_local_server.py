import importlib.util
import os
import sys
import traceback
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api"))
sys.path.insert(0, str(ROOT / "fund_manager_tracker"))


class LocalDispatcher(BaseHTTPRequestHandler):
    def do_GET(self):
        self.dispatch()

    def do_POST(self):
        self.dispatch()

    def dispatch(self):
        # Extract path without query parameters
        path = self.path.split("?")[0]
        if path == "/" or path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            with open(ROOT / "index.html", "rb") as f:
                self.wfile.write(f.read())
            return

        # Check if it is an API route
        if path.startswith("/api/"):
            endpoint = path[5:]  # Remove '/api/'
            api_file = ROOT / "api" / f"{endpoint}.py"
            if api_file.exists():
                try:
                    # Hot-reloading module cache to avoid import/execution overhead
                    import time
                    t_start = time.time()
                    if not hasattr(self.server, "_module_cache"):
                        self.server._module_cache = {}
                    
                    mtime = api_file.stat().st_mtime
                    cached = self.server._module_cache.get(api_file)
                    if cached and cached[0] == mtime:
                        print("[LOCAL SERVER] Module Cache HIT")
                        module = cached[1]
                    else:
                        print("[LOCAL SERVER] Module Cache MISS - Importing...")
                        spec = importlib.util.spec_from_file_location(endpoint, str(api_file))
                        module = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(module)
                        self.server._module_cache[api_file] = (mtime, module)
                    print(f"[LOCAL SERVER] Module load took: {time.time() - t_start:.4f}s")

                    # Execute method directly on handler_class bound to self to avoid socket deadlock
                    t_exec = time.time()
                    handler_class = getattr(module, "handler")
                    if self.command == "GET":
                        handler_class.do_GET(self)
                    elif self.command == "POST":
                        handler_class.do_POST(self)
                    print(f"[LOCAL SERVER] Handler execution took: {time.time() - t_exec:.4f}s")
                    return
                except Exception as e:
                    self.send_response(500)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    import json
                    self.wfile.write(
                        json.dumps(
                            {
                                "error": "Local execution failed",
                                "details": str(e),
                                "traceback": traceback.format_exc(),
                            }
                        ).encode("utf-8")
                    )
                    return
            else:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"API Endpoint Not Found")
                return

        # Static file fallback
        try:
            rel_path = path.lstrip("/")
            file_path = ROOT / rel_path
            if file_path.is_file() and (ROOT in file_path.resolve().parents or file_path.resolve().parent == ROOT):
                self.send_response(200)
                if path.endswith(".png"):
                    self.send_header("Content-Type", "image/png")
                elif path.endswith(".jpg") or path.endswith(".jpeg"):
                    self.send_header("Content-Type", "image/jpeg")
                elif path.endswith(".css"):
                    self.send_header("Content-Type", "text/css")
                elif path.endswith(".js"):
                    self.send_header("Content-Type", "application/javascript")
                else:
                    self.send_header("Content-Type", "application/octet-stream")
                self.end_headers()
                with open(file_path, "rb") as f:
                    self.wfile.write(f.read())
                return
        except Exception:
            pass

        # 404 Not Found
        self.send_response(404)
        self.end_headers()
        self.wfile.write(b"404 Not Found")


def _resolve_port(default: int = 8787) -> int:
    """Pick a port via CLI arg, KAIROS_PORT env, then default 8787.

    8787 is used instead of 3000 because 3000 is the most-collided dev port on
    a workstation; choose something distinctive so Kairos never fights another
    Next.js / Vite / Vercel app for the socket.
    """
    if len(sys.argv) > 1:
        try:
            return int(sys.argv[1])
        except ValueError:
            pass
    env_port = os.getenv("KAIROS_PORT")
    if env_port:
        try:
            return int(env_port)
        except ValueError:
            pass
    return default


def run(port: int | None = None) -> None:
    port = port or _resolve_port()
    try:
        httpd = HTTPServer(("", port), LocalDispatcher)
    except OSError as exc:
        # Port collision is the most common local-dev failure mode; surface it
        # clearly so the user doesn't end up debugging fetch errors that are
        # actually "another app is on this port".
        print(f"\n[Kairos] Could not bind to port {port}: {exc}")
        print(f"[Kairos] Another process is already listening on {port}.")
        print(f"[Kairos] Try a different port:  python vercel_local_server.py 9090")
        print(f"[Kairos] Or set KAIROS_PORT=9090 and re-run.\n")
        sys.exit(2)
    url = f"http://localhost:{port}"
    print(f"[Kairos] Local Vercel emulator running at {url}")
    print(f"[Kairos] Open {url} in your browser. Ctrl-C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[Kairos] Stopping local server...")
        httpd.server_close()


if __name__ == "__main__":
    run()
