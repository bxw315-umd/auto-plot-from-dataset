from abc import ABC, abstractmethod
import requests
import json
import os

class ShellLogger(ABC):
    """Abstract base class for shell loggers. Subclasses must implement the log method."""
    @abstractmethod
    def log(self, msg: dict): pass

class StdoutLogger(ShellLogger):
    """Logger that prints messages to standard output."""
    def log(self, msg: dict):
        print(msg)

class HTTPEndpointLogger(ShellLogger):
    """Logger that sends messages to an HTTP endpoint."""
    def __init__(self, endpoint_url: str):
        self.endpoint_url = endpoint_url

    def log(self, msg: dict):
        requests.post(self.endpoint_url, json=msg)

class NullLogger(ShellLogger):
    """Logger that does nothing (no-op)."""
    def log(self, msg: dict): pass

class FileLogger(ShellLogger):
    """Logger that writes messages to a file."""
    def __init__(self, file_path: str):
        self.file_path = file_path

    def log(self, msg: dict):
        try:
            # Create directory if it doesn't exist
            directory = os.path.dirname(self.file_path)
            if directory and not os.path.exists(directory):
                os.makedirs(directory, exist_ok=True)

            with open(self.file_path, "a") as f:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")
        except Exception as e:
            print(f"Error writing to log file {self.file_path}: {e}")
            # Fallback to stdout
            print(f"Log message: {msg}")