"""Deprecated compatibility shim for misspelled 'registery.py'.

Canonical tool registry is located at 'customer_support.registry'.
This module re-exports all components from customer_support.registry.
"""

import warnings
from customer_support.registry import *
from customer_support.registry import registry, get_registry

warnings.warn(
    "customer_support.registery is deprecated and will be removed. "
    "Import from customer_support.registry instead.",
    DeprecationWarning,
    stacklevel=2,
)