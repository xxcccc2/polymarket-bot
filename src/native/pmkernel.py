"""
Native bridge to the bs-p computation library (libpmkernel).

Loads the compiled C shared library via ctypes and exposes typed Python
wrappers around every FFI function.  When the library is unavailable or
NATIVE_ENGINE_ENABLED is false, every function silently falls back to a
pure-Python implementation so the bot keeps running.
"""

from __future__ import annotations

import ctypes
import math
import os
import sys
from ctypes import POINTER, Structure, c_double, c_size_t
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

import numpy as np

# ---------------------------------------------------------------------------
# Library discovery
# ---------------------------------------------------------------------------

_LIB_NAME_DARWIN = "libpmkernel.dylib"
_LIB_NAME_LINUX = "libpmkernel.so"
_LIB_NAME = _LIB_NAME_DARWIN if sys.platform == "darwin" else _LIB_NAME_LINUX

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _find_library() -> Optional[ctypes.CDLL]:
    """Search env var -> lib/ subdir -> None."""
    candidates = []

    env_path = os.getenv("PMKERNEL_LIB_PATH")
    if env_path:
        candidates.append(env_path)

    candidates.append(str(_PROJECT_ROOT / "lib" / _LIB_NAME))

    for path in candidates:
        if os.path.isfile(path):
            try:
                return ctypes.CDLL(path)
            except OSError:
                continue
    return None


def _engine_enabled() -> bool:
    return os.getenv("NATIVE_ENGINE_ENABLED", "true").lower() != "false"


_lib: Optional[ctypes.CDLL] = None
if _engine_enabled():
    _lib = _find_library()

NATIVE_AVAILABLE: bool = _lib is not None

# ---------------------------------------------------------------------------
# ctypes struct mirrors
# ---------------------------------------------------------------------------

class GreekOut(Structure):
    _fields_ = [
        ("delta_x", c_double),
        ("gamma_x", c_double),
    ]

# ---------------------------------------------------------------------------
# Signature declarations
# ---------------------------------------------------------------------------

_DP = POINTER(c_double)

if _lib is not None:
    # kernel.h
    _lib.kernel_sigmoid.argtypes = [c_double]
    _lib.kernel_sigmoid.restype = c_double

    _lib.kernel_logit.argtypes = [c_double]
    _lib.kernel_logit.restype = c_double

    _lib.kernel_sigmoid_batch.argtypes = [_DP, _DP, c_size_t]
    _lib.kernel_sigmoid_batch.restype = None

    _lib.kernel_logit_batch.argtypes = [_DP, _DP, c_size_t]
    _lib.kernel_logit_batch.restype = None

    _lib.kernel_greeks_from_logit.argtypes = [c_double, _DP, _DP]
    _lib.kernel_greeks_from_logit.restype = None

    _lib.kernel_greeks_batch.argtypes = [_DP, POINTER(GreekOut), c_size_t]
    _lib.kernel_greeks_batch.restype = None

    _lib.calculate_quotes_logit.argtypes = [
        _DP, _DP, _DP, _DP, _DP, _DP,  # x_t, q_t, sigma_b, gamma, tau, k
        _DP, _DP,                        # bid_p, ask_p (out)
        c_size_t,
    ]
    _lib.calculate_quotes_logit.restype = None

    # analytics.h
    _lib.implied_belief_volatility_batch.argtypes = [
        _DP, _DP, _DP, _DP, _DP, _DP,  # bid_p, ask_p, q_t, gamma, tau, k
        _DP,                              # out_sigma_b
        c_size_t,
    ]
    _lib.implied_belief_volatility_batch.restype = None

    _lib.simulate_shock_logit_batch.argtypes = [
        _DP, _DP, _DP, _DP, _DP, _DP,  # x_t, q_t, sigma_b, gamma, tau, k
        _DP,                              # shock_p
        _DP, _DP, _DP,                   # out_r_x, out_bid_p, out_ask_p
        POINTER(GreekOut),                # out_greeks
        _DP,                              # out_pnl_shift
        c_size_t,
    ]
    _lib.simulate_shock_logit_batch.restype = None

    _lib.adaptive_kelly_clip_batch.argtypes = [
        _DP, _DP, _DP, _DP, _DP, _DP,  # belief, market, q_t, gamma, risk_limit, max_clip
        _DP, _DP,                        # out_maker, out_taker
        c_size_t,
    ]
    _lib.adaptive_kelly_clip_batch.restype = None

    _lib.order_book_microstructure_batch.argtypes = [
        _DP, _DP, _DP, _DP,  # bid_p, ask_p, bid_vol, ask_vol
        _DP, _DP, _DP, _DP,  # out_obi, out_vwm_p, out_vwm_x, out_pressure
        c_size_t,
    ]
    _lib.order_book_microstructure_batch.restype = None

    _lib.aggregate_portfolio_greeks.argtypes = [
        _DP, _DP, _DP,  # positions, delta_x, gamma_x
        _DP, _DP,        # weights (nullable), corr_matrix (nullable)
        c_size_t,
        _DP, _DP,        # out_net_delta, out_net_gamma
    ]
    _lib.aggregate_portfolio_greeks.restype = None

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _d(arr: np.ndarray) -> ctypes.Array:
    """Convert numpy float64 array to a ctypes double pointer (zero-copy)."""
    return arr.ctypes.data_as(_DP)


