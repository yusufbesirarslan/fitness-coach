"""Apple trust boundary. Client transaction IDs are lookup inputs, never proof."""
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
    def __init__(self, code, status=422):
        self.code, self.status = code, status
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
        try:
            submitted = self._transaction(
                self.client.get_transaction_info(transaction_id).signedTransactionInfo, token)
            if submitted.transactionId != transaction_id:
                raise SubscriptionError("SUBSCRIPTION_INVALID_EVIDENCE")
            response = self.client.get_all_subscription_statuses(submitted.originalTransactionId)
            # App ID is present in the authenticated HTTPS API envelope, not
            # JWSTransaction. SignedDataVerifier validates signed bundle/environment.
            if (response.bundleId != self.bundle or response.appAppleId != self.app_id
                    or response.environment != Environment(self.environment)):
                raise SubscriptionError("SUBSCRIPTION_INVALID_EVIDENCE")
            matches = []
            for group in response.data or []:
                for item in group.lastTransactions or []:
                    if item.originalTransactionId != submitted.originalTransactionId:
                        continue
                    tx = self._transaction(item.signedTransactionInfo, token)
                    renewal = self.verifier.verify_and_decode_renewal_info(item.signedRenewalInfo)
                    if (group.subscriptionGroupIdentifier != self.group
                            or tx.originalTransactionId != submitted.originalTransactionId
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
                        raise SubscriptionError("SUBSCRIPTION_VERIFICATION_PENDING", 503)
                    grace = milliseconds(renewal.gracePeriodExpiresDate) if renewal.gracePeriodExpiresDate else None
                    if item.status == 4 and (not grace or renewal.isInBillingRetryPeriod is not True):
                        raise SubscriptionError("SUBSCRIPTION_INVALID_EVIDENCE")
                    matches.append(Evidence(
                        self.environment, tx.originalTransactionId, tx.transactionId, tx.productId,
                        self.group, str(UUID(tx.appAccountToken)), int(item.status),
                        milliseconds(tx.expiresDate), milliseconds(tx.revocationDate) if tx.revocationDate else None,
                        grace, renewal.autoRenewStatus == 1, tx_date, renewal_date))
            if len(matches) != 1:
                raise SubscriptionError("SUBSCRIPTION_INVALID_EVIDENCE")
            return matches[0]
        except VerificationException as error:
            if error.status == VerificationStatus.RETRYABLE_VERIFICATION_FAILURE:
                raise SubscriptionError("SUBSCRIPTION_TEMPORARILY_UNAVAILABLE", 503) from None
            raise SubscriptionError("SUBSCRIPTION_INVALID_EVIDENCE") from None
        except APIException as error:
            if error.http_status_code == 404:
                raise SubscriptionError("SUBSCRIPTION_INVALID_EVIDENCE") from None
            raise SubscriptionError("SUBSCRIPTION_TEMPORARILY_UNAVAILABLE", 503) from None
        except requests.RequestException:
            raise SubscriptionError("SUBSCRIPTION_TEMPORARILY_UNAVAILABLE", 503) from None
        except (ValueError, TypeError, AttributeError):
            raise SubscriptionError("SUBSCRIPTION_INVALID_EVIDENCE") from None
