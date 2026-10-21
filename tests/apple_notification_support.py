"""Synthetic App Store signing for BE-02 tests: official verifier, NOT Apple PKI.

Authentic synthetic ES256 signatures over a synthetic x5c chain carrying Apple's
marker OIDs. No live OCSP, network or real Apple payload; live Apple
interoperability belongs to MON-03.
"""
import base64
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID, ObjectIdentifier
from appstoreserverlibrary.models.Environment import Environment
from appstoreserverlibrary.signed_data_verifier import SignedDataVerifier

from app.services.apple_subscriptions import AppleAdapter

BUNDLE, APP_ID, GROUP = "offline.bundle", 123, "group.fixture"
PRODUCTS = {"monthly.fixture", "annual.fixture"}


def ms(value):
    return int(value.timestamp() * 1000)


class Signer:
    def __init__(self):
        keys = [ec.generate_private_key(ec.SECP256R1()) for _ in range(3)]
        names = [x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, n)])
                 for n in ("offline root", "offline intermediate", "offline leaf")]
        now = datetime.now(timezone.utc)
        certs = []
        for i in range(3):
            issuer = max(0, i - 1)
            builder = (x509.CertificateBuilder().subject_name(names[i]).issuer_name(names[issuer])
                       .public_key(keys[i].public_key()).serial_number(x509.random_serial_number())
                       .not_valid_before(now - timedelta(days=30)).not_valid_after(now + timedelta(days=30))
                       .add_extension(x509.BasicConstraints(ca=i < 2, path_length=None), critical=True)
                       .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False,
                                                   key_encipherment=False, data_encipherment=False,
                                                   key_agreement=False, key_cert_sign=i < 2, crl_sign=i < 2,
                                                   encipher_only=None, decipher_only=None), critical=True)
                       .add_extension(x509.SubjectKeyIdentifier.from_public_key(keys[i].public_key()), critical=False)
                       .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(
                           keys[issuer].public_key()), critical=False))
            if i:
                oid = "1.2.840.113635.100.6.2.1" if i == 1 else "1.2.840.113635.100.6.11.1"
                builder = builder.add_extension(x509.UnrecognizedExtension(ObjectIdentifier(oid), b"\x05\x00"),
                                                critical=False)
            certs.append(builder.sign(keys[issuer], hashes.SHA256()))
        der = [c.public_bytes(serialization.Encoding.DER) for c in certs]
        self.key, self.root = keys[-1], der[0]
        self.headers = {"x5c": [base64.b64encode(c).decode() for c in reversed(der)]}

    def sign(self, payload, key=None, headers=None):
        return jwt.encode(payload, key or self.key, algorithm="ES256",
                          headers=self.headers if headers is None else headers)

    def adapter(self, environment="Sandbox"):
        a = AppleAdapter.__new__(AppleAdapter)
        a.environment, a.bundle, a.app_id = environment, BUNDLE, APP_ID
        a.products, a.group = set(PRODUCTS), GROUP
        # Online OCSP is disabled ONLY because the chain is synthetic.
        a.verifier = SignedDataVerifier([self.root], False, Environment(environment), BUNDLE, APP_ID)
        return a

    def transaction(self, token, environment="Sandbox", key=None, **changes):
        now = datetime.now(timezone.utc)
        payload = dict(transactionId="101", originalTransactionId="100", bundleId=BUNDLE,
                       productId="monthly.fixture", subscriptionGroupIdentifier=GROUP,
                       purchaseDate=ms(now - timedelta(minutes=1)), expiresDate=ms(now + timedelta(days=30)),
                       type="Auto-Renewable Subscription", inAppOwnershipType="PURCHASED",
                       environment=environment, appAccountToken=token, signedDate=ms(now))
        payload.update(changes)
        return self.sign({k: v for k, v in payload.items() if v is not None}, key=key)

    def renewal(self, environment="Sandbox", **changes):
        payload = dict(originalTransactionId="100", productId="monthly.fixture", autoRenewStatus=1,
                       environment=environment, signedDate=ms(datetime.now(timezone.utc)))
        payload.update(changes)
        return self.sign(payload)

    def notification(self, token=None, kind="DID_RENEW", environment="Sandbox", data=True,
                     transaction=None, renewal=None, **changes):
        payload = dict(notificationType=kind, notificationUUID=str(uuid4()), version="2.0",
                       signedDate=ms(datetime.now(timezone.utc)))
        if data:
            body = dict(bundleId=BUNDLE, environment=environment, status=1)
            body["signedTransactionInfo"] = transaction if transaction is not None else \
                self.transaction(token or str(uuid4()), environment)
            body["signedRenewalInfo"] = renewal if renewal is not None else self.renewal(environment)
            if data is not True:
                body.update(data)
            payload["data"] = {k: v for k, v in body.items() if v is not None}
        payload.update(changes)
        return self.sign({k: v for k, v in payload.items() if v is not None}), payload
