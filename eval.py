import modal

app = modal.App("dataset-to-plot-evaluator")

image = (
    modal.Image.debian_slim()
    .pip_install("modal")
)

@app.function(image=image)
def move_files_between_volumes(source_volume_name: str, target_volume_name: str, target_dir: str):
    import subprocess

    source_volume = modal.Volume.from_name(source_volume_name)
    target_volume = modal.Volume.from_name(target_volume_name)

    # construct a sandbox to move the files between the two volumes
    sandbox = modal.Sandbox.create(
        image=modal.Image.debian_slim(),
        volumes={"/source": source_volume, "/target": target_volume}
    )

    # create the target path if it doesn't exist
    sandbox.exec("mkdir", "-p", f"{target_dir}")

    cp_cmd = sandbox.exec("cp", "-r", "/source/.", f"/target/{target_dir}")
    cp_cmd.stdout.read()

    sandbox.terminate()

    subprocess.run(["modal", "volume", "delete", source_volume_name, "--yes"]) # delete the volume after moving the files

@app.local_entrypoint()
def test_function():
    from tqdm import tqdm
    import json

    dataset_name = "Rahma 20220324 AS oxidation cells"

    with open("eval_files/eval_list.json", "r") as f:
        eval_list = json.load(f) # list of strings, each string is a user prompt

    plot_fn = modal.Function.from_name("dataset-to-plot", "generate_plot")
    plot_fn_args = [(dataset_name, prompt) for prompt in eval_list]
    volume_names = []

    for vol_name in tqdm(plot_fn.starmap(plot_fn_args), desc="Generating plots"):
        volume_names.append(vol_name)

    eval_volume_name = "dataset-to-plot-eval-results"
    eval_volume = modal.Volume.from_name(eval_volume_name, create_if_missing=True) # just to make sure it exists
    eval_volume.hydrate()

    move_files_args = [(vol_name, eval_volume_name, f"q{i:03d}") for i, vol_name in enumerate(volume_names)]
    for _ in tqdm(move_files_between_volumes.starmap(move_files_args), desc="Moving files to a central volume"):
        pass # just to wait for the function to finish

    print("Done")