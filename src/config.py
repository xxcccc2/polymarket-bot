"""
Polymarket Micro-Spread Trading Bot - Configuration
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
PROJECT_ROOT = Path(__file__).parent.parent
load_dotenv(dotenv_path=PROJECT_ROOT / '.env')

# =============================================================================
# POLYMARKET API SETTINGS
# =============================================================================
CLOB_HOST = "https://clob.polymarket.com"
GAMMA_HOST = "https://gamma-api.polymarket.com"
WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
CHAIN_ID = 137  # Polygon mainnet

# =============================================================================
# CREDENTIALS (from .env)
# =============================================================================
PRIVATE_KEY = os.getenv("POLYMARKET_PRIVATE_KEY", "")
PROXY_ADDRESS = os.getenv("POLYMARKET_PROXY_ADDRESS", "")

# Signature type: 1 = Email/Magic, 2 = Browser wallet (MetaMask, Binance, etc.)
SIGNATURE_TYPE = 2

# =============================================================================
# TRADING PARAMETERS
# =============================================================================
# Minimum spread in cents to consider profitable (after fees)
# Note: Most crypto markets have 0.1-1c spreads. Set to 1 for real trading.
MIN_SPREAD_CENTS = float(os.getenv("MIN_SPREAD_CENTS", "1"))

# Order size per trade in USD
ORDER_SIZE_USD = float(os.getenv("ORDER_SIZE_USD", "10"))

# Maximum position per market in USD
MAX_POSITION_USD = float(os.getenv("MAX_POSITION_USD", "100"))

# Maximum total exposure across all markets
MAX_TOTAL_EXPOSURE_USD = float(os.getenv("MAX_TOTAL_EXPOSURE_USD", "500"))

# =============================================================================
# RISK MANAGEMENT
# =============================================================================
# Daily loss limit - stop trading if reached
DAILY_LOSS_LIMIT_USD = float(os.getenv("DAILY_LOSS_LIMIT_USD", "50"))

# Daily profit target (optional)
DAILY_PROFIT_TARGET_USD = float(os.getenv("DAILY_PROFIT_TARGET_USD", "100"))

# Minimum balance to maintain
MIN_BALANCE_USD = float(os.getenv("MIN_BALANCE_USD", "50"))

# Maximum concurrent active orders
MAX_ACTIVE_ORDERS = int(os.getenv("MAX_ACTIVE_ORDERS", "20"))

# Order timeout - cancel orders older than this (seconds)
ORDER_TIMEOUT_SECONDS = int(os.getenv("ORDER_TIMEOUT_SECONDS", "300"))

# =============================================================================
# MARKET FILTERS
# =============================================================================
# Only trade prices between these bounds (avoid near-resolution)
MIN_PRICE_CENTS = float(os.getenv("MIN_PRICE_CENTS", "5"))
MAX_PRICE_CENTS = float(os.getenv("MAX_PRICE_CENTS", "95"))

# Minimum 24h volume to consider market
MIN_VOLUME_USD = float(os.getenv("MIN_VOLUME_USD", "10000"))

# Keywords to identify crypto price prediction markets
CRYPTO_MARKET_KEYWORDS = [
    "bitcoin", "btc", "ethereum", "eth", "solana", "sol",
    "xrp", "ripple", "crypto", "price"
]

# =============================================================================
# BOT BEHAVIOR
# =============================================================================
# How often to scan for opportunities (seconds)
SCAN_INTERVAL_SECONDS = float(os.getenv("SCAN_INTERVAL_SECONDS", "5"))

# Paper trading mode (no real orders)
PAPER_TRADING = os.getenv("PAPER_TRADING", "true").lower() == "true"

# Log level
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# =============================================================================
# RATE LIMITS (Polymarket CLOB)
# =============================================================================
# POST /order: 240/sec burst, 40/sec sustained
ORDER_RATE_LIMIT_BURST = 240
ORDER_RATE_LIMIT_SUSTAINED = 40

# General endpoints: lower limits
GENERAL_RATE_LIMIT = 100

# =============================================================================
# FEE STRUCTURE
# =============================================================================
# Polymarket trading fee (1% = 0.01)
TRADING_FEE_RATE = 0.01

# Minimum profit margin after fees to execute trade
MIN_PROFIT_MARGIN = 0.005  # 0.5%

# =============================================================================
# DATA PATHS
# =============================================================================
DATA_DIR = PROJECT_ROOT / "data"
LOGS_DIR = PROJECT_ROOT / "logs"

# Create directories if they don't exist
DATA_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)


def validate_config():
    """Validate required configuration is present"""
    errors = []
    
    if not PRIVATE_KEY:
        errors.append("POLYMARKET_PRIVATE_KEY not set in .env")
    
    if not PROXY_ADDRESS:
        errors.append("POLYMARKET_PROXY_ADDRESS not set in .env")
    
    if MIN_SPREAD_CENTS < 0.1:
        errors.append("MIN_SPREAD_CENTS should be at least 0.1 cent")
    
    if ORDER_SIZE_USD < 1:
        errors.append("ORDER_SIZE_USD should be at least $1")
    
    return errors


def print_config():
    """Print current configuration (hiding sensitive data)"""
    from termcolor import cprint
    
    cprint("\n" + "="*60, "cyan")
    cprint("📋 Bot Configuration", "cyan", attrs=["bold"])
    cprint("="*60, "cyan")
    
    cprint(f"  Private Key: {'*' * 10}...{PRIVATE_KEY[-4:] if PRIVATE_KEY else 'NOT SET'}", "yellow")
    cprint(f"  Proxy Address: {PROXY_ADDRESS[:10]}...{PROXY_ADDRESS[-4:] if PROXY_ADDRESS else 'NOT SET'}", "yellow")
    cprint(f"  Signature Type: {SIGNATURE_TYPE} (Browser Wallet)", "white")
    
    cprint("\n📊 Trading Parameters:", "cyan")
    cprint(f"  Min Spread: {MIN_SPREAD_CENTS}¢", "white")
    cprint(f"  Order Size: ${ORDER_SIZE_USD}", "white")
    cprint(f"  Max Position/Market: ${MAX_POSITION_USD}", "white")
    cprint(f"  Max Total Exposure: ${MAX_TOTAL_EXPOSURE_USD}", "white")
    
    cprint("\n⚠️  Risk Management:", "cyan")
    cprint(f"  Daily Loss Limit: ${DAILY_LOSS_LIMIT_USD}", "white")
    cprint(f"  Min Balance: ${MIN_BALANCE_USD}", "white")
    cprint(f"  Max Active Orders: {MAX_ACTIVE_ORDERS}", "white")
    cprint(f"  Order Timeout: {ORDER_TIMEOUT_SECONDS}s", "white")
    
    cprint("\n🔧 Bot Settings:", "cyan")
    cprint(f"  Paper Trading: {'✅ ON' if PAPER_TRADING else '❌ OFF (LIVE!)'}", "green" if PAPER_TRADING else "red")
    cprint(f"  Scan Interval: {SCAN_INTERVAL_SECONDS}s", "white")
    cprint(f"  Log Level: {LOG_LEVEL}", "white")
    
    cprint("="*60 + "\n", "cyan")



