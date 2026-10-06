import base64
import subprocess

files_to_sync = [
    "Caddyfile",
    "app/config.py",
    "app/main.py",
    "app/routers/auth.py",
    "app/routers/badge.py",
    "app/routers/billing.py",
    "app/routers/inventory.py",
    "app/services/badge_service.py",
    "app/services/payment_service.py",
    "app/templates/index.html",
    "app/templates/badge.svg.j2",
    "app/templates/shield.svg.j2",
]

for file_path in files_to_sync:
    with open(file_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")

    script = f"echo '{b64}' | base64 -d > /opt/opensponsor/{file_path}"
    with open("tmp_sync.sh", "w", encoding="utf-8") as sf:
        sf.write(script)

    print(f"Syncing {file_path} to Azure VM...")
    subprocess.run([
        "az.cmd", "vm", "run-command", "invoke",
        "--resource-group", "rg-opensponsor-prod",
        "--name", "vm-opensponsor",
        "--command-id", "RunShellScript",
        "--scripts", "@tmp_sync.sh"
    ], check=True)

# Restart container to pick up code changes
print("Restarting app & caddy containers on Azure VM...")
subprocess.run([
    "az.cmd", "vm", "run-command", "invoke",
    "--resource-group", "rg-opensponsor-prod",
    "--name", "vm-opensponsor",
    "--command-id", "RunShellScript",
    "--scripts", "docker cp /opt/opensponsor/app opensponsor_app:/app/; docker restart opensponsor_app; docker restart opensponsor_caddy"
], check=True)

print("Sync and restart complete!")
