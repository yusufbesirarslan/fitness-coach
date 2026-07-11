"""Compatibility exports for the canonical native Cognito service.

All implementation lives in :mod:`app.services.cognito_service` so token
verification and provider behavior cannot diverge between two copies.
"""
from app.services.cognito_service import (
    CognitoIdpError,
    CognitoServiceError,
    _decode_claims,
    _get_client,
    _get_jwks,
    _maybe_secret,
    _secret_hash,
    _wrap,
    confirm_sign_up,
    initiate_auth,
    resend_code,
    sign_up,
)

__all__ = [
    "CognitoIdpError",
    "CognitoServiceError",
    "confirm_sign_up",
    "initiate_auth",
    "resend_code",
    "sign_up",
]
