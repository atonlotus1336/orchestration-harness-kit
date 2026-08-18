param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Arguments)
& python (Join-Path $PSScriptRoot "kit.py") configure @Arguments
exit $LASTEXITCODE
