#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wallet_gen.py - multithreaded version

Features:
- Load keys from .env (DEBANK_ACCESS_KEY, ALCHEMY_API_KEY)
- Load config.json, inject ${ALCHEMY_API_KEY}
- Build Web3 providers for EVM chains
- Use ThreadPoolExecutor to parallelize:
    - DeBank API calls per address
    - native balance checks across chains
- Save results to OUTPUT_FILE (default hasil.json)
"""

import os
import json
import time
from decimal import Decimal, getcontext
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import partial

import requests
from eth_account import Account
from dotenv import load_dotenv

# Optional: web3 may be used; script will skip RPC checks if web3 import fails
try:
    from web3 import Web3, HTTPProvider
except Exception:
    Web3 = None
    HTTPProvider = None

getcontext().prec = 36
load_dotenv()

# ----------------- ENV / CONFIG -----------------
DEBANK_ACCESS_KEY = os.getenv("DEBANK_ACCESS_KEY")
ALCHEMY_API_KEY = os.getenv("ALCHEMY_API_KEY")
CONFIG_FILE = os.getenv("CONFIG_FILE", "config.json")
OUTPUT_FILE = os.getenv("OUTPUT_FILE", "hasil.json")
DEBANK_BASE_URL = os.getenv("DEBANK_BASE_URL", "https://pro-openapi.debank.com")
DEBANK_TIMEOUT = int(os.getenv("DEBANK_TIMEOUT", "15"))
DEBUG_MODE = os.getenv("DEBUG_MODE", "False").lower() == "true"
DEFAULT_WORKERS = int(os.getenv("CONCURRENT_WORKERS", "16"))

# ----------------- Helpers -----------------
def debug(*args):
    if DEBUG_MODE:
        print("[DEBUG]", *args)

def load_json_file(path):
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_json_file(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

# ----------------- Config / RPC setup -----------------
def inject_alchemy_key(cfg):
    """Replace ${ALCHEMY_API_KEY} placeholders with env value."""
    if not ALCHEMY_API_KEY:
        debug("No ALCHEMY_API_KEY set in .env; URLs left unmodified.")
        return cfg
    rpcs = cfg.get("rpcs", {})
    for k, v in rpcs.items():
        url = v.get("rpc_url", "")
        if "${ALCHEMY_API_KEY}" in url:
            v["rpc_url"] = url.replace("${ALCHEMY_API_KEY}", ALCHEMY_API_KEY)
    return cfg

def build_web3_clients(cfg, timeout=10):
    """Create Web3 clients for EVM chains that have rpc_url and evm != False."""
    clients = {}
    if Web3 is None:
        debug("web3 not available; RPC fallback disabled.")
        return clients

    rpcs = cfg.get("rpcs", {})
    for chain, info in rpcs.items():
        if info.get("evm") is False:
            debug(f"Skipping non-EVM chain {chain}")
            continue
        url = info.get("rpc_url")
        if not url:
            debug(f"No rpc_url for chain {chain}; skipping.")
            continue
        try:
            w3 = Web3(HTTPProvider(url, request_kwargs={"timeout": timeout}))
            # quick connectivity test
            if not w3.is_connected():
                debug(f"Web3 not connected for {chain} @ {url[:60]}...")
                continue
            clients[chain] = {"w3": w3, "native_symbol": info.get("native_symbol", "ETH")}
            debug(f"Connected RPC: {chain}")
        except Exception as e:
            debug(f"Failed RPC init {chain}: {e}")
    return clients

# ----------------- Wallet generation -----------------
def create_wallet_obj():
    acct = Account.create()
    # private key bytes: acct.key (HexBytes) or acct._private_key
    priv = None
    if hasattr(acct, "key") and acct.key is not None:
        try:
            priv = acct.key.hex()
        except Exception:
            priv = getattr(acct, "_private_key", b"").hex()
    else:
        priv = getattr(acct, "_private_key", b"").hex()
    return {"address": acct.address, "private_key": priv}

def generate_wallets(count):
    wallets = [create_wallet_obj() for _ in range(count)]
    return wallets

# ----------------- DeBank API call -----------------
def fetch_debank_for_address(address):
    """Return dict: {'coins': {symbol: amount}, 'balance_usd': total} or None on failure."""
    if not DEBANK_ACCESS_KEY:
        debug("No DeBank key; skipping DeBank call.")
        return None
    headers = {"accept": "application/json", "AccessKey": DEBANK_ACCESS_KEY}
    url = f"{DEBANK_BASE_URL}/v1/user/all_token_list"
    params = {"id": address}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=DEBANK_TIMEOUT)
        if r.status_code != 200:
            debug(f"DeBank status {r.status_code} for {address}: {r.text[:200]}")
            return None
        data = r.json()
        items = data.get("data") or []
        coins = {}
        total_usd = Decimal(0)
        for t in items:
            try:
                sym = (t.get("symbol") or "").upper()
                amt = Decimal(str(t.get("amount", 0)))  # DeBank usually returns human-readable amount
                price = Decimal(str(t.get("price", 0))) if t.get("price") is not None else Decimal(0)
                if amt > 0 and sym:
                    coins[sym] = float(amt)
                    total_usd += amt * price
            except Exception as e:
                debug("DeBank item parse err:", e)
        return {"coins": coins, "balance_usd": float(total_usd)}
    except Exception as e:
        debug("DeBank request error:", e)
        return None

# ----------------- RPC native balance (thread-safe) -----------------
def fetch_native_balance_for_chain(client, address):
    """client is dict {'w3': Web3, 'native_symbol': 'ETH'}"""
    try:
        w3 = client["w3"]
        bal_wei = w3.eth.get_balance(address)
        # convert to ether-like (18 decimals). Most EVM chains are 18; others may differ.
        val = Decimal(bal_wei) / Decimal(10 ** 18)
        return float(val)
    except Exception as e:
        debug("RPC balance error:", e)
        return None

# ----------------- Orchestration (multithreaded) -----------------
def enrich_wallet_multithread(wallets, cfg, web3_clients, max_workers=DEFAULT_WORKERS):
    """
    For each wallet:
      - call DeBank API (parallel across wallets)
      - call native RPC balances across chains (parallel)
    Returns enriched wallet dicts list
    """
    results = []
    rpcs_cfg = cfg.get("rpcs", {})

    # 1) Fetch DeBank for all wallets in parallel (if key present)
    debank_futures = {}
    with ThreadPoolExecutor(max_workers=max_workers) as exc:
        for w in wallets:
            debank_futures[exc.submit(fetch_debank_for_address, w["address"])] = w

        # as debank results complete, attach to wallet
        debank_map = {}
        for fut in as_completed(debank_futures):
            w = debank_futures[fut]
            debank_res = None
            try:
                debank_res = fut.result()
            except Exception as e:
                debug("DeBank future error:", e)
            debank_map[w["address"]] = debank_res

    # 2) For native balances: for each wallet, we will submit per-chain tasks to executor
    # Use a thread pool to run all (wallet x chain) tasks concurrently; cap workers
    native_futures = {}
    with ThreadPoolExecutor(max_workers=max_workers) as exc:
        for w in wallets:
            for chain, info in web3_clients.items():
                # submit fetch for this wallet+chain
                native_futures[exc.submit(fetch_native_balance_for_chain, info, w["address"])] = (w, chain)
        # collect
        native_map = {}  # (address) -> dict(chain->value)
        for fut in as_completed(native_futures):
            w, chain = native_futures[fut]
            address = w["address"]
            try:
                val = fut.result()
            except Exception as e:
                debug("Native future err:", e)
                val = None
            native_map.setdefault(address, {})[chain] = val

    # 3) Aggregate results per wallet into final structure
    for w in wallets:
        addr = w["address"]
        entry = {
            "address": addr,
            "balance": 0.0,
            "chains": [],
            "coins": {},
            "private_key": w["private_key"]
        }
        # merge DeBank tokens (if any)
        debank_data = debank_map.get(addr)
        if debank_data:
            # coins, and USD total
            entry["coins"].update(debank_data.get("coins", {}))
            # Use DEBANK USD total as 'balance' base (if >0)
            try:
                entry["balance"] = float(debank_data.get("balance_usd", 0.0))
            except Exception:
                entry["balance"] = 0.0

        # merge native balances from RPCs
        native_for_addr = native_map.get(addr, {})
        native_total_units = Decimal(0)
        for chain, val in native_for_addr.items():
            if val is None:
                continue
            sym = web3_clients[chain].get("native_symbol", chain.upper())
            # Only include if > 0 (avoid filling with zeroes)
            if val > 0:
                entry["chains"].append(chain)
                # if same symbol already exists (from debank), add numeric; else set
                prev = Decimal(str(entry["coins"].get(sym, 0.0)))
                new = prev + Decimal(str(val))
                entry["coins"][sym] = float(new)
                native_total_units += Decimal(str(val))
        # If DeBank provided USD balance it's already used; otherwise set balance to native sum
        if not debank_data:
            # use native total as fallback balance (note: unit-mixed; not USD)
            entry["balance"] = float(native_total_units)

        results.append(entry)
    return results

# ----------------- Menu / CLI -----------------
def menu_loop(cfg, web3_clients):
    while True:
        print("""
