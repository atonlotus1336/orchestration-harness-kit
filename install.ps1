param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Arguments)
& python (Join-Path $PSScriptRoot "installer\kit.py") install @Arguments
exit $LASTEXITCODE
