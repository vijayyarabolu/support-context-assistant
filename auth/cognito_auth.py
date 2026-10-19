"""
AWS Cognito JWT Validation and Tenant Isolation Middleware.

Validates JWT tokens issued by AWS Cognito User Pools to ensure:
1. The token signature is valid (verified against Cognito JWKS)
2. The token has not expired
3. The tenant_id claim is present for per-tenant data isolation

The agent only accesses data belonging to the authenticated tenant,
enforcing strict multi-tenant isolation at the application layer.
"""

import json
from typing import Dict, Any, Optional
from urllib.request import urlopen

import jwt
from jwt.algorithms import RSAAlgorithm

import config


class CognitoAuthMiddleware:
    """
    Validates Cognito JWTs and enforces tenant isolation.

    In production, retrieves the JSON Web Key Set (JWKS) from the Cognito
    User Pool and verifies token signatures. The tenant_id is extracted
    from a custom claim (custom:tenant_id) set during user registration.
    """

    def __init__(self):
        """
        Initialize the middleware with Cognito configuration.

        Caches the JWKS keys on first load to avoid repeated HTTP calls
        to the Cognito JWKS endpoint.
        """
        self.user_pool_id = config.COGNITO_USER_POOL_ID
        self.client_id = config.COGNITO_APP_CLIENT_ID
        self.region = config.AWS_REGION
        self.jwks_url = config.COGNITO_JWKS_URL

        # Cache for JWKS keys, loaded on first validation
        self._jwks_keys: Optional[list] = None

    def _get_jwks_keys(self) -> list:
        """
        Fetch and cache the JWKS public keys from Cognito.

        The JWKS (JSON Web Key Set) contains the public keys used to
        verify JWT signatures issued by this Cognito User Pool.

        Returns:
            List of JWK key dictionaries
        """
        if self._jwks_keys is not None:
            return self._jwks_keys

        if not self.jwks_url:
            raise ValueError("COGNITO_USER_POOL_ID not configured, cannot fetch JWKS")

        # Fetch JWKS from the Cognito well-known endpoint
        response = urlopen(self.jwks_url)
        jwks = json.loads(response.read())
        self._jwks_keys = jwks.get("keys", [])

        return self._jwks_keys

    def _get_public_key(self, token_header: dict):
        """
        Find the correct public key from JWKS matching the token's key ID (kid).

        Args:
            token_header: Decoded JWT header containing 'kid'

        Returns:
            RSA public key for signature verification

        Raises:
            ValueError: If no matching key found in JWKS
        """
        kid = token_header.get("kid")
        if not kid:
            raise ValueError("Token header missing kid")

        keys = self._get_jwks_keys()

        for key in keys:
            if key.get("kid") == kid:
                # Convert JWK to PEM public key format for PyJWT
                return RSAAlgorithm.from_jwk(json.dumps(key))

        raise ValueError(f"No matching key found for kid: {kid}")

    def validate_token(self, token: str) -> Dict[str, Any]:
        """
        Validate a Cognito JWT token and extract tenant information.

        Performs full cryptographic verification against the Cognito JWKS:
        1. Decodes the header to get the key ID (kid)
        2. Fetches the matching public key from JWKS
        3. Verifies the signature, expiration, and audience
        4. Extracts the tenant_id from custom claims

        Args:
            token: Raw JWT token string from the Authorization header

        Returns:
            Dict with validation result and tenant context
        """
        try:
            # Step 1: Decode the header without verification to get kid
            unverified_header = jwt.get_unverified_header(token)

            # Step 2: Get the public key matching this token's kid
            public_key = self._get_public_key(unverified_header)

            # Step 3: Verify and decode the token
            # This validates: signature, expiration (exp), audience (aud)
            decoded = jwt.decode(
                token,
                public_key,
                algorithms=["RS256"],
                audience=self.client_id,
                issuer=f"https://cognito-idp.{self.region}.amazonaws.com/{self.user_pool_id}",
            )

            # Step 4: Extract tenant_id from custom claims
            tenant_id = decoded.get("custom:tenant_id", "")
            if not tenant_id:
                return {
                    "is_valid": False,
                    "error": "Token missing custom:tenant_id claim",
                }

            return {
                "is_valid": True,
                "tenant_id": tenant_id,
                "username": decoded.get("cognito:username", decoded.get("sub", "unknown")),
                "email": decoded.get("email", ""),
                "groups": decoded.get("cognito:groups", []),
                "token_use": decoded.get("token_use", ""),
                "exp": decoded.get("exp", 0),
            }

        except jwt.ExpiredSignatureError:
            return {
                "is_valid": False,
                "error": "Token has expired",
            }
        except jwt.InvalidAudienceError:
            return {
                "is_valid": False,
                "error": "Token audience does not match app client ID",
            }
        except jwt.InvalidTokenError as e:
            return {
                "is_valid": False,
                "error": f"Invalid token: {str(e)}",
            }
        except Exception as e:
            if config.DEMO_MODE:
                return self._validate_demo_token(token, str(e))

            return {
                "is_valid": False,
                "error": f"Token verification failed: {str(e)}",
            }

    def _validate_demo_token(self, token: str, verification_error: str) -> Dict[str, Any]:
        """
        Decode a local/demo token without signature verification.

        This path is intentionally gated by config.DEMO_MODE so unverified JWTs
        cannot silently authenticate users in production deployments.
        """
        try:
            decoded = jwt.decode(
                token,
                options={
                    "verify_signature": False,
                    "verify_aud": False,
                    "verify_iss": False,
                    "verify_exp": True,
                },
            )
        except jwt.ExpiredSignatureError:
            return {
                "is_valid": False,
                "error": "Demo token has expired",
                "auth_mode": "demo_unverified",
            }
        except jwt.DecodeError:
            return {
                "is_valid": False,
                "error": f"Token decode failed: {verification_error}",
                "auth_mode": "demo_unverified",
            }
        except jwt.InvalidTokenError as e:
            return {
                "is_valid": False,
                "error": f"Invalid demo token: {str(e)}",
                "auth_mode": "demo_unverified",
            }

        tenant_id = decoded.get("custom:tenant_id")
        if not tenant_id:
            return {
                "is_valid": False,
                "error": "Token missing custom:tenant_id claim",
                "auth_mode": "demo_unverified",
            }

        return {
            "is_valid": True,
            "tenant_id": tenant_id,
            "username": decoded.get("cognito:username", decoded.get("username", "demo_user")),
            "email": decoded.get("email", "demo@example.com"),
            "auth_mode": "demo_unverified",
            "warning": "Signature verification skipped because DEMO_MODE=true",
            "verification_error": verification_error[:100],
        }

    def enforce_tenant_isolation(self, token: str, requested_tenant_id: str) -> bool:
        """
        Verify that the authenticated user has access to the requested tenant.

        Prevents cross-tenant data access by comparing the token's tenant_id
        with the tenant_id being requested in the agent query.

        Args:
            token: JWT token from the request
            requested_tenant_id: Tenant ID the agent is trying to access

        Returns:
            True if access is allowed, False otherwise
        """
        validation = self.validate_token(token)

        if not validation.get("is_valid"):
            return False

        # Tenant isolation: token's tenant must match the requested tenant
        return validation.get("tenant_id") == requested_tenant_id
