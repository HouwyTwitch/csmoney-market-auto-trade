"""
RSA-OAEP encryption matching the CS.Money Chrome extension logic.

Extension source (decompiled from serviceWorker.js):
  algorithm = { name: "RSA-OAEP", modulusLength: 6144, hash: "SHA-256" }
  key format: base64-encoded DER wrapped in -----BEGIN RSA PUBLIC KEY-----

  encryptData({ cert, message }):
    1. base64-decode the cert
    2. strip the PEM header/footer
    3. base64-decode the inner DER bytes
    4. importKey("spki", ...)
    5. encrypt with RSA-OAEP
    6. return base64(ciphertext)
"""

import base64
import logging

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

logger = logging.getLogger(__name__)

_PEM_HEADER = b"-----BEGIN RSA PUBLIC KEY-----"
_PEM_FOOTER = b"-----END RSA PUBLIC KEY-----"


def _load_public_key(b64_cert: str):
    """
    Load RSA public key from the base64-encoded PEM string returned by
    /1.0/market/secure/key. The server wraps a PEM in another base64 layer.

    The extension does:
      Buffer.from(publicKey, "base64").toString("utf-8")   -> PEM text
      strip header/footer, base64-decode remaining          -> DER bytes
      importKey("spki", der, ...)                          -> CryptoKey
    """
    pem_text = base64.b64decode(b64_cert).decode("utf-8")
    pem_bytes = pem_text.strip().encode("utf-8")
    return serialization.load_pem_public_key(pem_bytes)


def encrypt_message(b64_cert: str, message: str) -> str:
    """Encrypt *message* with the server's RSA public key using OAEP/SHA-256."""
    public_key = _load_public_key(b64_cert)
    plaintext = message.encode("utf-8")
    ciphertext = public_key.encrypt(
        plaintext,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
    return base64.b64encode(ciphertext).decode("utf-8")
