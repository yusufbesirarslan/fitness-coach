"""Apple trust boundary. Client transaction IDs are lookup inputs, never proof.

Notifications are authenticated hints only: they name a subscription to
reconcile, and only a fresh authenticated status lookup changes access.
"""
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import requests
from appstoreserverlibrary.api_client import AppStoreServerAPIClient, APIException
from appstoreserverlibrary.models.Environment import Environment
from appstoreserverlibrary.models.Type import Type
from appstoreserverlibrary.models.InAppOwnershipType import InAppOwnershipType
from appstoreserverlibrary.signed_data_verifier import SignedDataVerifier, VerificationException, VerificationStatus


class SubscriptionError(Exception):
    # ``reason`` is internal classification for reconciliation; never a response field.
    def __init__(self, code, status=422, reason=None):
        self.code, self.status, self.reason = code, status, reason
        super().__init__(code)


def milliseconds(value):
    if type(value) is not int or value <= 0:
        raise SubscriptionError("SUBSCRIPTION_INVALID_EVIDENCE")
    try:
        return datetime.fromtimestamp(value / 1000, timezone.utc)
    except (ValueError, OverflowError, OSError):
        raise SubscriptionError("SUBSCRIPTION_INVALID_EVIDENCE") from None


@dataclass(frozen=True)
class Evidence:
    environment: str
    original_id: str
    transaction_id: str
    product_id: str
    group_id: str
    account_token: str
    status: int
    expires_at: datetime
    revoked_at: datetime | None
    grace_expires_at: datetime | None
    auto_renew: bool
    transaction_signed_at: datetime
    renewal_signed_at: datetime
    purchased_at: datetime | None = None


@dataclass(frozen=True)
class Notification:
    """Verified notification identity. ``original_id`` set means: reconcile it."""
    uuid: str
    environment: str
    type: str
    subtype: str | None
    signed_at: datetime
    original_id: str | None
    account_token: str | None
    disposition: str


_NOTIFICATION_NAME = re.compile(r"[A-Z0-9_]{1,40}")
MAX_CLOCK_SKEW_SECONDS = 300


class BoundedAppleClient(AppStoreServerAPIClient):
    def _execute_request(self, method, url, params, headers, json, data):
        # Official authentication, URL selection and decoding; bounded transport,
        # no automatic retries or redirects carrying the authorization header.
        return requests.request(method, url, params=params, headers=headers,
                                json=json, data=data, timeout=(3, 8), allow_redirects=False)


