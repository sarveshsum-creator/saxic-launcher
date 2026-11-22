# SAXIC Launcher - enable a fixed Windows pagefile (needs administrator).
# Run through enable-pagefile.cmd, which elevates automatically.
$ErrorActionPreference = 'Stop'

$freeGb = (Get-PSDrive C).Free / 1GB
Write-Host ('Free space on C: {0:N1} GB' -f $freeGb)

$size = 8192
if ($freeGb -lt 12) { $size = 4096 }
if ($freeGb -lt 5) {
    Write-Host 'Not enough free disk space for a pagefile (need at least 5 GB).'
    exit 1
}
Write-Host ('Using a {0} MB pagefile on C:.' -f $size)

$cs = Get-CimInstance Win32_ComputerSystem
if ($cs.AutomaticManagedPagefile) {
    Set-CimInstance -InputObject $cs -Property @{AutomaticManagedPagefile = $false }
    Write-Host 'Turned off automatic pagefile management.'
}

$pf = Get-CimInstance Win32_PageFileSetting -ErrorAction SilentlyContinue
if ($pf) {
    Set-CimInstance -InputObject $pf -Property @{InitialSize = $size; MaximumSize = $size }
    Write-Host ('Updated pagefile {0} to {1} MB.' -f $pf.Name, $size)
} else {
    New-CimInstance -ClassName Win32_PageFileSetting -Property @{
        Name = 'C:\pagefile.sys'; InitialSize = $size; MaximumSize = $size
    } | Out-Null
    Write-Host ('Created pagefile C:\pagefile.sys ({0} MB).' -f $size)
}

Write-Host 'Pagefile configured. Restart Windows to activate it.'
exit 0
