Set-Location E:\AntNest\AntNest-master

# 0. 修正本地 tag 指向当前 HEAD(166703d, 含 RELEASE_NOTES), 强制更新远端
git tag -f v0.1.0 HEAD | Out-Host
git push origin v0.1.0 --force 2>&1 | Out-Host
Write-Host "== tag v0.1.0 已指向 $((git rev-parse --short HEAD)) 并推送 =="

# 1. git archive 打包源码(只含已跟踪文件, config.json/.venv 等敏感文件自动排除)
$zipPath = "$env:TEMP\antnest-v0.1.0.zip"
git archive --format=zip --output=$zipPath HEAD
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [System.IO.Compression.ZipFile]::Open($zipPath, 'Update')
$entry = $zip.CreateEntry('AntNest.exe', [System.IO.Compression.CompressionLevel]::Optimal)
$entryStream = $entry.Open()
$fileStream = [System.IO.File]::OpenRead('E:\AntNest\AntNest-master\AntNest.exe')
$fileStream.CopyTo($entryStream)
$fileStream.Close()
$entryStream.Close()
$zip.Dispose()
Write-Host ("== zip 已生成: {0:N1} KB ==" -f ((Get-Item $zipPath).Length/1KB))

# 2. 取 token(不打印明文)
$credInputFile = "$env:TEMP\cred_input.txt"
[System.IO.File]::WriteAllText($credInputFile, "protocol=https`nhost=github.com`n", [System.Text.Encoding]::ASCII)
$cred = cmd /c "git credential fill < `"%TEMP%\cred_input.txt`" 2>&1"
$token = ($cred | Where-Object { $_ -match '^password=' }) -replace '^password=',''
if (-not $token) { throw "无法获取 GitHub token" }
Write-Host ("== token 已获取(长度 {0}) ==" -f $token.Length)
$headers = @{ Authorization = "Bearer $token"; Accept = "application/vnd.github+json" }

# 3. 检查是否已有 v0.1.0 release
$api = "https://api.github.com/repos/llxpy/AntNest"
$existing = $null
try { $existing = Invoke-RestMethod -Uri "$api/releases/tags/v0.1.0" -Headers $headers -ErrorAction Stop } catch { $existing = $null }
$releaseNotes = [System.IO.File]::ReadAllText("E:\AntNest\AntNest-master\RELEASE_NOTES_v0.1.0.md", [System.Text.Encoding]::UTF8)
$body = @{ tag_name = "v0.1.0"; name = "AntNest v0.1.0"; body = $releaseNotes; draft = $false; prerelease = $false } | ConvertTo-Json

if ($existing) {
    Write-Host "== 已有 release,更新之 =="
    $release = Invoke-RestMethod -Uri "$api/releases/$($existing.id)" -Method Patch -Headers $headers -ContentType "application/json; charset=utf-8" -Body ([System.Text.Encoding]::UTF8.GetBytes($body))
    # 已有同名附件先删
    foreach ($asset in @($release.assets)) {
        if ($asset.name -eq 'antnest-v0.1.0.zip') {
            Invoke-RestMethod -Uri "$api/releases/assets/$($asset.id)" -Method Delete -Headers $headers | Out-Null
            Write-Host "== 旧附件已删 =="
        }
    }
} else {
    Write-Host "== 无已有 release,新建 =="
    $release = Invoke-RestMethod -Uri "$api/releases" -Method Post -Headers $headers -ContentType "application/json; charset=utf-8" -Body ([System.Text.Encoding]::UTF8.GetBytes($body))
}
if (-not $release -or -not $release.html_url) { throw "Release 创建/更新失败" }
Write-Host "== Release: $($release.html_url) =="

# 4. 上传附件
$assetUrl = "https://uploads.github.com/repos/llxpy/AntNest/releases/$($release.id)/assets"
$fileBytes = [System.IO.File]::ReadAllBytes($zipPath)
$resp = Invoke-RestMethod -Uri "${assetUrl}?name=antnest-v0.1.0.zip" -Method Post -Headers $headers -ContentType "application/zip" -Body $fileBytes
Write-Host "== 附件: $($resp.browser_download_url) =="
Write-Host "== 全部完成 =="
