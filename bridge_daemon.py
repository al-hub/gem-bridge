"""
gem-bridge Daemon Entrypoint
Redirects execution to v2 architecture (daemon_v2.py) with Dispatcher-Executor pattern.
This ensures systemd services pointing to bridge_daemon.py automatically run v2.
"""
import sys
from daemon_v2 import main

if __name__ == "__main__":
    main()
