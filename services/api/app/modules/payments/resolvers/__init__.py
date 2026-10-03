"""
SBP Payment link resolvers package.
"""

from app.modules.payments.resolvers.antilopay import AntilopaySBPResolver
from app.modules.payments.resolvers.base import SBPResolver
from app.modules.payments.resolvers.cardlink import CardlinkSBPResolver
from app.modules.payments.resolvers.service import resolve_sbp_link

__all__ = [
    "AntilopaySBPResolver",
    "CardlinkSBPResolver",
    "SBPResolver",
    "resolve_sbp_link",
]
