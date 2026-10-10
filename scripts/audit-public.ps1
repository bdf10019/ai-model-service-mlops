$ErrorActionPreference='Stop'
$taskRoot=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$taskPaths=@(& git -c core.quotePath=false -C $taskRoot diff --cached --name-only --diff-filter=ACM)
if($LASTEXITCODE -ne 0){throw 'Cannot inspect Git index'}
if($taskPaths.Count -eq 0){$taskPaths=@(& git -c core.quotePath=false -C $taskRoot ls-files)}
$taskFindings=@()
foreach($taskRelative in $taskPaths){
 $taskPath=[IO.Path]::GetFullPath((Join-Path $taskRoot $taskRelative))
 if(!$taskPath.StartsWith($taskRoot+[IO.Path]::DirectorySeparatorChar)){throw 'Path outside publication root'}
 if($taskRelative -match '(^|/)(state|tmp|reports|node_modules|\.venv[^/]*|__pycache__)(/|$)' -or $taskRelative -match '(^|/)\.env($|\.)' -or $taskRelative -match '\.(db|sqlite3?|gguf|safetensors|onnx|pt|pth|exe|dll|zip|log|map)$'){$taskFindings+=($taskRelative+': forbidden publication path');continue}
 $taskFile=Get-Item -LiteralPath $taskPath
 if($taskFile.Length -gt 25MB){$taskFindings+=($taskRelative+': exceeds 25 MiB limit')}
 if($taskFile.Attributes -band [IO.FileAttributes]::ReparsePoint){$taskFindings+=($taskRelative+': symlink or reparse point')}
 if($taskFile.Extension -in '.png','.jpg','.jpeg','.woff','.woff2','.glb','.pdf'){continue}
 $taskText=[IO.File]::ReadAllText($taskPath)
 if($taskText -match '(sk-[A-Za-z0-9]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|AIza[0-9A-Za-z_-]{30,}|-----BEGIN (RSA |OPENSSH |EC )?PRIVATE KEY-----)'){$taskFindings+=($taskRelative+': credential-shaped content')}
 if($taskRelative -ne 'scripts/showcase_privacy_check.py' -and $taskText -match 'C:[\\/]+Users[\\/]+'){$taskFindings+=($taskRelative+': private Windows user path')}
}
if($taskFindings.Count){$taskFindings|Write-Output;throw 'Public-release audit failed; no push allowed'}
Write-Output ('Public-release audit passed: '+$taskPaths.Count+' indexed files; binary assets require separate visual and license review.')
