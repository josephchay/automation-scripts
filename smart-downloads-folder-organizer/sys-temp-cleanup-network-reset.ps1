# Run as Administrator for full system cleanup
$ErrorActionPreference = "SilentlyContinue"

function Get-FreeSpaceGB {
    return [math]::Round((Get-PSDrive C).Free / 1GB, 2)
}

$before = Get-FreeSpaceGB
Write-Host "Free space before cleanup: $before GB" -ForegroundColor Cyan

# 1. Clear User and Windows Temp folders
$tempPaths = @("$env:TEMP\*", "C:\Windows\Temp\*")
foreach ($path in $tempPaths) {
    Write-Host "Cleaning $path..."
    Remove-Item -Path $path -Recurse -Force
}

# 2. Empty Recycle Bin
Write-Host "Emptying Recycle Bin..."
Clear-RecycleBin -Force

# 3. Flush DNS Cache
Write-Host "Flushing DNS cache..."
Clear-DnsClientCache

$after = Get-FreeSpaceGB
$reclaimed = [math]::Round($after - $before, 2)
Write-Host "Cleanup complete! Free space: $after GB (Reclaimed ~$reclaimed GB)" -ForegroundColor Green
