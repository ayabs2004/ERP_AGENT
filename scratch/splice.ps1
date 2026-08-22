$path = "api\orchestrateur_general.py"
$enc = [System.Text.Encoding]::UTF8
$lines = [System.IO.File]::ReadAllLines($path, $enc)
$total = $lines.Length
Write-Host "Total lines before: $total"
# Keep lines 1-773 (0-indexed 0-772) + lines 1958-end (0-indexed 1957-end)
# This deletes the orphaned dead block between them
$before = $lines[0..772]
$after  = $lines[1957..($total - 1)]
$merged = $before + $after
[System.IO.File]::WriteAllLines($path, $merged, $enc)
Write-Host "Done. New line count: $($merged.Length)"
