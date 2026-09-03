<#
.SYNOPSIS
    Lädt alle Python-Pakete, die für Reader-Agent + .exe-Build gebraucht werden, als
    Wheel-Dateien in einen lokalen Ordner ("Wheelhouse") herunter.

.DESCRIPTION
    NUR auf einem Rechner MIT Internetzugang ausführen (z.B. ein normaler Büro-PC,
    ausdrücklich NICHT der air-gapped Kiosk-PC oder Server). Den erzeugten Ordner
    anschließend per USB-Stick/internem Fileshare auf den Rechner übertragen, der
    tatsächlich baut/installiert (siehe agent/build_exe.ps1 -Wheelhouse, bzw.
    agent/README.md).

    Einmal vorbereitet, deckt die Wheelhouse alle Python-Versionsstände dieses Projekts
    ab -- kein erneuter Internetzugang nötig, solange sich requirements.txt/
    requirements-tray.txt nicht ändern.

.EXAMPLE
    .\prepare_wheelhouse.ps1 -Ziel C:\rz-checkin-wheelhouse
#>

param(
    [string]$Ziel = "wheelhouse"
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

py -3 -m pip download -d $Ziel -r requirements.txt
py -3 -m pip download -d $Ziel -r requirements-tray.txt

Write-Host ""
Write-Host "Fertig: $Ziel enthaelt alle benoetigten Wheel-Dateien."
Write-Host "Ordner auf den Build-/Zielrechner uebertragen, dort z.B.:"
Write-Host "  pip install --no-index --find-links $Ziel -r requirements.txt"
Write-Host "  .\build_exe.ps1 -Wheelhouse $Ziel"
