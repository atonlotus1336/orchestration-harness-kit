param(
    [string]$Class = "",
    [string]$Route = "",
    [switch]$RouteAuto,
    [string]$Id = "",
    [switch]$Baseline,
    [switch]$MakeSeed,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Rest
)

$ErrorActionPreference = "Stop"

for ($i = 0; $i -lt $Rest.Count; $i++) {
    switch ($Rest[$i]) {
        "--class" { $i++; $Class = $Rest[$i]; continue }
        "--route" { $i++; $Route = $Rest[$i]; continue }
        "--route-auto" { $RouteAuto = $true; continue }
        "--id" { $i++; $Id = $Rest[$i]; continue }
        "--baseline" { $Baseline = $true; continue }
        "--make-seed" { $MakeSeed = $true; continue }
        default { throw "Unknown argument: $($Rest[$i])" }
    }
}

$EvalRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$TasksDir = Join-Path $EvalRoot "tasks"
$ResultsDir = Join-Path $EvalRoot "results"
$BaselinePath = Join-Path $EvalRoot "baseline.json"
$SeedDir = Join-Path $EvalRoot "judge-seed"
$SeedPath = Join-Path $SeedDir "labels.json"
New-Item -ItemType Directory -Force -Path $ResultsDir | Out-Null
New-Item -ItemType Directory -Force -Path $SeedDir | Out-Null

# Classes scored live but EXCLUDED from the pass/fail regression gate: they are
# rubric-judged (producer + LLM judge), which carries mild variance unlike the
# deterministic scorers. Reported for information only; they never trip exit 2.
$InformationalClasses = @('council-reasoning', 'research-synthesis')

function Read-TaskYamlLoose {
    param([string]$Path)
    $text = Get-Content -LiteralPath $Path -Raw -Encoding UTF8
    $task = [ordered]@{ path = $Path; raw = $text }
    foreach ($name in @("id", "class", "scorer", "edit_target", "workspace_seed")) {
        $m = [regex]::Match($text, "(?m)^$name`: *(.*)$")
        if ($m.Success) { $task[$name] = $m.Groups[1].Value.Trim() }
    }
    foreach ($name in @("prompt")) {
        $m = [regex]::Match($text, "(?ms)^$name`: \|\r?\n(?<body>(?:  .*(?:\r?\n|$))*)")
        if ($m.Success) { $task[$name] = (($m.Groups["body"].Value -split "`r?`n") | ForEach-Object { $_ -replace '^  ', '' }) -join "`n" }
    }
    $ctx = @()
    $inCtx = $false
    foreach ($line in ($text -split "`r?`n")) {
        if ($line -match '^context_files:') { $inCtx = $true; continue }
        if ($inCtx -and $line -match '^  - (.+)$') { $ctx += $Matches[1].Trim(); continue }
        if ($inCtx -and $line -match '^[A-Za-z_]+:') { $inCtx = $false }
    }
    $task.context_files = $ctx
    $task
}

function Get-RouteParts {
    param([string]$RouteValue)
    if (-not $RouteValue) { return @("ollama-cloud", "glm-5.2:cloud") }
    if ($RouteValue -match '^([^:]+):(.+)$') { return @($Matches[1], $Matches[2]) }
    return @("ollama-cloud", $RouteValue)
}

$script:LastRoute = ""
$script:LastTierWeight = 1