def _f64(values) -> np.ndarray:
    """Ensure input is a contiguous float64 numpy array."""
    return np.ascontiguousarray(values, dtype=np.float64)


def _scalar_arr(v: float) -> np.ndarray:
    """Wrap a scalar in a length-1 float64 array."""
    return np.array([v], dtype=np.float64)

# ---------------------------------------------------------------------------
# Constants (match C source)
# ---------------------------------------------------------------------------

_EPS = 1e-12
_ONE_MINUS_EPS = 1.0 - 1e-12
_SIGMOID_CLIP = 10.0

# ---------------------------------------------------------------------------
# Pure-Python fallbacks
# ---------------------------------------------------------------------------

def _py_sigmoid(x: float) -> float:
    xc = max(-_SIGMOID_CLIP, min(_SIGMOID_CLIP, x))
    return max(_EPS, min(_ONE_MINUS_EPS, 1.0 / (1.0 + math.exp(-xc))))


def _py_logit(p: float) -> float:
    pc = max(_EPS, min(_ONE_MINUS_EPS, p))
    return math.log(pc / (1.0 - pc))

# ---------------------------------------------------------------------------
# Public API — scalar functions
# ---------------------------------------------------------------------------

def sigmoid(x: float) -> float:
    """Map logit to probability."""
    if _lib is not None:
        return _lib.kernel_sigmoid(x)
    return _py_sigmoid(x)


def logit(p: float) -> float:
    """Map probability to logit."""
    if _lib is not None:
        return _lib.kernel_logit(p)
    return _py_logit(p)


def greeks_from_logit(x: float) -> Tuple[float, float]:
    """Compute (delta, gamma) at logit value *x*."""
    if _lib is not None:
        delta = c_double()
        gamma = c_double()
        _lib.kernel_greeks_from_logit(x, ctypes.byref(delta), ctypes.byref(gamma))
        return delta.value, gamma.value
    p = _py_sigmoid(x)
    d = p * (1.0 - p)
    return d, d * (1.0 - 2.0 * p)

# ---------------------------------------------------------------------------
# Public API — batch functions
# ---------------------------------------------------------------------------

def sigmoid_batch(x: np.ndarray) -> np.ndarray:
    """Map array of logits to probabilities."""
    x = _f64(x)
    out = np.empty_like(x)
    if _lib is not None:
        _lib.kernel_sigmoid_batch(_d(x), _d(out), len(x))
    else:
        out[:] = np.array([_py_sigmoid(v) for v in x])
    return out


def logit_batch(p: np.ndarray) -> np.ndarray:
    """Map array of probabilities to logits."""
    p = _f64(p)
    out = np.empty_like(p)
    if _lib is not None:
        _lib.kernel_logit_batch(_d(p), _d(out), len(p))
    else:
        out[:] = np.array([_py_logit(v) for v in p])
    return out


