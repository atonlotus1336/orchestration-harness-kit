param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Arguments)
& python (Join-Path $PSScriptRoot "installer\kit.py") bundle @Arguments
exit $LASTEXITCODE
