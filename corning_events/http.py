"""Shared HTTP session.

Lands in M1. Carries config.USER_AGENT on every request, applies
config.HTTP_TIMEOUT_SECONDS, retries config.HTTP_MAX_RETRIES times with
backoff, and pauses config.HTTP_INTER_REQUEST_SECONDS between requests to one
host. Spec section 11 asks for a descriptive User-Agent and backoff on errors.
"""