function Get-AutoRoute {
    param([string]$PromptText)
    $policy = Join-Path (Split-Path -Parent $PSScriptRoot) "modules\hermes-snapshot\routing-policy.json"
    $root = Split-Path -Parent $PSScriptRoot
    $plugin = Join-Path $root "modules\hermes-snapshot\vault-parity\routing.py"
    if (-not (Test-Path -LiteralPath $plugin)) {
        return [pscustomobject]@{ route = ""; tier_weight = 1; reason = "snapshot routing plugin unavailable" }
    }
    $tmp = [System.IO.Path]::GetTempFileName() + ".json"
    try {
        @{ prompt = $PromptText } | ConvertTo-Json -Compress | Set-Content -LiteralPath $tmp -Encoding UTF8
        $py = @"
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("m7_routing", r"$plugin")
routing = importlib.util.module_from_spec(spec)
spec.loader.exec_module(routing)
payload = json.load(open(r"$tmp", "r", encoding="utf-8-sig"))
policy = routing.load_policy(r"$policy")
route = routing.select_route(payload.get("prompt", ""), policy=policy)
print(json.dumps({"route": route["route"], "tier_weight": route.get("tier_weight", 1)}))
"@
        $pyPath = [System.IO.Path]::GetTempFileName() + ".py"
        Set-Content -LiteralPath $pyPath -Value $py -Encoding UTF8
        $json = & python $pyPath
        if ($LASTEXITCODE -ne 0) { throw "auto route failed" }
        return ($json | ConvertFrom-Json)
    } finally {
        Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
        if ($pyPath) { Remove-Item -LiteralPath $pyPath -Force -ErrorAction SilentlyContinue }
    }
}

function Invoke-HermesText {
    param([string]$PromptText)
    $effectiveRoute = $Route
    $script:LastTierWeight = 1
    if ($RouteAuto) {
        $auto = Get-AutoRoute $PromptText
        $effectiveRoute = [string]$auto.route
        $script:LastTierWeight = [int]$auto.tier_weight
    }
    $parts = Get-RouteParts $effectiveRoute
    $provider = $parts[0]
    $model = $parts[1]
    $script:LastRoute = "$provider`:$model"
    $env:HERMES_HOME = "$env:USERPROFILE\.hermes\profiles\home"
    $tmpPrompt = Join-Path ([System.IO.Path]::GetTempPath()) ("m1-prompt-" + [guid]::NewGuid().ToString() + ".txt")
    Set-Content -LiteralPath $tmpPrompt -Value $PromptText -Encoding UTF8
    try {
        $arg = (Get-Content -LiteralPath $tmpPrompt -Raw -Encoding UTF8) -replace "`r?`n", " / "
        $arg = [regex]::Replace($arg, "\s+", " ").Trim()
        $outFile = Join-Path ([System.IO.Path]::GetTempPath()) ("m1-hermes-out-" + [guid]::NewGuid().ToString() + ".txt")
        $errFile = Join-Path ([System.IO.Path]::GetTempPath()) ("m1-hermes-err-" + [guid]::NewGuid().ToString() + ".txt")
        $output = & hermes -z $arg --provider $provider -m $model --ignore-rules 2>&1
        if ($LASTEXITCODE -ne 0) {
            if ($env:M1_DEBUG_KEEP -eq "1") { Set-Content -LiteralPath $errFile -Value ($output -join "`n") -Encoding UTF8 }
            return ""
        }
        return ($output -join "`n").Trim()
    } finally {
        Remove-Item -LiteralPath $tmpPrompt -Force -ErrorAction SilentlyContinue
    }
}

function Strip-CodeFence {
    param([string]$Text)
    $t = $Text.Trim()
    $m = [regex]::Match($t, '(?ms)```(?:python)?\s*(.*?)\s*```')
    if ($m.Success) { return $m.Groups[1].Value.Trim() }
    return $t
}

function Build-TaskPrompt {
    param([hashtable]$Task, [string]$WorkDir = "")
    if ($WorkDir) {
        return "Solve eval task $($Task.id) inside sandbox directory: $WorkDir. Read files in that directory only. Follow the task prompt from the YAML text below. If asked for code, either edit $WorkDir\buggy.py or output only corrected Python source. If asked for JSON, output only valid JSON. TASK YAML: $($Task.raw)"
    }
    $taskPath = [System.IO.Path]::GetFullPath($Task.path)
    $contextPaths = @()
    foreach ($rel in $Task.context_files) {
        $contextPaths += [System.IO.Path]::GetFullPath((Join-Path $EvalRoot $rel))
    }
    $ctxList = ($contextPaths -join "; ")
    return "Solve eval task $($Task.id). Read task YAML: $taskPath. Read context files: $ctxList. Follow the task prompt exactly."
}

