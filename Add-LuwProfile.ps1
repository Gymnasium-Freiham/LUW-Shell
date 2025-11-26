param(
    [string]$ExePath
)

# Pfad zur Windows Terminal settings.json
$settingsPath = "$env:LOCALAPPDATA\Packages\Microsoft.WindowsTerminal_8wekyb3d8bbwe\LocalState\settings.json"

# JSON laden
$json = Get-Content $settingsPath -Raw | ConvertFrom-Json

# Neues Profil erzeugen
$newProfile = @{
    guid = "{b2f5ff47-2f3c-4a4f-9d0a-123456789abc}"   # eigene GUID, einmalig!
    name = "LUW Shell"
    commandline = "`"$ExePath`""
    startingDirectory = "%USERPROFILE%"
}

# Profil anhängen, falls nicht schon vorhanden
if (-not ($json.profiles.list | Where-Object { $_.name -eq "LUW Shell" })) {
    $json.profiles.list += $newProfile
    $json | ConvertTo-Json -Depth 5 | Set-Content $settingsPath -Encoding UTF8
    Write-Output "LUW Shell Profil hinzugefügt."
} else {
    Write-Output "LUW Shell Profil existiert bereits."
}
