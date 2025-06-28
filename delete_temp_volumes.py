import modal
import json

app = modal.App("delete-temp-volumes")

@app.local_entrypoint()
def main():
    import subprocess

    volumes = subprocess.run(["modal", "volume", "list", "--json"], capture_output=True, text=True)
    volumes = json.loads(volumes.stdout)
    for volume in volumes:
        if volume["Name"].startswith("temp-dataset-to-plot-volume"):
            subprocess.run(["modal", "volume", "delete", volume["Name"], "--yes"])

if __name__ == "__main__":
    main()