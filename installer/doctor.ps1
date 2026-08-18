param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Arguments)
& python (Join-Path $PSScriptRoot "kit.py") doctor @Arguments
exit $LASTEXITCODE
