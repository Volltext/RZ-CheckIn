<#
.SYNOPSIS
    Baut den Reader-Agenten (Systray-Variante) zu einer einzelnen .exe.

.DESCRIPTION
    Läuft auf dem BUILD-Rechner (braucht einmalig Internetzugang, siehe
    agent/README.md Abschnitt "Air-Gapped: Wheelhouse vorbereiten"), NICHT auf dem
    air-gapped Kiosk-PC. Das Ergebnis ist eine einzelne .exe unter dist\
    RZ-CheckIn-Agent.exe, die auf dem Kiosk-PC ohne Python-Installation und ohne
    Netzwerkzugriff läuft -- alle Abhängigkeiten sind eingebettet.

.PARAMETER Wheelhouse
    Pfad zu einem lokalen Ordner mit vorab heruntergeladenen Wheel-Dateien (siehe
    "pip download" im README). Wird NUR gebraucht, wenn dieser Build-Rechner selbst
    keinen Internetzugang hat; mit Internetzugang einfach weglassen.

.EXAMPLE
    .\build_exe.ps1
    Baut mit Paketen direkt von PyPI (Build-Rechner hat Internetzugang).

.EXAMPLE
    .\build_exe.ps1 -Wheelhouse C:\rz-checkin-wheelhouse
    Baut komplett offline aus einer vorbereiteten Wheelhouse.
#>

param(
    [string]$Wheelhouse = ""
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$venvDir = "build-venv"
if (-not (Test-Path $venvDir)) {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        py -3 -m venv $venvDir
    } elseif (Get-Command python -ErrorAction SilentlyContinue) {
        python -m venv $venvDir
    } else {
        Write-Host ""
        Write-Host "FEHLER: Es wurde weder der 'py'-Launcher noch 'python' gefunden." -ForegroundColor Red
        Write-Host "Auf diesem Build-Rechner ist Python entweder nicht installiert oder nicht im PATH."
        Write-Host ""
        Write-Host "Loesung:"
        Write-Host "  1. Python von https://www.python.org/downloads/ installieren (Version 3.x)."
        Write-Host "  2. Im Installer die Haken bei 'Add python.exe to PATH' UND 'py launcher' setzen."
        Write-Host "  3. Danach dieses PowerShell-Fenster neu oeffnen und build_exe.ps1 erneut ausfuehren."
        throw "Python wurde nicht gefunden."
    }
}
$pip = Join-Path $venvDir "Scripts\pip.exe"
$pyinstaller = Join-Path $venvDir "Scripts\pyinstaller.exe"

$pipArgs = @("install")
if ($Wheelhouse -ne "") {
    Write-Host "Installiere ausschließlich aus Wheelhouse: $Wheelhouse (kein Netzwerkzugriff)"
    $pipArgs += @("--no-index", "--find-links", $Wheelhouse)
} else {
    Write-Host "Installiere von PyPI (Build-Rechner hat Internetzugang)"
}
$pipArgs += @("-r", "requirements.txt", "-r", "requirements-tray.txt")

& $pip @pipArgs

Write-Host "Baue RZ-CheckIn-Agent.exe ..."
& $pyinstaller `
    --noconfirm `
    --onefile `
    --windowed `
    --name "RZ-CheckIn-Agent" `
    --paths . `
    tray_app.py

Write-Host ""
Write-Host "Fertig: dist\RZ-CheckIn-Agent.exe"
Write-Host "Diese eine Datei auf den Kiosk-PC kopieren (z.B. per USB-Stick) -- sie"
Write-Host "enthaelt Python und alle Abhaengigkeiten, es ist dort keine Installation und"
Write-Host "kein Netzwerkzugriff noetig."
