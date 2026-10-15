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
        """Load the existing salt or generate a new one.

        The salt feeds the PBKDF2 derivation of the cookie/password key, so
        it must never be readable by another user -- not even for the instant
        between creating the file and chmod'ing it. It is written with
        ``O_CREAT|O_EXCL|O_NOFOLLOW`` at mode 0600 so the process umask never
        decides its permissions, and a pre-existing file is re-tightened to
        0600 and refused if it is a symlink (writing through a symlink would
        let a local attacker redirect the write to another file).
        """
        path = PATHS.KEY_FILE
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if path.is_symlink():
            raise RuntimeError(f"key file {path} must not be a symlink")
        if path.exists():
            path.chmod(CRYPTO_CONFIG.KEY_FILE_PERMS)
            return bytes.fromhex(path.read_text(encoding="ascii").strip())
        salt = os.urandom(CRYPTO_CONFIG.SALT_LENGTH)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(path, flags, CRYPTO_CONFIG.KEY_FILE_PERMS)
        except FileExistsError:
            path.chmod(CRYPTO_CONFIG.KEY_FILE_PERMS)
            return bytes.fromhex(path.read_text(encoding="ascii").strip())
        try:
            os.write(fd, salt.hex().encode("ascii"))
            os.fsync(fd)
        finally:
            os.close(fd)
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