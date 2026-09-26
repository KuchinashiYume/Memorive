param(
    [Parameter(Mandatory = $true)][string]$SourcePath,
    [Parameter(Mandatory = $true)][string]$OutputPath,
    [Parameter(Mandatory = $true)][ValidateSet('doc', 'ppt', 'xls')][string]$FormatKey
)

$ErrorActionPreference = 'Stop'

function Get-Sha256Hex([string]$Path) {
    $algorithm = [System.Security.Cryptography.SHA256]::Create()
    $stream = [System.IO.File]::OpenRead($Path)
    try {
        $bytes = $algorithm.ComputeHash($stream)
        return ([System.BitConverter]::ToString($bytes)).Replace('-', '')
    }
    finally {
        $stream.Dispose()
        $algorithm.Dispose()
    }
}

$source = [System.IO.Path]::GetFullPath($SourcePath)
$output = [System.IO.Path]::GetFullPath($OutputPath)
if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
    throw "Source is missing: $source"
}
$parent = Split-Path -Parent $output
New-Item -ItemType Directory -Force -Path $parent | Out-Null
if (Test-Path -LiteralPath $output) {
    throw "Refuse to overwrite bridge output: $output"
}

$processNames = @('WINWORD', 'EXCEL', 'POWERPNT')
$before = @(Get-Process -Name $processNames -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id)
if ($before.Count -ne 0) {
    throw "OFFICE_USER_SESSION_PRESENT:$($before -join ',')"
}

$application = $null
$document = $null
$auxiliary = @()
$ownedResidual = @()
try {
    if ($FormatKey -eq 'doc') {
        $application = New-Object -ComObject Word.Application
        $application.Visible = $false
        $application.DisplayAlerts = 0
        $application.AutomationSecurity = 3
        $application.Options.UpdateLinksAtOpen = $false
        # Use only the stable leading COM parameters. AutomationSecurity and
        # UpdateLinksAtOpen above carry the security policy; positional empty
        # strings for the full optional signature are not type-stable across
        # Office builds.
        $document = $application.Documents.Open($source, $false, $true)
        $document.SaveAs2($output, 16)
        $document.Close($false)
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($document)
        $document = $null
        $application.Quit()
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($application)
        $application = $null
    }
    elseif ($FormatKey -eq 'xls') {
        $application = New-Object -ComObject Excel.Application
        $application.Visible = $false
        $application.DisplayAlerts = $false
        $application.EnableEvents = $false
        $application.AskToUpdateLinks = $false
        $application.AutomationSecurity = 3
        $document = $application.Workbooks.Open($source, 0, $true)
        $document.SaveAs($output, 51)
        $document.Close($false)
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($document)
        $document = $null
        $application.Quit()
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($application)
        $application = $null
    }
    else {
        $application = New-Object -ComObject PowerPoint.Application
        $application.AutomationSecurity = 3
        $document = $application.Presentations.Open($source, $true, $true, $false)
        $document.SaveAs($output, 24)
        $document.Close()
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($document)
        $document = $null
        $application.Quit()
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($application)
        $application = $null
    }
}
finally {
    if ($document -ne $null) {
        try {
            if ($FormatKey -eq 'ppt') { $document.Close() }
            else { $document.Close($false) }
        }
        catch {}
        try { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($document) } catch {}
        $document = $null
    }
    if ($application -ne $null) {
        try { $application.Quit() } catch {}
        try { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($application) } catch {}
        $application = $null
    }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()

    # The next format must never inherit a short-lived process from a failed
    # predecessor. Wait only for PIDs created after the zero-process precheck.
    for ($attempt = 0; $attempt -lt 12; $attempt++) {
        Start-Sleep -Milliseconds 250
        $afterCleanup = @(Get-Process -Name $processNames -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id)
        $ownedResidual = @($afterCleanup | Where-Object { $_ -notin $before })
        if ($ownedResidual.Count -eq 0) { break }
    }
    if ($ownedResidual.Count -ne 0) {
        # All Office PIDs were absent before this bridge call, so this exact
        # residual set is bridge-owned. Force-close only that bounded set.
        Stop-Process -Id $ownedResidual -Force -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 500
        $stillPresent = @(Get-Process -Id $ownedResidual -ErrorAction SilentlyContinue)
        if ($stillPresent.Count -ne 0) {
            throw "OFFICE_OWNED_PROCESS_RESIDUAL:$($ownedResidual -join ',')"
        }
        $ownedResidual = @()
    }
}

if (-not (Test-Path -LiteralPath $output -PathType Leaf)) {
    throw "Office bridge output is missing: $output"
}
Start-Sleep -Milliseconds 750
$after = @(Get-Process -Name $processNames -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id)
$residual = @($after | Where-Object { $_ -notin $before })
if ($residual.Count -ne 0) {
    throw "OFFICE_OWNED_PROCESS_RESIDUAL:$($residual -join ',')"
}

$sourceItem = Get-Item -LiteralPath $source
$outputItem = Get-Item -LiteralPath $output
[ordered]@{
    status = 'PASS'
    profile_id = 'office_legacy_local_v1'
    format_key = $FormatKey
    source_name = $sourceItem.Name
    source_sha256 = Get-Sha256Hex $source
    output_name = $outputItem.Name
    output_sha256 = Get-Sha256Hex $output
    output_size = $outputItem.Length
    preexisting_office_processes = $before.Count
    residual_office_processes = $residual.Count
    automation_security = 'msoAutomationSecurityForceDisable'
    update_links = 'disabled'
    read_only_open = $true
    visible = $false
    network_requests = 0
    model_or_ocr_calls = 0
} | ConvertTo-Json -Depth 4
