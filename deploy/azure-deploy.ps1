# ==============================================================================
# OpenSponsor 1-Click Azure Deployment Script
# Provisions a low-cost Standard_B1s Linux VM (~$4-$7/mo or free under student tier),
# installs Docker & Caddy, opens ports 80/443, and deploys the platform.
# ==============================================================================

param(
    [string]$ResourceGroup = "rg-opensponsor-prod",
    [string]$Location = "centralus",
    [string]$VmName = "vm-opensponsor",
    [string]$VmSize = "Standard_B2ps_v2",
    [string]$DnsPrefix = "opensponsor-$((Get-Random -Minimum 1000 -Maximum 9999))",
    [string]$AdminUsername = "azureuser"
)

$ErrorActionPreference = "Stop"

Write-Host "========================================================" -ForegroundColor Cyan
Write-Host "  Deploying OpenSponsor to Microsoft Azure (Low-Cost VPS)" -ForegroundColor Cyan
Write-Host "  Resource Group: $ResourceGroup | Location: $Location" -ForegroundColor Gray
Write-Host "  VM Size: $VmSize | DNS Prefix: $DnsPrefix" -ForegroundColor Gray
Write-Host "========================================================" -ForegroundColor Cyan

# 1. Verify Azure Login
Write-Host "[1/5] Checking Azure authentication..." -ForegroundColor Yellow
$account = az account show --output json | ConvertFrom-Json
Write-Host "Authenticated as: $($account.user.name) on subscription: $($account.name)" -ForegroundColor Green

# 2. Create Resource Group
Write-Host "[2/5] Creating resource group '$ResourceGroup' in '$Location'..." -ForegroundColor Yellow
az group create --name $ResourceGroup --location $Location --output table

# 3. Create Cloud-Init Script
$cloudInit = @"
#cloud-config
package_upgrade: true
packages:
  - docker.io
  - docker-compose
  - git
  - curl
runcmd:
  - systemctl enable docker
  - systemctl start docker
  - usermod -aG docker $AdminUsername
  - mkdir -p /opt/opensponsor
"@
$cloudInitFile = [System.IO.Path]::GetTempFileName()
Set-Content -Path $cloudInitFile -Value $cloudInit

# 4. Create Virtual Machine with Public IP and DNS
Write-Host "[3/5] Provisioning low-cost VM '$VmName' ($VmSize)..." -ForegroundColor Yellow
az vm create `
    --resource-group $ResourceGroup `
    --name $VmName `
    --image "Canonical:0001-com-ubuntu-server-jammy:22_04-lts-arm64:latest" `
    --size $VmSize `
    --admin-username $AdminUsername `
    --generate-ssh-keys `
    --public-ip-address-dns-name $DnsPrefix `
    --custom-data $cloudInitFile `
    --output table


Remove-Item -Path $cloudInitFile -Force -ErrorAction SilentlyContinue

# 5. Open HTTP and HTTPS Ports (80 & 443)
Write-Host "[4/5] Opening firewall ports (80 HTTP, 443 HTTPS, 22 SSH)..." -ForegroundColor Yellow
az vm open-port --resource-group $ResourceGroup --name $VmName --port 80 --priority 1010 --output table
az vm open-port --resource-group $ResourceGroup --name $VmName --port 443 --priority 1020 --output table

# 6. Retrieve FQDN and IP
$fqdn = az network public-ip show --resource-group $ResourceGroup --name "${VmName}PublicIP" --query "dnsSettings.fqdn" -o tsv
$ip = az network public-ip show --resource-group $ResourceGroup --name "${VmName}PublicIP" --query "ipAddress" -o tsv

Write-Host "========================================================" -ForegroundColor Green
Write-Host "  Azure VPS Deployment Complete!" -ForegroundColor Green
Write-Host "  Public FQDN: https://$fqdn" -ForegroundColor Cyan
Write-Host "  Public IP:   $ip" -ForegroundColor Cyan
Write-Host "  SSH Access:  ssh $AdminUsername@$fqdn" -ForegroundColor Gray
Write-Host "========================================================" -ForegroundColor Green
Write-Host "Next step: Run 'scp -r * $AdminUsername@${fqdn}:/opt/opensponsor/' and 'docker compose up -d'" -ForegroundColor Yellow
