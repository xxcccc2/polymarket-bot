"""
Wallet rotation module for wallet_copy strategy.

Discovers, scores, and rotates tracked wallets based on inactivity.
Designed for runtime use inside WalletCopyStrategy only.
"""

from .rotation_manager import RotationManager, RotationResult
from .candidate_discovery import fetch_candidate_wallets
from .metrics import compute_wallet_metrics
from .scoring import score_wallet

__all__ = [
    "RotationManager",
    "RotationResult",
    "fetch_candidate_wallets",
    "compute_wallet_metrics",
    "score_wallet",
]