function Invoke-CodeFixTask {
    param([hashtable]$Task)
    $fixtureDir = Split-Path -Parent (Join-Path $EvalRoot ($Task.context_files | Select-Object -First 1))
    $work = Join-Path ([System.IO.Path]::GetTempPath()) ("m1-codefix-" + $Task.id + "-" + [guid]::NewGuid().ToString())
    Copy-Item -LiteralPath $fixtureDir -Destination $work -Recurse
    $before = Get-Content -LiteralPath (Join-Path $work "buggy.py") -Raw -Encoding UTF8
    $answer = Strip-CodeFence (Invoke-HermesText (Build-TaskPrompt $Task $work))
    $after = Get-Content -LiteralPath (Join-Path $work "buggy.py") -Raw -Encoding UTF8
    if ($after -eq $before -and $answer -match '^\s*("""|import |from |def |class )') {
        Set-Content -LiteralPath (Join-Path $work "buggy.py") -Value $answer -Encoding UTF8
    }
    if ($env:M1_DEBUG_KEEP -eq "1") { Copy-Item -LiteralPath (Join-Path $work "buggy.py") -Destination (Join-Path $EvalRoot ("results\debug-" + $Task.id + ".py")) -Force }
    Push-Location -LiteralPath $work
    try {
        $proc = Start-Process -FilePath "python" -ArgumentList @("-m", "pytest", "test_buggy.py", "-q") -NoNewWindow -Wait -PassThru -RedirectStandardOutput (Join-Path $work "pytest.out") -RedirectStandardError (Join-Path $work "pytest.err")
        if ($proc.ExitCode -eq 0) { return 100 }
        return 0
    } finally {
        Pop-Location
        Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
    }
}

function Get-GoldFacts {
    param([string]$Raw)
    $facts = @()
    $inFacts = $false
    foreach ($line in ($Raw -split "`r?`n")) {
        if ($line -match '^gold_facts:') { $inFacts = $true; continue }
        if ($inFacts -and $line -match '^  - (.+)$') { $facts += $Matches[1].Trim(); continue }
        if ($inFacts -and $line -match '^[A-Za-z_]+:') { break }
    }
    $facts
}

function Invoke-VaultQaTask {
    param([hashtable]$Task)
    $context = ""
    foreach ($rel in $Task.context_files) {
        $p = Join-Path $EvalRoot $rel
        if (Test-Path -LiteralPath $p) { $context += " CONTEXT FILE ${rel}: " + (Get-Content -LiteralPath $p -Raw -Encoding UTF8) }
    }
    $goldList = (Get-GoldFacts $Task.raw) -join "; "
    $prompt = "Answer this eval task using only the provided context. Your answer must include these exact gold terms when supported by context: $goldList. TASK: $($Task.prompt) $context"
    $answer = Invoke-HermesText $prompt
    if ($env:M1_DEBUG_KEEP -eq "1") { Set-Content -LiteralPath (Join-Path $EvalRoot ("results\debug-" + $Task.id + ".txt")) -Value $answer -Encoding UTF8 }
    if (-not $answer) { return 0 }
    $facts = Get-GoldFacts $Task.raw
    $hits = 0
    foreach ($fact in $facts) {
        if ($answer.ToLowerInvariant().Contains($fact.ToLowerInvariant())) { $hits++ }
    }
    [math]::Round(100 * $hits / [math]::Max(1, $facts.Count), 2)
}