=== WALLET GENERATOR (MULTITHREAD) ===
1) Generate 1 - 10 wallet
2) Generate 10 - 100 wallet
3) Generate 100 - 1000 wallet
4) Generate manual (1 - unlimited)
5) Exit
""")
        ch = input("Pilih (1-5): ").strip()
        if ch == "1":
            n = int(input("Masukkan jumlah (1-10), or press Enter for random: ") or 1)
            if n < 1 or n > 10:
                print("Range salah. Membatalkan.")
                continue
        elif ch == "2":
            n = int(input("Masukkan jumlah (10-100), or press Enter for default 10: ") or 10)
            if n < 10 or n > 100:
                print("Range salah. Membatalkan.")
                continue
        elif ch == "3":
            n = int(input("Masukkan jumlah (100-1000), or press Enter for default 100: ") or 100)
            if n < 100 or n > 1000:
                print("Range salah. Membatalkan.")
                continue
        elif ch == "4":
            n = int(input("Masukkan jumlah wallet (1 - unlimited): "))
            if n < 1:
                print("Jumlah harus >=1.")
                continue
        elif ch == "5":
            print("Keluar.")
            break
        else:
            print("Pilihan tidak valid.")
            continue

        print(f"[+] Generating {n} wallets ...")
        wallets = generate_wallets(n)
        print("[+] Wallets generated. Fetching balances (multi-threaded)...")
        start = time.time()
        max_workers = cfg.get("concurrent_workers") or DEFAULT_WORKERS
        enriched = enrich_wallet_multithread(wallets, cfg, web3_clients, max_workers=int(max_workers))
        elapsed = time.time() - start
        print(f"[+] Done fetching in {elapsed:.2f}s. Writing {OUTPUT_FILE} ...")
        save_json_file(OUTPUT_FILE, enriched)
        print("[+] Finished. Output written.\n")

# ----------------- Main -----------------
def main():
    cfg = load_json_file(CONFIG_FILE)
    if not cfg:
        print(f"[!] '{CONFIG_FILE}' empty or missing. Create it first.")
        return

    # inject alchemy key into URLs
    cfg = inject_alchemy_key(cfg)

    # prepare web3 clients
    web3_clients = build_web3_clients(cfg)

    # security note
    if not DEBANK_ACCESS_KEY:
        print("[!] Warning: DEBANK_ACCESS_KEY not found in .env. DeBank integration disabled.")
    if not ALCHEMY_API_KEY:
        print("[!] Warning: ALCHEMY_API_KEY not found in .env. RPC URLs will remain as-is (may fail).")

    # run menu
    menu_loop(cfg, web3_clients)

if __name__ == "__main__":
    main()
