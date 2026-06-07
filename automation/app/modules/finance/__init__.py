"""finance — domain namespace package.

This __init__.py intentionally has NO register() function.
The registry detects this and treats finance/ as a domain namespace,
scanning one level deeper to load each submodule (watchlist/, technical/, etc.)
"""
