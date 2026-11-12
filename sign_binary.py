#!/usr/bin/env python3
"""
Sign Binary Script for Discord Badge Maximizer
Generates a Self-Signed Code Signing Certificate using Python `cryptography`
and signs the .exe binaries with Microsoft SignTool + DigiCert RFC3161 Timestamp.
"""

import sys
import shutil
import datetime
import subprocess
from pathlib import Path
from cryptography import x509
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12, Encoding, BestAvailableEncryption

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
PASSWORD = b"discord123"

def find_signtool():
    # 1. Search PATH
    for name in ["signtool.exe", "signtool"]:
        p = shutil.which(name)
        if p:
            return str(p)

    # 2. Search Windows Kits SDK
    search_dirs = [
        Path(r"C:\Program Files (x86)\Windows Kits\10\bin"),
        Path(r"C:\Program Files\Microsoft Visual Studio"),
        Path(r"C:\Program Files (x86)\Microsoft Visual Studio"),
    ]
    for d in search_dirs:
        if d.exists():
            matches = sorted(d.glob("**/x64/signtool.exe"), reverse=True)
            if matches:
                return str(matches[0])
            matches = sorted(d.glob("**/signtool.exe"), reverse=True)
            if matches:
                return str(matches[0])
    return None

def generate_cert():
    print("=" * 60)
    print("  GENERATING CODE SIGNING CERTIFICATE")
    print("=" * 60)
    
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "Discord Badge Maximizer"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Developer Community"),
        x509.NameAttribute(NameOID.COUNTRY_NAME, "VN"),
    ])
    
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=3650)) # 10 years validity
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CODE_SIGNING]), critical=False)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False
            ),
            critical=True
        )
        .sign(key, hashes.SHA256())
    )

    DIST.mkdir(parents=True, exist_ok=True)
    cer_path = DIST / "DiscordBadgeMaximizer.cer"
    pfx_path = DIST / "cert.pfx"

    # Save DER certificate
    cer_path.write_bytes(cert.public_bytes(Encoding.DER))
    print(f"Saved Public Certificate: {cer_path} ({cer_path.stat().st_size} bytes)")

    # Save PFX with password
    pfx_bytes = pkcs12.serialize_key_and_certificates(
        b"Discord Badge Maximizer", key, cert, None, BestAvailableEncryption(PASSWORD)
    )
    pfx_path.write_bytes(pfx_bytes)
    print(f"Saved PFX Keystore: {pfx_path} ({pfx_path.stat().st_size} bytes)")

    return cer_path, pfx_path

def sign_binary(target_path: Path, pfx_path: Path, signtool: str):
    if not target_path.exists():
        print(f"Target file not found: {target_path}")
        return False

    print(f"\nSigning binary: {target_path.name}...")
    cmd = [
        signtool, "sign",
        "/f", str(pfx_path),
        "/p", PASSWORD.decode("ascii"),
        "/fd", "SHA256",
        "/tr", "http://timestamp.digicert.com",
        "/td", "SHA256",
        str(target_path)
    ]
    res = subprocess.run(cmd)
    if res.returncode == 0:
        print(f"Successfully signed {target_path.name} with DigiCert Timestamp!")
        return True
    else:
        print(f"SignTool failed with code {res.returncode}")
        return False

def main():
    cer_path, pfx_path = generate_cert()

    signtool = find_signtool()
    if not signtool:
        print("Warning: SignTool.exe not found on system. Certificate generated without signing.")
    else:
        print(f"\nFound Microsoft SignTool: {signtool}")
        # Sign onefile exe
        sign_binary(DIST / "DiscordBadgeMaximizer.exe", pfx_path, signtool)

        # Sign onedir exe
        sign_binary(DIST / "DiscordBadgeMaximizer-App" / "DiscordBadgeMaximizer-App.exe", pfx_path, signtool)

        # Sign Setup installer if present
        setup_exe = DIST / "DiscordBadgeMaximizer-Setup.exe"
        if setup_exe.exists():
            sign_binary(setup_exe, pfx_path, signtool)

    # Clean up sensitive PFX private key file from dist
    if pfx_path.exists():
        pfx_path.unlink()
        print("Removed temporary cert.pfx.")

    # Copy cer, install_cert.bat and assets to onedir app folder
    onedir_folder = DIST / "DiscordBadgeMaximizer-App"
    if onedir_folder.exists():
        if cer_path.exists():
            shutil.copy2(cer_path, onedir_folder / "DiscordBadgeMaximizer.cer")
        bat_file = ROOT / "install_cert.bat"
        if bat_file.exists():
            shutil.copy2(bat_file, onedir_folder / "install_cert.bat")
        
        # Ensure data/users directory is ready for users
        (onedir_folder / "data" / "users").mkdir(parents=True, exist_ok=True)

        # Ensure assets are present
        dest_assets = onedir_folder / "assets"
        dest_assets.mkdir(parents=True, exist_ok=True)
        src_assets = ROOT / "assets"
        if src_assets.exists():
            for a_file in src_assets.iterdir():
                if a_file.is_file():
                    shutil.copy2(a_file, dest_assets / a_file.name)
        print("Copied certificate, assets, and prepared data folder in DiscordBadgeMaximizer-App folder.")

if __name__ == "__main__":
    main()
