# Parse the semantic model with Microsoft's Tabular Object Model (TOM), the library Power BI tools use to read
# and write TMDL, and write what it sees to a JSON summary. Needs PowerShell 7 (.NET 8 or later); nothing is
# installed system-wide: the NuGet package is downloaded into .tom/ and its SHA-256 is checked.
#
#   pwsh tools/check_tmdl.ps1 -Out .tom/tom_model.json
#
# Fails (exit 1) when TmdlSerializer cannot deserialize the folder or Model.Validate() reports errors.
param(
    [string]$Definition = (Join-Path $PSScriptRoot '..' 'SGTransport.SemanticModel' 'definition'),
    [string]$Out = (Join-Path $PSScriptRoot '..' '.tom' 'tom_model.json'),
    [string]$PackageDir = (Join-Path $PSScriptRoot '..' '.tom')
)
$ErrorActionPreference = 'Stop'
$version = '19.117.0'
$sha256 = '6b29b095de1c8562b711094433674ea56c13beb1c707e1749db6860a47c6f2b1'

$nupkg = Join-Path $PackageDir "microsoft.analysisservices.$version.nupkg"
$lib = Join-Path $PackageDir "as-$version" 'lib' 'net8.0'
if (-not (Test-Path (Join-Path $lib 'Microsoft.AnalysisServices.Tabular.dll'))) {
    New-Item -ItemType Directory -Force $PackageDir | Out-Null
    Invoke-WebRequest "https://api.nuget.org/v3-flatcontainer/microsoft.analysisservices/$version/microsoft.analysisservices.$version.nupkg" -OutFile $nupkg
    $actual = (Get-FileHash $nupkg -Algorithm SHA256).Hash.ToLower()
    if ($actual -ne $sha256) { throw "SHA-256 mismatch for $nupkg : $actual" }
    Expand-Archive $nupkg -DestinationPath (Join-Path $PackageDir "as-$version") -Force
}
Add-Type -Path (Join-Path $lib 'Microsoft.AnalysisServices.Tabular.dll')

$db = [Microsoft.AnalysisServices.Tabular.TmdlSerializer]::DeserializeDatabaseFromFolder((Resolve-Path $Definition).Path)
$result = $db.Model.Validate()
if ($result.ContainsErrors) {
    foreach ($e in $result.Errors) { Write-Host "TOM validation error: $($e.Message)" }
    exit 1
}

$tables = [ordered]@{}
foreach ($t in $db.Model.Tables) {
    $cols = [ordered]@{}
    foreach ($c in $t.Columns) {
        if ($c.Type -eq [Microsoft.AnalysisServices.Tabular.ColumnType]::RowNumber) { continue }
        $cols[$c.Name] = [ordered]@{ dataType = $c.DataType.ToString(); sourceColumn = $c.SourceColumn; isKey = $c.IsKey; isHidden = $c.IsHidden }
    }
    $measures = [ordered]@{}
    foreach ($m in $t.Measures) { $measures[$m.Name] = [ordered]@{ expression = $m.Expression; formatString = $m.FormatString } }
    $sources = @($t.Partitions | ForEach-Object { $_.Source.Expression })
    $tables[$t.Name] = [ordered]@{ dataCategory = $t.DataCategory; columns = $cols; measures = $measures; partitionSources = $sources }
}
$rels = @($db.Model.Relationships | ForEach-Object {
    [ordered]@{ from = "$($_.FromTable.Name).$($_.FromColumn.Name)"; to = "$($_.ToTable.Name).$($_.ToColumn.Name)";
                fromCardinality = $_.FromCardinality.ToString(); toCardinality = $_.ToCardinality.ToString();
                crossFilteringBehavior = $_.CrossFilteringBehavior.ToString(); isActive = $_.IsActive }
})
$summary = [ordered]@{
    tomVersion = $version
    compatibilityLevel = $db.CompatibilityLevel
    culture = $db.Model.Culture
    expressions = @($db.Model.Expressions | ForEach-Object { [ordered]@{ name = $_.Name; kind = $_.Kind.ToString(); expression = $_.Expression } })
    tables = $tables
    relationships = $rels
}
New-Item -ItemType Directory -Force (Split-Path $Out) | Out-Null
$summary | ConvertTo-Json -Depth 8 | Set-Content -Path $Out -Encoding utf8
$nMeasures = ($db.Model.Tables | ForEach-Object { $_.Measures.Count } | Measure-Object -Sum).Sum
Write-Host "TOM $version read $($db.Model.Tables.Count) tables, $nMeasures measures, $($db.Model.Relationships.Count) relationships; Model.Validate() reported no errors. Summary: $Out"
