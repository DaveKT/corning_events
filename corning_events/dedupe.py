"""Cross-source deduplication.

Lands in M3. Buckets candidates by start date, applies the four rule cascade
from spec section 10, forms clusters by union-find, and resolves conflicting
field values by the trust order in config.SOURCES.
"""