class AppleAdapter:
    def __init__(self, config):
        self.environment = config.get("APPLE_SUBSCRIPTION_ENVIRONMENT")
        self.bundle = config.get("APPLE_BUNDLE_ID")
        self.app_id = config.get("APPLE_APP_ID")
        self.products = {config.get("APPLE_MONTHLY_PRODUCT_ID"), config.get("APPLE_ANNUAL_PRODUCT_ID")}
        self.group = config.get("APPLE_SUBSCRIPTION_GROUP_ID")
        # One server deployment trusts exactly one environment, never client-selected.
        if (self.environment not in ("Sandbox", "Production") or not self.bundle
                or type(self.app_id) is not int or self.app_id <= 0
                or len(self.products) != 2 or not all(self.products) or not self.group):
            raise SubscriptionError("SUBSCRIPTION_NOT_CONFIGURED", 503)
        try:
            roots = [Path(p).read_bytes() for p in config["APPLE_ROOT_CERTIFICATE_PATHS"]]
            key = Path(config["APPLE_PRIVATE_KEY_PATH"]).read_bytes()
            if not roots or not key or not config["APPLE_KEY_ID"] or not config["APPLE_ISSUER_ID"]:
                raise ValueError()
            env = Environment(self.environment)
            self.verifier = SignedDataVerifier(roots, True, env, self.bundle, self.app_id)
            self.client = BoundedAppleClient(key, config["APPLE_KEY_ID"],
                                            config["APPLE_ISSUER_ID"], self.bundle, env)
        except (KeyError, TypeError, ValueError, OSError):
            raise SubscriptionError("SUBSCRIPTION_NOT_CONFIGURED", 503) from None

    def _transaction(self, signed, token):
        tx = self.verifier.verify_and_decode_signed_transaction(signed)
        if (tx.bundleId != self.bundle or tx.environment != Environment(self.environment)
                or tx.productId not in self.products or tx.subscriptionGroupIdentifier != self.group
                or tx.type != Type.AUTO_RENEWABLE_SUBSCRIPTION
                or tx.inAppOwnershipType != InAppOwnershipType.PURCHASED
                or not tx.originalTransactionId or not tx.transactionId
                or len(tx.originalTransactionId) > 64 or len(tx.transactionId) > 64):
            raise SubscriptionError("SUBSCRIPTION_INVALID_EVIDENCE")
        try:
            if UUID(tx.appAccountToken) != UUID(token):
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise SubscriptionError("SUBSCRIPTION_OWNERSHIP_CONFLICT", 409) from None
        return tx

    def verify(self, transaction_id, token):
        def lookup():
            submitted = self._transaction(
                self.client.get_transaction_info(transaction_id).signedTransactionInfo, token)
            if submitted.transactionId != transaction_id:
                raise SubscriptionError("SUBSCRIPTION_INVALID_EVIDENCE")
            return self._status(submitted.originalTransactionId, token)
        return self._guard(lookup)

    def status(self, original_id, token):
        """Reconciliation: a fresh authoritative lookup for a known original ID."""
        if type(original_id) is not str or not original_id.isascii() or not original_id.isdigit() \
                or not 1 <= len(original_id) <= 64:
            raise SubscriptionError("SUBSCRIPTION_INVALID_EVIDENCE")
        return self._guard(lambda: self._status(original_id, token))

    def _status(self, original_id, token):
        response = self.client.get_all_subscription_statuses(original_id)
        # App Apple ID may be omitted in Sandbox. If provided it must match;
        # Production always requires the configured ID. Bundle/environment and
        # every signed transaction/renewal check remain mandatory in both.
        app_id_matches = response.appAppleId == self.app_id
        if self.environment == "Sandbox" and response.appAppleId is None:
            app_id_matches = True
        if (response.bundleId != self.bundle or not app_id_matches
                or response.environment != Environment(self.environment)):
            raise SubscriptionError("SUBSCRIPTION_INVALID_EVIDENCE")
        matches = []
        for group in response.data or []:
            for item in group.lastTransactions or []:
                if item.originalTransactionId != original_id:
                    continue
                tx = self._transaction(item.signedTransactionInfo, token)
                renewal = self.verifier.verify_and_decode_renewal_info(item.signedRenewalInfo)
                if (group.subscriptionGroupIdentifier != self.group
                        or tx.originalTransactionId != original_id
                        or renewal.originalTransactionId != tx.originalTransactionId
                        or renewal.environment != Environment(self.environment)
                        or renewal.productId != tx.productId
                        or item.status not in (1, 2, 3, 4, 5)
                        or renewal.autoRenewStatus not in (0, 1)
                        or (renewal.autoRenewProductId is not None
                            and renewal.autoRenewProductId not in self.products)):
                    raise SubscriptionError("SUBSCRIPTION_INVALID_EVIDENCE")
                now = datetime.now(timezone.utc)
                tx_date, renewal_date = milliseconds(tx.signedDate), milliseconds(renewal.signedDate)
                # Don't bless stale signed snapshots as a fresh status lookup.
                if any(abs((now - d).total_seconds()) > 300 for d in (tx_date, renewal_date)):
                    raise SubscriptionError("SUBSCRIPTION_VERIFICATION_PENDING", 503, "STALE_SNAPSHOT")
                grace = milliseconds(renewal.gracePeriodExpiresDate) if renewal.gracePeriodExpiresDate else None
                if item.status == 4 and (not grace or renewal.isInBillingRetryPeriod is not True):
                    raise SubscriptionError("SUBSCRIPTION_INVALID_EVIDENCE")
                matches.append(Evidence(
                    self.environment, tx.originalTransactionId, tx.transactionId, tx.productId,
                    self.group, str(UUID(tx.appAccountToken)), int(item.status),
                    milliseconds(tx.expiresDate), milliseconds(tx.revocationDate) if tx.revocationDate else None,
                    grace, renewal.autoRenewStatus == 1, tx_date, renewal_date,
                    milliseconds(tx.purchaseDate)))
        if len(matches) != 1:
            raise SubscriptionError("SUBSCRIPTION_INVALID_EVIDENCE")
        return matches[0]

    def notification(self, signed_payload):
        """Authenticate an App Store Server Notification V2 and name its subscription.

        Signature/identity failures are INVALID. An authentic notification that
        concerns nothing this deployment sells is IGNORED, so Apple stops retrying.
        """
        def decode():
            payload = self.verifier.verify_and_decode_notification(signed_payload)
            uuid = str(UUID(payload.notificationUUID))
            signed_at = milliseconds(payload.signedDate)
            kind, subtype = payload.rawNotificationType, payload.rawSubtype
            if (payload.version != "2.0" or uuid != payload.notificationUUID.lower()
                    or type(kind) is not str or not _NOTIFICATION_NAME.fullmatch(kind)
                    or (subtype is not None and (type(subtype) is not str
                                                 or not _NOTIFICATION_NAME.fullmatch(subtype)))
                    or (signed_at - datetime.now(timezone.utc)).total_seconds() > MAX_CLOCK_SKEW_SECONDS):
                raise SubscriptionError("SUBSCRIPTION_INVALID_EVIDENCE")
            data = payload.data

            def result(disposition, original_id=None, token=None):
                return Notification(uuid, self.environment, kind, subtype, signed_at,
                                    original_id, token, disposition)
            if data is None or not data.signedTransactionInfo:
                # TEST, summaries and app-level events carry no subscription.
                return result("IGNORED_NO_TRANSACTION")
            # The verifier compares App Apple ID in Production only; Sandbox omits it.
            if data.appAppleId is not None and data.appAppleId != self.app_id:
                raise SubscriptionError("SUBSCRIPTION_INVALID_EVIDENCE")
            tx = self.verifier.verify_and_decode_signed_transaction(data.signedTransactionInfo)
            original = tx.originalTransactionId
            if (tx.bundleId != self.bundle or tx.environment != Environment(self.environment)
                    or type(original) is not str or not original.isascii() or not original.isdigit()
                    or not 1 <= len(original) <= 64):
                raise SubscriptionError("SUBSCRIPTION_INVALID_EVIDENCE")
            if data.signedRenewalInfo:
                renewal = self.verifier.verify_and_decode_renewal_info(data.signedRenewalInfo)
                if (renewal.originalTransactionId != original
                        or renewal.environment != Environment(self.environment)):
                    raise SubscriptionError("SUBSCRIPTION_INVALID_EVIDENCE")
            if (tx.type != Type.AUTO_RENEWABLE_SUBSCRIPTION or tx.productId not in self.products
                    or tx.subscriptionGroupIdentifier != self.group):
                return result("IGNORED_PRODUCT")
            if tx.inAppOwnershipType != InAppOwnershipType.PURCHASED:
                return result("IGNORED_OWNERSHIP")
            try:
                token = str(UUID(tx.appAccountToken))
            except (ValueError, TypeError, AttributeError):
                token = None  # Only an existing ownership row can then be reconciled.
            return result("ENQUEUED", original, token)
        return self._guard(decode)

    def _guard(self, operation):
        try:
            return operation()
        except VerificationException as error:
            if error.status == VerificationStatus.RETRYABLE_VERIFICATION_FAILURE:
                raise SubscriptionError("SUBSCRIPTION_TEMPORARILY_UNAVAILABLE", 503) from None
            raise SubscriptionError("SUBSCRIPTION_INVALID_EVIDENCE") from None
        except APIException as error:
            if error.http_status_code == 404:
                # Not proof of invalidity: Apple documents retryable 404s. Client
                # verify still answers INVALID_EVIDENCE; reconciliation retries it.
                raise SubscriptionError("SUBSCRIPTION_INVALID_EVIDENCE", reason="APPLE_NOT_FOUND") from None
            raise SubscriptionError("SUBSCRIPTION_TEMPORARILY_UNAVAILABLE", 503) from None
        except requests.RequestException:
            raise SubscriptionError("SUBSCRIPTION_TEMPORARILY_UNAVAILABLE", 503) from None
        except (ValueError, TypeError, AttributeError):
            raise SubscriptionError("SUBSCRIPTION_INVALID_EVIDENCE") from None