def greeks_batch(x: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Compute (delta, gamma) arrays for logit array *x*."""
    x = _f64(x)
    n = len(x)
    if _lib is not None:
        out = (GreekOut * n)()
        _lib.kernel_greeks_batch(_d(x), out, n)
        delta = np.array([out[i].delta_x for i in range(n)])
        gamma = np.array([out[i].gamma_x for i in range(n)])
    else:
        probs = sigmoid_batch(x)
        delta = probs * (1.0 - probs)
        gamma = delta * (1.0 - 2.0 * probs)
    return delta, gamma

# ---------------------------------------------------------------------------
# Quoting engine
# ---------------------------------------------------------------------------

@dataclass
class QuoteResult:
    bid_p: float
    ask_p: float
    spread: float


def calculate_quotes(
    x_t: float,
    q_t: float,
    sigma_b: float,
    gamma: float,
    tau: float,
    k: float,
) -> QuoteResult:
    """Avellaneda-Stoikov optimal bid/ask for a single market."""
    arr_x = _scalar_arr(x_t)
    arr_q = _scalar_arr(q_t)
    arr_s = _scalar_arr(sigma_b)
    arr_g = _scalar_arr(gamma)
    arr_t = _scalar_arr(tau)
    arr_k = _scalar_arr(k)
    bid = np.empty(1, dtype=np.float64)
    ask = np.empty(1, dtype=np.float64)

    if _lib is not None:
        _lib.calculate_quotes_logit(
            _d(arr_x), _d(arr_q), _d(arr_s), _d(arr_g), _d(arr_t), _d(arr_k),
            _d(bid), _d(ask), 1,
        )
    else:
        g = max(0.0, gamma)
        t = max(0.0, tau)
        kv = max(_EPS, k)
        s2 = sigma_b * sigma_b
        risk_term = g * s2 * t
        r_x = x_t - q_t * risk_term
        delta_x = 0.5 * risk_term + math.log1p(g / kv) / kv
        bid[0] = _py_sigmoid(r_x - delta_x)
        ask[0] = _py_sigmoid(r_x + delta_x)

    return QuoteResult(bid_p=bid[0], ask_p=ask[0], spread=ask[0] - bid[0])


def calculate_quotes_batch(
    x_t: np.ndarray,
    q_t: np.ndarray,
    sigma_b: np.ndarray,
    gamma: np.ndarray,
    tau: np.ndarray,
    k: np.ndarray,
) -> Tuple[np.ndarray, np.ndarray]:
    """Batch Avellaneda-Stoikov quoting. Returns (bid_p, ask_p) arrays."""
    x_t, q_t, sigma_b = _f64(x_t), _f64(q_t), _f64(sigma_b)
    gamma, tau, k = _f64(gamma), _f64(tau), _f64(k)
    n = len(x_t)
    bid = np.empty(n, dtype=np.float64)
    ask = np.empty(n, dtype=np.float64)

    if _lib is not None:
        _lib.calculate_quotes_logit(
            _d(x_t), _d(q_t), _d(sigma_b), _d(gamma), _d(tau), _d(k),
            _d(bid), _d(ask), n,
        )
    else:
        for i in range(n):
            r = calculate_quotes(x_t[i], q_t[i], sigma_b[i], gamma[i], tau[i], k[i])
            bid[i], ask[i] = r.bid_p, r.ask_p

    return bid, ask

# ---------------------------------------------------------------------------
# Analytics — implied volatility
# ---------------------------------------------------------------------------

def implied_belief_vol(
    bid_p: float,
    ask_p: float,
    q_t: float,
    gamma: float,
    tau: float,
    k: float,
) -> float:
    """Invert market spread to recover implied belief volatility (single market)."""
    arr_b = _scalar_arr(bid_p)
    arr_a = _scalar_arr(ask_p)
    arr_q = _scalar_arr(q_t)
    arr_g = _scalar_arr(gamma)
    arr_t = _scalar_arr(tau)
    arr_k = _scalar_arr(k)
    out = np.empty(1, dtype=np.float64)

    if _lib is not None:
        _lib.implied_belief_volatility_batch(
            _d(arr_b), _d(arr_a), _d(arr_q), _d(arr_g), _d(arr_t), _d(arr_k),
            _d(out), 1,
        )
    else:
        g = max(0.0, gamma)
        t = max(0.0, tau)
        kv = max(_EPS, k)
        if ask_p <= bid_p or g <= _EPS or t <= _EPS:
            return 0.0
        target = _py_logit(ask_p) - _py_logit(bid_p)
        two_nl = 2.0 * math.log1p(g / kv) / kv
        gt = max(1e-9, g * t)
        sigma = math.sqrt(max(0.0, (target - two_nl) / gt))
        for _ in range(4):
            f = gt * sigma * sigma + two_nl - target
            fp = max(1e-9, 2.0 * gt * sigma)
            sigma = max(0.0, sigma - f / fp)
        out[0] = sigma

    return float(out[0])


def implied_belief_vol_batch(
    bid_p: np.ndarray,
    ask_p: np.ndarray,
    q_t: np.ndarray,
    gamma: np.ndarray,
    tau: np.ndarray,
    k: np.ndarray,
) -> np.ndarray:
    """Batch implied belief volatility."""
    bid_p, ask_p, q_t = _f64(bid_p), _f64(ask_p), _f64(q_t)
    gamma, tau, k = _f64(gamma), _f64(tau), _f64(k)
    n = len(bid_p)
    out = np.empty(n, dtype=np.float64)

    if _lib is not None:
        _lib.implied_belief_volatility_batch(
            _d(bid_p), _d(ask_p), _d(q_t), _d(gamma), _d(tau), _d(k),
            _d(out), n,
        )
    else:
        for i in range(n):
            out[i] = implied_belief_vol(bid_p[i], ask_p[i], q_t[i], gamma[i], tau[i], k[i])

    return out

# ---------------------------------------------------------------------------
# Analytics — Kelly sizing
# ---------------------------------------------------------------------------

@dataclass
class KellyClipResult:
    maker_clip: float
    taker_clip: float


def adaptive_kelly_clip(
    belief_p: float,
    market_p: float,
    q_t: float,
    gamma: float,
    risk_limit: float,
    max_clip: float,
) -> KellyClipResult:
    """Inventory-aware Kelly sizing for a single market."""
    arr_bp = _scalar_arr(belief_p)
    arr_mp = _scalar_arr(market_p)
    arr_q = _scalar_arr(q_t)
    arr_g = _scalar_arr(gamma)
    arr_rl = _scalar_arr(risk_limit)
    arr_mc = _scalar_arr(max_clip)
    maker = np.empty(1, dtype=np.float64)
    taker = np.empty(1, dtype=np.float64)

    if _lib is not None:
        _lib.adaptive_kelly_clip_batch(
            _d(arr_bp), _d(arr_mp), _d(arr_q), _d(arr_g), _d(arr_rl), _d(arr_mc),
            _d(maker), _d(taker), 1,
        )
    else:
        b = max(_EPS, min(_ONE_MINUS_EPS, belief_p))
        m = max(_EPS, min(_ONE_MINUS_EPS, market_p))
        edge = b - m
        variance = max(_EPS, m * (1.0 - m))
        kelly_frac = edge / variance
        g = max(0.0, gamma)
        inv_scale = 1.0 / (1.0 + g * abs(q_t))
        risk = max(_EPS, risk_limit)
        clip_cap = max(_EPS, max_clip)

        t = kelly_frac * risk * inv_scale
        t = max(-clip_cap, min(clip_cap, t))
        long_lim = risk - q_t
        short_lim = -risk - q_t
        t = max(short_lim, min(long_lim, t))
        mk = max(short_lim, min(long_lim, 0.5 * t))

        maker[0] = mk
        taker[0] = t

    return KellyClipResult(maker_clip=float(maker[0]), taker_clip=float(taker[0]))

# ---------------------------------------------------------------------------
# Analytics — orderbook microstructure
# ---------------------------------------------------------------------------

@dataclass
class MicrostructureResult:
    obi: float
    vwm_p: float
    vwm_x: float
    pressure: float


def order_book_microstructure(
    bid_p: float,
    ask_p: float,
    bid_vol: float,
    ask_vol: float,
) -> MicrostructureResult:
    """OBI, VWAP mid, and directional pressure for a single market."""
    arr_bp = _scalar_arr(bid_p)
    arr_ap = _scalar_arr(ask_p)
    arr_bv = _scalar_arr(bid_vol)
    arr_av = _scalar_arr(ask_vol)
    out_obi = np.empty(1, dtype=np.float64)
    out_vwm_p = np.empty(1, dtype=np.float64)
    out_vwm_x = np.empty(1, dtype=np.float64)
    out_press = np.empty(1, dtype=np.float64)

    if _lib is not None:
        _lib.order_book_microstructure_batch(
            _d(arr_bp), _d(arr_ap), _d(arr_bv), _d(arr_av),
            _d(out_obi), _d(out_vwm_p), _d(out_vwm_x), _d(out_press), 1,
        )
    else:
        b = max(_EPS, min(_ONE_MINUS_EPS, bid_p))
        a = max(_EPS, min(_ONE_MINUS_EPS, ask_p))
        bv = max(0.0, bid_vol)
        av = max(0.0, ask_vol)
        vol_sum = max(_EPS, bv + av)
        obi = (bv - av) / vol_sum
        vwm = max(_EPS, min(_ONE_MINUS_EPS, (a * bv + b * av) / vol_sum))
        mid = 0.5 * (b + a)
        spread = max(_EPS, a - b)
        pressure = obi + (vwm - mid) / spread
        out_obi[0] = obi
        out_vwm_p[0] = vwm
        out_vwm_x[0] = _py_logit(vwm)
        out_press[0] = pressure

    return MicrostructureResult(
        obi=float(out_obi[0]),
        vwm_p=float(out_vwm_p[0]),
        vwm_x=float(out_vwm_x[0]),
        pressure=float(out_press[0]),
    )

# ---------------------------------------------------------------------------
# Analytics — shock testing
# ---------------------------------------------------------------------------

@dataclass
class ShockResult:
    r_x: np.ndarray
    bid_p: np.ndarray
    ask_p: np.ndarray
    delta: np.ndarray
    gamma: np.ndarray
    pnl_shift: np.ndarray


def simulate_shock(
    x_t: np.ndarray,
    q_t: np.ndarray,
    sigma_b: np.ndarray,
    gamma: np.ndarray,
    tau: np.ndarray,
    k: np.ndarray,
    shock_p: np.ndarray,
) -> ShockResult:
    """Stress-test positions under probability shocks."""
    x_t, q_t, sigma_b = _f64(x_t), _f64(q_t), _f64(sigma_b)
    gamma, tau, k, shock_p = _f64(gamma), _f64(tau), _f64(k), _f64(shock_p)
    n = len(x_t)
    out_r = np.empty(n, dtype=np.float64)
    out_bid = np.empty(n, dtype=np.float64)
    out_ask = np.empty(n, dtype=np.float64)
    out_pnl = np.empty(n, dtype=np.float64)

    if _lib is not None:
        out_greeks = (GreekOut * n)()
        _lib.simulate_shock_logit_batch(
            _d(x_t), _d(q_t), _d(sigma_b), _d(gamma), _d(tau), _d(k), _d(shock_p),
            _d(out_r), _d(out_bid), _d(out_ask), out_greeks, _d(out_pnl), n,
        )
        delta = np.array([out_greeks[i].delta_x for i in range(n)])
        gamma_arr = np.array([out_greeks[i].gamma_x for i in range(n)])
    else:
        delta = np.empty(n, dtype=np.float64)
        gamma_arr = np.empty(n, dtype=np.float64)
        for i in range(n):
            base_p = _py_sigmoid(x_t[i])
            shocked_p = max(_EPS, min(_ONE_MINUS_EPS, base_p + shock_p[i]))
            x_sh = _py_logit(shocked_p)
            gv = max(0.0, gamma[i])
            tv = max(0.0, tau[i])
            kv = max(_EPS, k[i])
            s2 = sigma_b[i] ** 2
            rt = gv * s2 * tv
            rx = x_sh - q_t[i] * rt
            hs = 0.5 * rt + math.log1p(gv / kv) / kv
            out_r[i] = rx
            out_bid[i] = _py_sigmoid(rx - hs)
            out_ask[i] = _py_sigmoid(rx + hs)
            p_sh = _py_sigmoid(x_sh)
            d = p_sh * (1.0 - p_sh)
            delta[i] = d
            gamma_arr[i] = d * (1.0 - 2.0 * p_sh)
            out_pnl[i] = q_t[i] * (shocked_p - base_p)

    return ShockResult(
        r_x=out_r, bid_p=out_bid, ask_p=out_ask,
        delta=delta, gamma=gamma_arr, pnl_shift=out_pnl,
    )

# ---------------------------------------------------------------------------
# Analytics — portfolio greeks
# ---------------------------------------------------------------------------

@dataclass
class PortfolioGreeks:
    net_delta: float
    net_gamma: float


def aggregate_portfolio_greeks(
    positions: np.ndarray,
    delta_x: np.ndarray,
    gamma_x: np.ndarray,
    weights: Optional[np.ndarray] = None,
    corr_matrix: Optional[np.ndarray] = None,
) -> PortfolioGreeks:
    """Aggregate delta/gamma across a portfolio of positions."""
    positions, delta_x, gamma_x = _f64(positions), _f64(delta_x), _f64(gamma_x)
    n = len(positions)
    out_d = c_double(0.0)
    out_g = c_double(0.0)

    if _lib is not None:
        w_ptr = _d(_f64(weights)) if weights is not None else None
        c_ptr = _d(_f64(corr_matrix.ravel())) if corr_matrix is not None else None
        _lib.aggregate_portfolio_greeks(
            _d(positions), _d(delta_x), _d(gamma_x),
            w_ptr, c_ptr, n,
            ctypes.byref(out_d), ctypes.byref(out_g),
        )
    else:
        nd = 0.0
        ng = 0.0
        for i in range(n):
            w = weights[i] if weights is not None else 1.0
            nd += positions[i] * delta_x[i] * w
            ng += positions[i] * gamma_x[i] * w
        out_d.value = nd
        out_g.value = ng

    return PortfolioGreeks(net_delta=out_d.value, net_gamma=out_g.value)