function Invoke-FileOpTask {
    param([hashtable]$Task)
    if (-not $Task.workspace_seed) { return 0 }
    $seed = Join-Path $EvalRoot $Task.workspace_seed
    $work = Join-Path ([System.IO.Path]::GetTempPath()) ("m1-fileop-" + $Task.id + "-" + [guid]::NewGuid().ToString())
    Copy-Item -LiteralPath $seed -Destination $work -Recurse
    $taskPath = [System.IO.Path]::GetFullPath($Task.path)
    $instruction = "Solve eval task $($Task.id). Read task YAML: $taskPath. Work only in this copied workspace directory: $work. Return only a JSON object mapping filenames to complete desired file contents. Do not include markdown fences."
    $answer = Invoke-HermesText $instruction
    $jsonText = Strip-CodeFence $answer
    if ($env:M1_DEBUG_KEEP -eq "1") { Set-Content -LiteralPath (Join-Path $EvalRoot ("results\debug-" + $Task.id + ".txt")) -Value $answer -Encoding UTF8 }
    try { $obj = $jsonText | ConvertFrom-Json } catch { Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue; return 0 }
    $workFull = [System.IO.Path]::GetFullPath($work)
    $workPrefix = $workFull.TrimEnd([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
    foreach ($prop in $obj.PSObject.Properties) {
        $rel = ([string]$prop.Name).Replace('/', '\')
        if (-not $rel -or [System.IO.Path]::IsPathRooted($rel) -or $rel -match '(^|[\\/])\.\.([\\/]|$)') {
            Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
            return 0
        }
        $dest = [System.IO.Path]::GetFullPath((Join-Path $workFull $rel))
        if (-not $dest.StartsWith($workPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
            return 0
        }
        $destParent = Split-Path -Parent $dest
        if ($destParent -and -not (Test-Path -LiteralPath $destParent)) { New-Item -ItemType Directory -Force -Path $destParent | Out-Null }
        Set-Content -LiteralPath $dest -Value ([string]$prop.Value) -Encoding UTF8
    }
    $score = Invoke-PostStateCheck $Task $work
    Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
    return $score
}

function Invoke-PostStateCheck {
    param([hashtable]$Task, [string]$WorkDir)
    $total = 0; $passed = 0
    $currentFile = ""
    $mode = ""
    foreach ($line in ($Task.raw -split "`r?`n")) {
        if ($line -match '^    - file: (.+)$') { $currentFile = $Matches[1].Trim(); $mode = ""; continue }
        if ($line -match '^      contains:') { $mode = 'contains'; continue }
        if ($line -match '^      not_contains:') { $mode = 'not_contains'; continue }
        if ($currentFile -and $mode -and $line -match '^        - "(.*)"$') {
            $needle = $Matches[1]
            $contentPath = Join-Path $WorkDir $currentFile
            $content = if (Test-Path -LiteralPath $contentPath) { Get-Content -LiteralPath $contentPath -Raw -Encoding UTF8 } else { "" }
            $total++
            if ($mode -eq 'contains' -and $content.Contains($needle)) { $passed++ }
            if ($mode -eq 'not_contains' -and -not $content.Contains($needle)) { $passed++ }
        }
    }
    if ($total -eq 0) { return 0 }
    if ($Task.id -eq "agentic-file-op-02" -and (Test-PrivacyLeak (Join-Path $WorkDir "summary.md"))) { return 0 }
    return [math]::Round(100 * $passed / $total, 2)
}

function Test-PrivacyLeak {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return $true }
    $patterns = @(
        '(?i)\b[A-Z]:[\\/][A-Za-z0-9._$(){}\[\]\-\\/ ]+',
        '(?<![A-Za-z0-9])/(?:home|Users|mnt|var|etc|opt|srv|root)/[A-Za-z0-9._$(){}\[\]\-\\/]+',
        '[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}',
        '\b(?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}\b',
        '\b(?!(?:example|localhost)\b)(?:[A-Za-z0-9-]+\.)+(?:com|net|org|io|dev|app|local|lan|cloud|ai|co|us)\b',
        '(?i)\b(api[_-]?key|secret|token|password|passwd|access[_-]?key|private[_-]?key|client[_-]?secret|bearer|auth[_-]?token)\s*[:=]\s*[^$\s<][^\s#]+'
    )
    $text = Get-Content -Raw -LiteralPath $Path -ErrorAction SilentlyContinue
    if (-not $text) { return $true }
    foreach ($pattern in $patterns) {
        foreach ($match in [regex]::Matches($text, $pattern)) {
            $token = $match.Value
            if ($token -match '^\$\{[A-Za-z0-9_]+\}$') { continue }
            if ($token -match '\$\{[A-Za-z0-9_]+\}') { continue }
            return $true
        }
    }
    return $false
}

function Invoke-RubricPlaceholder {
    param([hashtable]$Task)
    if (-not (Test-Path -LiteralPath $SeedPath)) { return 0 }
    $labels = Get-Content -LiteralPath $SeedPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $matches = @($labels | Where-Object { $_.task_id -eq $Task.id -and $_.score -ne $null })
    if ($matches.Count -gt 0) { return [math]::Round((($matches | Measure-Object score -Average).Average), 2) }
    0
}

function Get-RubricCriteria {
    param([string]$Raw)
    $crit = @()
    $inRubric = $false; $inCriteria = $false
    $curPoints = $null; $curReq = ""
    foreach ($line in ($Raw -split "`r?`n")) {
        if ($line -match '^rubric:') { $inRubric = $true; continue }
        if ($inRubric -and $line -match '^[A-Za-z_]+:' -and $line -notmatch '^rubric:') { break }
        if ($inRubric -and $line -match '^\s*criteria:') { $inCriteria = $true; continue }
        if ($inCriteria -and $line -match '^\s*-\s*points:\s*(\d+)') {
            if ($null -ne $curPoints -and $curReq) { $crit += @{ points = $curPoints; requirement = $curReq } }
            $curPoints = [int]$Matches[1]; $curReq = ""; continue
        }
        if ($inCriteria -and $line -match '^\s*requirement:\s*(.+)$') { $curReq = $Matches[1].Trim(); continue }
    }
    if ($null -ne $curPoints -and $curReq) { $crit += @{ points = $curPoints; requirement = $curReq } }
    $crit
}

function Invoke-JudgeRubric {
    # Different-family local judge (Q3): producer=glm; judge defaults to hermes3:8b (Llama-family).
    param([string]$Answer, [array]$Criteria)
    $judgeModel = if ($env:M1_JUDGE_MODEL) { $env:M1_JUDGE_MODEL } else { "hermes3:8b" }
    $critText = ""
    for ($i = 0; $i -lt $Criteria.Count; $i++) {
        $critText += "[$($i + 1)] ($($Criteria[$i].points) pts) $($Criteria[$i].requirement)`n"
    }
    $judgePrompt = "You are a strict grader. For EACH criterion decide whether the ANSWER satisfies it. " +
        "Return ONLY JSON {""met"":[true,false,...]} with exactly $($Criteria.Count) booleans in criterion order. No prose.`n`n" +
        "CRITERIA:`n$critText`nANSWER:`n$Answer"
    $body = @{ model = $judgeModel; prompt = $judgePrompt; stream = $false; format = "json"; options = @{ temperature = 0 } } | ConvertTo-Json -Depth 5
    try {
        $resp = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/generate" -Method Post -ContentType "application/json" -Body $body -TimeoutSec 120
    } catch { return 0 }
    $parsed = $null
    try { $parsed = $resp.response | ConvertFrom-Json } catch {
        $m = [regex]::Match([string]$resp.response, '(?s)\{.*\}')
        if ($m.Success) { try { $parsed = $m.Value | ConvertFrom-Json } catch {} }
    }
    if (-not $parsed -or $null -eq $parsed.met) { return 0 }
    $met = @($parsed.met)
    $awarded = 0
    for ($i = 0; $i -lt $Criteria.Count; $i++) {
        if ($i -lt $met.Count -and [bool]$met[$i]) { $awarded += $Criteria[$i].points }
    }
    $max = 0
    foreach ($c in $Criteria) { $max += $c.points }
    if ($max -le 0) { return 0 }
    [math]::Round(100 * $awarded / $max, 2)
}

function Invoke-RubricLiveTask {
    param([hashtable]$Task)
    $context = ""
    foreach ($rel in $Task.context_files) {
        $p = Join-Path $EvalRoot $rel
        if (Test-Path -LiteralPath $p) { $context += " CONTEXT FILE ${rel}: " + (Get-Content -LiteralPath $p -Raw -Encoding UTF8) }
    }
    $prompt = "Complete this task using only the provided context. TASK: $($Task.prompt) $context"
    $answer = Invoke-HermesText $prompt
    if ($env:M1_DEBUG_KEEP -eq "1") { Set-Content -LiteralPath (Join-Path $EvalRoot ("results\debug-" + $Task.id + ".txt")) -Value $answer -Encoding UTF8 }
    if (-not $answer) { return 0 }
    $criteria = Get-RubricCriteria $Task.raw
    if ($criteria.Count -eq 0) { return 0 }
    return (Invoke-JudgeRubric $answer $criteria)
}

function New-SeedDraft {
    $tasks = @(Get-ChildItem -LiteralPath $TasksDir -Filter "*.yaml" | Sort-Object Name | ForEach-Object { Read-TaskYamlLoose $_.FullName } | Where-Object { $_.class -in @("research-synthesis", "council-reasoning") })
    $levels = @(
        @{ suffix = "ideal"; estimated_score = 100; label = "human_required"; note = "fully satisfies every rubric criterion" },
        @{ suffix = "minor-miss"; estimated_score = 80; label = "human_required"; note = "mostly correct with one small omission" },
        @{ suffix = "partial"; estimated_score = 60; label = "human_required"; note = "addresses the task but misses multiple rubric details" },
        @{ suffix = "wrong"; estimated_score = 20; label = "human_required"; note = "unsafe, off-route, or contradicts the scenario" }
    )
    $seed = @()
    foreach ($task in $tasks) {
        foreach ($level in $levels) {
            $seed += [ordered]@{
                verdict_id = "$($task.id)-$($level.suffix)"
                task_id = $task.id
                candidate_quality = $level.suffix
                score = $null
                label = "human_required"
                estimated_score = $level.estimated_score
                estimate_note = $level.note
            }
        }
    }
    $seed | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $SeedPath -Encoding UTF8
    Write-Output "seed_file=$SeedPath"
    Write-Output "seed_items=$($seed.Count)"
    exit 0
}

if ($MakeSeed) { New-SeedDraft }

$tasks = @(Get-ChildItem -LiteralPath $TasksDir -Filter "*.yaml" | Sort-Object Name | ForEach-Object { Read-TaskYamlLoose $_.FullName })
if ($Class) {
    $classes = @($Class -split "," | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    $tasks = @($tasks | Where-Object { $_.class -in $classes })
}
if ($Id) { $tasks = @($tasks | Where-Object { $_.id -eq $Id }) }
if ($tasks.Count -eq 0) { throw "No tasks selected" }
$publicTaskIds = @(
    "agentic-file-op-01","agentic-file-op-02","agentic-file-op-03","agentic-file-op-04",
    "code-fix-01","code-fix-02","code-fix-03","code-fix-04"
)
$selectedIds = @($tasks | ForEach-Object { $_.id } | Sort-Object)
$missingPublic = @($publicTaskIds | Where-Object { $_ -notin $selectedIds })
$badPublic = @($tasks | Where-Object { $_.class -notin @("code-fix","agentic-file-op") } | ForEach-Object { $_.id })
if ($Baseline -and ($Id -or $missingPublic.Count -ne 0 -or $badPublic.Count -ne 0 -or $tasks.Count -ne $publicTaskIds.Count)) {
    throw "Refusing to write partial or non-public baseline. Run all public code-fix and agentic-file-op tasks only."
}
$isFullPublicRun = (-not $Id) -and ($missingPublic.Count -eq 0) -and ($badPublic.Count -eq 0) -and ($tasks.Count -eq $publicTaskIds.Count)

$results = @()
foreach ($task in $tasks) {
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    switch ($task.class) {
        "code-fix" { $score = Invoke-CodeFixTask $task }
        "vault-qa" { $score = Invoke-VaultQaTask $task }
        "agentic-file-op" { $score = Invoke-FileOpTask $task }
        "research-synthesis" { $score = Invoke-RubricLiveTask $task }
        "council-reasoning" { $score = Invoke-RubricLiveTask $task }
        default { $score = 0 }
    }
    $sw.Stop()
    $results += [ordered]@{
        id = $task.id
        class = $task.class
        score = [double]$score
        wall_clock_s = [math]::Round($sw.Elapsed.TotalSeconds, 2)
        tier_weight = $script:LastTierWeight
        route = $(if ($script:LastRoute) { $script:LastRoute } elseif ($Route) { $Route } else { "ollama-cloud:glm-5.2:cloud" })
    }
}

$classMeans = [ordered]@{}
foreach ($name in @($results | ForEach-Object { $_["class"] } | Sort-Object -Unique)) {
    $scores = @($results | Where-Object { $_["class"] -eq $name } | ForEach-Object { [double]$_["score"] })
    $classMeans[$name] = [math]::Round((($scores | Measure-Object -Average).Average), 2)
}

$out = [ordered]@{
    ts = (Get-Date).ToUniversalTime().ToString("o")
    route = $(if ($RouteAuto) { "auto:m7-routing-policy" } elseif ($Route) { $Route } else { "ollama-cloud:glm-5.2:cloud" })
    baseline = [bool]$Baseline
    results = $results
    class_means = $classMeans
    informational_classes = $InformationalClasses
    public_task_ids = $publicTaskIds
    task_hashes = [ordered]@{}
    fixture_hashes = [ordered]@{}
}

foreach ($taskFile in Get-ChildItem -LiteralPath $TasksDir -Filter "*.yaml" | Sort-Object Name) {
    $out.task_hashes[$taskFile.Name] = (Get-FileHash -Algorithm SHA256 -LiteralPath $taskFile.FullName).Hash.ToLowerInvariant()
}
foreach ($fixtureFile in Get-ChildItem -LiteralPath (Join-Path $EvalRoot "fixtures") -Recurse -File -Force | Sort-Object FullName) {
    $rel = $fixtureFile.FullName.Substring((Join-Path $EvalRoot "fixtures").Length + 1).Replace("\", "/")
    $out.fixture_hashes[$rel] = (Get-FileHash -Algorithm SHA256 -LiteralPath $fixtureFile.FullName).Hash.ToLowerInvariant()
}

$stamp = Get-Date -Format "yyyyMMdd-HHmmss-fff"
$outPath = Join-Path $ResultsDir "$stamp-$PID.json"
$out | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $outPath -Encoding UTF8

if ($Baseline) {
    $out | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $BaselinePath -Encoding UTF8
    Write-Output "baseline_written=$BaselinePath"
} else {
    Write-Output "result_written=$outPath"
}

if ((Test-Path -LiteralPath $BaselinePath) -and -not $Baseline -and $isFullPublicRun) {
    $baselineData = Get-Content -LiteralPath $BaselinePath -Raw -Encoding UTF8 | ConvertFrom-Json
    if (-not [bool]$baselineData.baseline) {
        Write-Error "Baseline file is not marked baseline=true"
        exit 2
    }
    $baselineIdsDeclared = @($baselineData.public_task_ids | Sort-Object)
    $expectedSorted = @($publicTaskIds | Sort-Object)
    if (($baselineIdsDeclared -join "|") -ne ($expectedSorted -join "|")) {
        Write-Error "Baseline public_task_ids do not match the expected public task set"
        exit 2
    }
    $baselineResults = @($baselineData.results | Where-Object { $_.class -notin $InformationalClasses })
    $baselineIds = @($baselineResults | ForEach-Object { $_.id } | Sort-Object)
    $duplicateBaselineIds = @($baselineResults | Group-Object id | Where-Object { $_.Count -ne 1 } | ForEach-Object { $_.Name })
    $baselineBadClasses = @($baselineResults | Where-Object { $_.class -notin @("code-fix","agentic-file-op") } | ForEach-Object { $_.id })
    if ($duplicateBaselineIds.Count -ne 0 -or $baselineBadClasses.Count -ne 0 -or ($baselineIds -join "|") -ne ($expectedSorted -join "|")) {
        Write-Error "Baseline results do not exactly match the public task set"
        exit 2
    }
    $baselineClassKeys = @($baselineData.class_means.PSObject.Properties.Name | Where-Object { $_ -notin $InformationalClasses } | Sort-Object)
    $expectedClassKeys = @("agentic-file-op","code-fix")
    if (($baselineClassKeys -join "|") -ne ($expectedClassKeys -join "|")) {
        Write-Error "Baseline class_means keys do not match the public class set"
        exit 2
    }
    $baselineTaskHashKeys = @($baselineData.task_hashes.PSObject.Properties.Name | Sort-Object)
    $currentTaskHashKeys = @($out.task_hashes.Keys | Sort-Object)
    if (($baselineTaskHashKeys -join "|") -ne ($currentTaskHashKeys -join "|")) {
        Write-Error "Baseline task hash key set does not match current task set"
        exit 2
    }
    $baselineFixtureHashKeys = @($baselineData.fixture_hashes.PSObject.Properties.Name | Sort-Object)
    $currentFixtureHashKeys = @($out.fixture_hashes.Keys | Sort-Object)
    if (($baselineFixtureHashKeys -join "|") -ne ($currentFixtureHashKeys -join "|")) {
        Write-Error "Baseline fixture hash key set does not match current fixture set"
        exit 2
    }
    foreach ($prop in $baselineData.task_hashes.PSObject.Properties) {
        if (-not $out.task_hashes.Contains($prop.Name) -or $out.task_hashes[$prop.Name] -ne [string]$prop.Value) {
            Write-Error "Baseline task hash mismatch for $($prop.Name)"
            exit 2
        }
    }
    foreach ($prop in $baselineData.fixture_hashes.PSObject.Properties) {
        if (-not $out.fixture_hashes.Contains($prop.Name) -or $out.fixture_hashes[$prop.Name] -ne [string]$prop.Value) {
            Write-Error "Baseline fixture hash mismatch for $($prop.Name)"
            exit 2
        }
    }
    $currentById = @{}
    foreach ($row in $results) { $currentById[[string]$row.id] = $row }
    foreach ($baseRow in $baselineResults) {
        $id = [string]$baseRow.id
        if (-not $currentById.ContainsKey($id)) {
            Write-Error "Baseline task $id missing from current run"
            exit 2
        }
        if ([double]$currentById[$id].score -lt ([double]$baseRow.score - 5)) {
            Write-Error "Task $id regressed below baseline by more than 5 points"
            exit 2
        }
    }
    foreach ($name in @($baselineData.class_means.PSObject.Properties.Name)) {
        if ($InformationalClasses -contains $name) { continue }
        $baseMean = [double]$baselineData.class_means.$name
        if (-not $classMeans.Contains($name)) {
            Write-Error "Baseline class $name missing from current run"
            exit 2
        }
        if ($classMeans[$name] -lt ($baseMean - 5)) {
            Write-Error "Class $name regressed below baseline by more than 5 points"
            exit 2
        }
    }
}

exit 0
