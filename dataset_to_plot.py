import modal
from datetime import datetime
import uuid
import shutil
import os

app = modal.App("dataset-to-plot")

upload_dataset_image = (
    modal.Image.debian_slim()
    .add_local_dir("datasets", remote_path="/local_datasets")
)

function_image = (
    modal.Image.debian_slim()
    .pip_install_from_requirements("sandbox_files/requirements.txt")
    .add_local_python_source("coding_agent")
    .add_local_dir("sandbox_files", remote_path="/root/sandbox_files")
)

sandbox_image = (
    modal.Image.debian_slim()
    .apt_install("ripgrep", "ed")
    .pip_install_from_requirements("/root/sandbox_files/requirements.txt")
    .add_local_file("/root/sandbox_files/apply_patch", "/usr/local/bin/apply_patch", copy=True)
    .run_commands("chmod +x /usr/local/bin/apply_patch")
)

dataset_volume = modal.Volume.from_name("datasets", create_if_missing=True)

def assign_volume_name():
    session_id = datetime.now().strftime('%Y%m%d_%H%M%S_') + str(uuid.uuid4())[:8]
    volume_name = f"temp-dataset-to-plot-volume-{session_id}"
    return volume_name

def get_agent_command(user_prompt: str):
    return f"""
    You are a helpful assistant that generates a command to generate a plot from a dataset.
    The user has provided a dataset located at /workspace/dataset.
    You will generate a python script at /workspace/plot.py that will generate a plot from the dataset.
    The plot will be saved as /workspace/plot.png.

    The user has provided the following prompt:
    {user_prompt}
    """

@app.function(image=upload_dataset_image, volumes={"/remote_datasets": dataset_volume})
def populate_datasets_from_local_dir():
    # copy the local directory to the dataset volume
    shutil.copytree("/local_datasets", "/remote_datasets", dirs_exist_ok=True)
    return "Dataset uploaded successfully"

@app.function(image=function_image, volumes={"/datasets": dataset_volume}, secrets=[modal.Secret.from_name("openai-secret")])
def generate_plot(dataset_name: str, user_prompt: str):
    volume_name = assign_volume_name()
    workspace_volume = modal.Volume.from_name(volume_name, create_if_missing=True)

    with workspace_volume.batch_upload() as batch:
        batch.put_directory(f"/datasets/{dataset_name}", "/dataset")

    sb = modal.Sandbox.create(
        image=sandbox_image,
        volumes={"/workspace": workspace_volume},
    )
    
    coding_agent_prompt = get_agent_command(user_prompt)
    
    from coding_agent.coding_agent import run_coding_agent
    result = run_coding_agent(coding_agent_prompt, sb, logger="file", file_logger_path="/root/shell_log.jsonl")
    sb.terminate()

    # move the shell log to the workspace volume
    with workspace_volume.batch_upload() as batch:
        batch.put_file("/root/shell_log.jsonl", "shell_log.jsonl")

    return volume_name

@app.local_entrypoint()
def main():
    dataset_name = "Rahma 20220324 AS oxidation cells"
    user_prompt = "Show a bar chart of OD600 for Poxy-sfGFP cells with 100 uM acetosyringone, no data normalization or blanking."
    generate_plot.remote(dataset_name, user_prompt)