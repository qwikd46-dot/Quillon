"""Cryptographic utilities: hardware fingerprint and key derivation."""

import hashlib
import os
import subprocess
from pathlib import Path
from typing import Optional

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from .config import CRYPTO_CONFIG, PATHS


class HardwareFingerprint:
    """Generates a stable hardware fingerprint for key derivation."""

    @staticmethod
    def _run_cmd(cmd: list[str], timeout: int = 5) -> Optional[str]:
        """Run command and return stdout or None on failure."""
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout
            )
            return result.stdout.strip()
        except Exception:
            return None

    @classmethod
    def get_cpu_id(cls) -> str:
        """Extract CPU ID from dmidecode."""
        output = cls._run_cmd(["dmidecode", "-t", "processor"])
        if not output:
            return "unknown-cpu"
        for line in output.split("\n"):
            if "ID:" in line and "UUID" not in line:
                return line.split("ID:")[-1].strip()
        return "unknown-cpu"

    @classmethod
    def get_mobo_serial(cls) -> str:
        """Extract motherboard serial from dmidecode."""
        output = cls._run_cmd(["dmidecode", "-t", "baseboard"])
        if not output:
            return "unknown-mobo"
        for line in output.split("\n"):
            if "Serial Number:" in line:
                return line.split("Serial Number:")[-1].strip()
        return "unknown-mobo"

    @classmethod
    def get_disk_uuid(cls) -> str:
        """Extract root filesystem UUID."""
        output = cls._run_cmd(["findmnt", "-o", "UUID", "-n", "/"])
        return output or "unknown-disk"

    @classmethod
    def get_mac_address(cls) -> str:
        """Extract primary MAC address."""
        output = cls._run_cmd(["ip", "link", "show"])
        if not output:
            return "unknown-mac"
        for line in output.split("\n"):
            if "link/ether" in line:
                return line.split("link/ether")[1].strip().split()[0]
        return "unknown-mac"

    @classmethod
    def generate(cls) -> str:
        """Generate combined hardware fingerprint hash."""
        cpu = cls.get_cpu_id()
        mobo = cls.get_mobo_serial()
        disk = cls.get_disk_uuid()
        mac = cls.get_mac_address()
        combined = f"{cpu}|{mobo}|{disk}|{mac}"
        return hashlib.sha256(combined.encode()).hexdigest()


class KeyManager:
    """Manages encryption key derivation and storage."""

    def __init__(self):
        self._key: Optional[bytes] = None
        self._salt: Optional[bytes] = None

    def _load_or_create_salt(self) -> bytes:
        """Load existing salt or generate new one."""
        if PATHS.KEY_FILE.exists():
            return bytes.fromhex(PATHS.KEY_FILE.read_text().strip())
        salt = os.urandom(CRYPTO_CONFIG.SALT_LENGTH)
        PATHS.KEY_FILE.write_text(salt.hex())
        PATHS.KEY_FILE.chmod(CRYPTO_CONFIG.KEY_FILE_PERMS)
        return salt

    def get_key(self) -> bytes:
        """Derive or return cached encryption key."""
        if self._key is not None:
            return self._key

        fingerprint = HardwareFingerprint.generate()
        salt = self._load_or_create_salt()

        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=CRYPTO_CONFIG.KEY_LENGTH,
            salt=salt,
            iterations=CRYPTO_CONFIG.PBKDF2_ITERATIONS,
        )
        self._key = kdf.derive(fingerprint.encode())
        return self._key

    def get_aesgcm(self) -> AESGCM:
        """Return AESGCM cipher instance."""
        return AESGCM(self.get_key())


# Global instances
HARDWARE_FINGERPRINT = HardwareFingerprint()
KEY_MANAGER = KeyManager()