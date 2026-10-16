"""Official verifier integration with a synthetic trust chain, NOT Apple PKI.

No live OCSP/network or real Apple signed payloads. Online trust wiring is
tested separately; live Apple interoperability belongs to MON-03.
"""
import base64
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID, ObjectIdentifier
from appstoreserverlibrary.models.Environment import Environment
from appstoreserverlibrary.signed_data_verifier import SignedDataVerifier, VerificationException

from app.services.apple_subscriptions import AppleAdapter, SubscriptionError


@pytest.fixture
def chain():
    keys = [ec.generate_private_key(ec.SECP256R1()) for _ in range(3)]
    names = [x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, n)])
             for n in ("offline root", "offline intermediate", "offline leaf")]
    certs = []
    now = datetime.now(timezone.utc)
    for i in range(3):
        issuer = max(0, i - 1)
        builder = (x509.CertificateBuilder().subject_name(names[i]).issuer_name(names[issuer])
                   .public_key(keys[i].public_key()).serial_number(x509.random_serial_number())
                   .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=1))
                   .add_extension(x509.BasicConstraints(ca=i < 2, path_length=None), critical=True)
                   .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False,
                                               key_encipherment=False, data_encipherment=False,
                                               key_agreement=False, key_cert_sign=i < 2, crl_sign=i < 2,
                                               encipher_only=None, decipher_only=None), critical=True)
                   .add_extension(x509.SubjectKeyIdentifier.from_public_key(keys[i].public_key()), critical=False)
                   .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(keys[issuer].public_key()), critical=False))
        if i:
            oid = "1.2.840.113635.100.6.2.1" if i == 1 else "1.2.840.113635.100.6.11.1"
            builder = builder.add_extension(x509.UnrecognizedExtension(ObjectIdentifier(oid), b"\x05\x00"), critical=False)
        certs.append(builder.sign(keys[issuer], hashes.SHA256()))
    der = [c.public_bytes(serialization.Encoding.DER) for c in certs]
    headers = {"x5c": [base64.b64encode(c).decode() for c in reversed(der)]}
    payload = dict(bundleId="offline.bundle", environment="Sandbox", transactionId="101",
                   originalTransactionId="100", signedDate=int(now.timestamp() * 1000))
    return keys[-1], der[0], headers, payload


def test_real_signature_chain_and_rejection(chain):
    key, root, headers, payload = chain
    verifier = SignedDataVerifier([root], False, Environment.SANDBOX, "offline.bundle", 123)
    signed = jwt.encode(payload, key, algorithm="ES256", headers=headers)
    assert verifier.verify_and_decode_signed_transaction(signed).transactionId == "101"
    for invalid in (
        jwt.encode(payload, ec.generate_private_key(ec.SECP256R1()), algorithm="ES256", headers=headers),
        jwt.encode(dict(payload, bundleId="wrong"), key, algorithm="ES256", headers=headers),
        jwt.encode(dict(payload, environment="Production"), key, algorithm="ES256", headers=headers),
        jwt.encode(payload, key, algorithm="ES256"),
    ):
        with pytest.raises(VerificationException):
            verifier.verify_and_decode_signed_transaction(invalid)
    with pytest.raises(VerificationException):
        SignedDataVerifier([], False, Environment.SANDBOX, "offline.bundle", 123).verify_and_decode_signed_transaction(signed)
    renewal = jwt.encode(dict(environment="Sandbox", originalTransactionId="100",
                              signedDate=payload["signedDate"]), key, algorithm="ES256", headers=headers)
    assert verifier.verify_and_decode_renewal_info(renewal).originalTransactionId == "100"


def test_real_adapter_enables_online_checks_and_trust_config(chain, tmp_path, monkeypatch):
    from app.services import apple_subscriptions as apple
    key, root, _, _ = chain
    root_path, key_path = tmp_path / "root.der", tmp_path / "key.pem"
    root_path.write_bytes(root)
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption()))
    config = dict(APPLE_SUBSCRIPTION_ENVIRONMENT="Production", APPLE_BUNDLE_ID="offline.bundle", APPLE_APP_ID=123,
                  APPLE_MONTHLY_PRODUCT_ID="monthly.fixture", APPLE_ANNUAL_PRODUCT_ID="annual.fixture",
                  APPLE_SUBSCRIPTION_GROUP_ID="group.fixture", APPLE_ROOT_CERTIFICATE_PATHS=[str(root_path)],
                  APPLE_PRIVATE_KEY_PATH=str(key_path), APPLE_KEY_ID="fixture", APPLE_ISSUER_ID="fixture")
    seen = []
    real_verifier = apple.SignedDataVerifier
    def observe(*args):
        seen.append(args)
        return real_verifier(*args)
    monkeypatch.setattr(apple, "SignedDataVerifier", observe)
    AppleAdapter(config)
    assert seen == [([root], True, Environment.PRODUCTION, "offline.bundle", 123)]
    for env in ("Xcode", "LocalTesting", "", "Sandbox,Production"):
        with pytest.raises(SubscriptionError, match="NOT_CONFIGURED"):
            AppleAdapter(dict(config, APPLE_SUBSCRIPTION_ENVIRONMENT=env))
