"""
Standard-Compliant Direct PyPI Uploader for OmniCache with Retry Logic.
"""

import sys
import os
import glob
import hashlib
import zipfile
import tarfile
import email
import time
import httpx

PYPI_UPLOAD_URL = "https://upload.pypi.org/legacy/"

def compute_hashes(file_bytes):
    md5 = hashlib.md5(file_bytes).hexdigest()
    sha256 = hashlib.sha256(file_bytes).hexdigest()
    blake2_256 = hashlib.blake2b(file_bytes, digest_size=32).hexdigest()
    return md5, sha256, blake2_256

def extract_metadata_from_file(file_path: str):
    if file_path.endswith(".whl"):
        with zipfile.ZipFile(file_path, "r") as z:
            for name in z.namelist():
                if name.endswith("METADATA"):
                    meta_bytes = z.read(name)
                    return email.message_from_bytes(meta_bytes)
    elif file_path.endswith(".tar.gz"):
        with tarfile.open(file_path, "r:gz") as t:
            for member in t.getmembers():
                if member.name.endswith("PKG-INFO"):
                    f = t.extractfile(member)
                    if f:
                        meta_bytes = f.read()
                        return email.message_from_bytes(meta_bytes)
    return None

def upload_package(api_token: str):
    dist_files = sorted(glob.glob("/root/omnicache_proxy/dist/*"))
    if not dist_files:
        print("❌ No distribution files found in dist/.")
        sys.exit(1)

    print(f"📦 Found {len(dist_files)} distribution files to upload:")
    for f in dist_files:
        print(f"  - {os.path.basename(f)} ({os.path.getsize(f)} bytes)")

    client = httpx.Client(timeout=120.0)

    for file_path in dist_files:
        filename = os.path.basename(file_path)
        is_wheel = filename.endswith(".whl")
        
        with open(file_path, "rb") as f:
            file_bytes = f.read()

        md5, sha256, blake2 = compute_hashes(file_bytes)
        msg = extract_metadata_from_file(file_path)
        if not msg:
            print(f"❌ Failed to extract metadata from {filename}")
            continue

        form_data = {
            ":action": "file_upload",
            "protocol_version": "1",
            "metadata_version": msg.get("Metadata-Version", "2.1"),
            "name": msg.get("Name", "omnicache-proxy"),
            "version": msg.get("Version", "2.0.2"),
            "filetype": "bdist_wheel" if is_wheel else "sdist",
            "md5_digest": md5,
            "sha256_digest": sha256,
            "blake2_256_digest": blake2,
            "summary": msg.get("Summary", ""),
            "author": msg.get("Author", ""),
            "author_email": msg.get("Author-email", ""),
            "license": msg.get("License", "MIT"),
            "description": msg.get_payload(),
            "description_content_type": msg.get("Description-Content-Type", "text/markdown"),
            "requires_python": msg.get("Requires-Python", ">=3.9")
        }

        if is_wheel:
            parts = filename.split("-")
            if len(parts) >= 5:
                form_data["pyversion"] = parts[2]
        else:
            form_data["pyversion"] = "source"

        files = {
            "content": (filename, file_bytes, "application/octet-stream")
        }

        print(f"\n🚀 Uploading {filename} to PyPI...")
        uploaded = False
        for attempt in range(1, 4):
            try:
                response = client.post(
                    PYPI_UPLOAD_URL,
                    data=form_data,
                    files=files,
                    auth=("__token__", api_token.strip()),
                    timeout=120.0
                )
                if response.status_code == 200:
                    print("✅ [SUCCESS 200 OK]")
                    uploaded = True
                    break
                elif "already exists" in response.text.lower():
                    print("ℹ️ [ALREADY UPLOADED - 200 OK]")
                    uploaded = True
                    break
                else:
                    print(f"❌ [STATUS {response.status_code}]: {response.text}")
                    break
            except Exception as e:
                print(f"⚠️ [Attempt {attempt}/3 failed]: {e}")
                time.sleep(2)

    print("\n🎉 Verification Link: https://pypi.org/project/omnicache-proxy/")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 upload_to_pypi.py <PYPI_API_TOKEN>")
        sys.exit(1)
    upload_package(sys.argv[1])
