from openai import OpenAI
import subprocess, os
import argparse
import pathlib
import sys
import modal
from .shell_logger import ShellLogger, NullLogger, HTTPEndpointLogger, StdoutLogger, FileLogger

def is_modal_environment():
    """Detect if running inside a Modal sandbox."""
    # Modal sets this environment variable inside sandboxes
    return os.environ.get("MODAL_SANDBOX_ID") is not None

def read_command_from_file(file_path: str) -> str:
    """Read command from a file, handling both relative and absolute paths."""
    path = pathlib.Path(file_path)
    if not path.is_absolute():
        path = pathlib.Path(os.getcwd()) / path
    
    if not path.exists():
        raise FileNotFoundError(f"Command file not found: {file_path}")
        
    with open(path, "r") as f:
        command = f.read().strip()
        if not command:
            raise ValueError(f"Command file is empty: {file_path}")
        return command

def docker_exec(container: str, cmd: list, cwd: str = None, env: dict = None, timeout_ms: int = None) -> subprocess.CompletedProcess:
    """Execute a command in a Docker container.
    
    Args:
        container: Name of the container to execute in
        cmd: Command as a list of strings (e.g. ['ls', '-l'])
        cwd: Working directory in the container
        env: Environment variables to set
        timeout_ms: Timeout in milliseconds
    """
    docker_cmd = ["docker", "exec"]
    
    # Add working directory if specified
    root_dir = "/workspace"
    if cwd:
        cwd = os.path.join(root_dir, cwd)
    else:
        cwd = root_dir
    
    docker_cmd.extend(["-w", cwd])
    
    # Add environment variables if specified
    if env:
        for key, value in env.items():
            docker_cmd.extend(["-e", f"{key}={value}"])
    
    # Add container name and command
    docker_cmd.append(container)
    docker_cmd.extend(cmd)
    
    return subprocess.run(
        docker_cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=(timeout_ms / 1000) if timeout_ms else None,
    )

def modal_exec(sandbox, cmd: list, cwd: str = None, env: dict = None, timeout_ms: int = None):
    """Execute a command in a Modal sandbox.
    
    Args:
        sandbox: Modal sandbox instance to execute in
        cmd: Command as a list of strings (e.g. ['ls', '-l'])
        cwd: Working directory in the sandbox
        env: Environment variables to set (not yet supported in Modal)
        timeout_ms: Timeout in milliseconds (not yet supported in Modal)
    """
    # Set working directory if specified
    if cwd:
        # Execute cd command first, then the actual command
        full_cmd = ["bash", "-c", f"cd {cwd} && {' '.join(cmd)}"]
    else:
        full_cmd = cmd
    
    # Execute the command in the Modal sandbox
    result = sandbox.exec(*full_cmd)
    result.wait()
    
    # Create a CompletedProcess-like object for compatibility
    class CompletedProcess:
        def __init__(self, returncode, stdout, stderr):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr
    
    return CompletedProcess(
        returncode=0,  # Modal doesn't provide return codes yet
        stdout=result.stdout.read() if result.stdout else "",
        stderr=result.stderr.read() if result.stderr else ""
    )

def run_coding_agent(request: str, container_or_sandbox, logger: str = None, use_modal: bool = True, endpoint_url: str = None, file_logger_path: str = None):
    """Executes an autonomous LLM-powered coding agent in a Docker container or Modal sandbox.
    
    The agent processes the request and continues executing commands until it determines
    the task is complete.
    """
    # Conditional default logger
    if logger is None:
        if use_modal or is_modal_environment():
            logger = "file"
        else:
            logger = "stdout"
    if logger == "stdout":
        logger = StdoutLogger()
    elif logger == "file":
        if file_logger_path is None:
            raise ValueError("file_logger_path is required when using --logger file")
        logger = FileLogger(file_path=file_logger_path)
    elif logger == "http":
        if endpoint_url is None:
            raise ValueError("endpoint_url is required when using --logger http")
        logger = HTTPEndpointLogger(endpoint_url=endpoint_url)
    elif logger in ["null", "none", "noop", None]:
        logger = NullLogger()
    else:
        raise ValueError(f"Invalid logger: {logger}")
    
    client = OpenAI()
    responses_log = []

    # Create the initial response request with the tool enabled
    response = client.responses.create(
        model="codex-mini-latest",
        tools=[{"type": "local_shell"}],
        input=[
            {
                "role": "developer",
                "content": [
                    {"type": "input_text", "text": "You are a helpful assistant that can write code and execute shell commands."}
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": request}
                ],
            }
        ],
    )

    responses_log.append(response)

    while True:
        # Look for a local_shell_call in the model's output items
        shell_calls = [item for item in response.output if item.type == "local_shell_call"]
        if not shell_calls:
            # No more commands — the assistant is done.
            break

        call = shell_calls[0]
        call_args = call.action

        # Execute the command in the container or sandbox
        if use_modal:
            completed = modal_exec(
                sandbox=container_or_sandbox,
                cmd=call_args.command,
                cwd=call_args.working_directory,
                env=call_args.env,
                timeout_ms=call_args.timeout_ms
            )
        else:
            completed = docker_exec(
                container=container_or_sandbox,
                cmd=call_args.command,
                cwd=call_args.working_directory,
                env=call_args.env,
                timeout_ms=call_args.timeout_ms
            )

        output_item = {
            "type": "local_shell_call_output",
            "call_id": call.call_id,
            "output": completed.stdout + completed.stderr,
        }

        # log the command and output
        logger.log({
            "command": call_args.command,
            "output": completed.stdout + completed.stderr
        })

        # Send the output back to the model to continue the conversation
        response = client.responses.create(
            model="codex-mini-latest",
            tools=[{"type": "local_shell"}],
            previous_response_id=response.id,
            input=[output_item],
        )

        responses_log.append(response)

    # Print the assistant's final answer
    final_message = next(
        item for item in response.output if item.type == "message" and item.role == "assistant"
    )

    final_text = final_message.content[0].text
    logger.log({
        "type": "final_response",
        "response": final_text
    })

    return final_text

def parse_args():
    parser = argparse.ArgumentParser(description="Execute commands in a Docker container or Modal sandbox using OpenAI.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--command", "-c", type=str, help="Direct command string to execute")
    parser.add_argument("--container", help="Name of the running Docker container (for Docker mode)")
    parser.add_argument("--sandbox", help="Modal sandbox instance (for Modal mode)")
    parser.add_argument("--use-modal", action="store_true", help="Use Modal sandbox instead of Docker container")
    parser.add_argument(
        "--logger", "-l", type=str, choices=["stdout", "null", "file", "http"],
        help="Logger to use (stdout, null, file, http). Default: 'file' in Modal, 'stdout' otherwise.",
        default=None
    )
    return parser.parse_args()

def main():
    args = parse_args()
    
    # Validate arguments
    if args.use_modal and not args.sandbox:
        raise ValueError("--sandbox is required when using --use-modal")
    elif not args.use_modal and not args.container:
        raise ValueError("--container is required when not using --use-modal")
    
    # Get command either directly or from file
    if args.command:
        if not args.command.strip():
            raise ValueError("Command string is empty")
        command = args.command.strip()
    else:
        command = read_command_from_file(args.file)
    
    # Determine the target (container or sandbox)
    target = args.sandbox if args.use_modal else args.container
    
    result = run_coding_agent(command, target, args.logger, args.use_modal)
    print(result)

if __name__ == "__main__":
    main()