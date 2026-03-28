$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Definition)
Set-Location $ScriptDir

Write-Host "=== Task 1: palindrome prefix/suffix ===" -ForegroundColor Cyan
python orchestrator.py examples/task1_palindrome_prefix_suffix.json --live --render md

Write-Host ""
Write-Host "=== Task 2: grammar S->SaSb ===" -ForegroundColor Cyan
python orchestrator.py examples/task2_grammar_sasb.json --live --render md

Write-Host ""
Write-Host "=== Task 3: regex with backreference ===" -ForegroundColor Cyan
python orchestrator.py examples/task3_regex_backref.json --live --render md
