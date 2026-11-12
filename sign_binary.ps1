# PowerShell script to create a self-signed code signing certificate
# and sign the built DiscordBadgeMaximizer binaries.

$ErrorActionPreference = "Continue"

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  CREATING DIGITAL CODE SIGNING CERTIFICATE & SIGNING     " -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

$distDir = Resolve-Path "dist" -ErrorAction SilentlyContinue
if (-not $distDir) {
    Write-Host "Error: dist directory not found!" -ForegroundColor Red
    exit 1
}

# 1. Create a Self-Signed Code Signing Certificate with 10-year validity
Write-Host "Generating Self-Signed Code Signing Certificate..." -ForegroundColor Yellow
$cert = New-SelfSignedCertificate `
    -Type CodeSigningCert `
    -Subject "CN=Discord Badge Maximizer, O=Developer Community, OU=Software Security, C=VN" `
    -CertStoreLocation "Cert:\CurrentUser\My" `
    -HashAlgorithm SHA256 `
    -NotAfter (Get-Date).AddYears(10)

Write-Host "Certificate Created Successfully!" -ForegroundColor Green
Write-Host "  Thumbprint : $($cert.Thumbprint)"
Write-Host "  Subject    : $($cert.Subject)"
Write-Host "  Expires    : $($cert.NotAfter)"
Write-Host ""

# 2. Export Public Certificate (.cer) to dist
$cerPath = Join-Path $distDir "DiscordBadgeMaximizer.cer"
Export-Certificate -Cert $cert -FilePath $cerPath | Out-Null
Write-Host "Exported Public Certificate (.cer) to: $cerPath" -ForegroundColor Green

# 3. Sign the onefile standalone EXE if exists
$onefileExe = Join-Path $distDir "DiscordBadgeMaximizer.exe"
if (Test-Path $onefileExe) {
    Write-Host "Signing standalone EXE: $onefileExe..." -ForegroundColor Yellow
    try {
        Set-AuthenticodeSignature -FilePath $onefileExe -Certificate $cert -TimestampServer "http://timestamp.digicert.com" -HashAlgorithm SHA256 | Out-Null
        Write-Host "Successfully signed $onefileExe with DigiCert Timestamp!" -ForegroundColor Green
    } catch {
        Write-Host "Warning signing $onefileExe: $_" -ForegroundColor Red
    }
}

# 4. Sign the onedir EXE if exists
$onedirExe = Join-Path $distDir "DiscordBadgeMaximizer-App\DiscordBadgeMaximizer-App.exe"
if (Test-Path $onedirExe) {
    Write-Host "Signing onedir EXE: $onedirExe..." -ForegroundColor Yellow
    try {
        Set-AuthenticodeSignature -FilePath $onedirExe -Certificate $cert -TimestampServer "http://timestamp.digicert.com" -HashAlgorithm SHA256 | Out-Null
        Write-Host "Successfully signed $onedirExe with DigiCert Timestamp!" -ForegroundColor Green
    } catch {
        Write-Host "Warning signing $onedirExe: $_" -ForegroundColor Red
    }

    # Copy .cer and install_cert.bat into the onedir folder so the zip includes them
    $onedirFolder = Join-Path $distDir "DiscordBadgeMaximizer-App"
    Copy-Item $cerPath -Destination (Join-Path $onedirFolder "DiscordBadgeMaximizer.cer") -Force
    if (Test-Path "install_cert.bat") {
        Copy-Item "install_cert.bat" -Destination (Join-Path $onedirFolder "install_cert.bat") -Force
    }
}

Write-Host ""
Write-Host "==========================================================" -ForegroundColor Green
Write-Host "  DIGITAL SIGNATURE PROCESS FINISHED!                     " -ForegroundColor Green
Write-Host "==========================================================" -ForegroundColor Green
