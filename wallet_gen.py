#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wallet_gen.py - STEALTH MODE (Mode A)

Purpose: Random wallet generation + balance scanning
Only saves wallets with balance > 0 or transaction history

Features:
- Load keys from .env (DEBANK_ACCESS_KEY, ALCHEMY_API_KEY)
- Load config.json, inject ${ALCHEMY_API_KEY}
- Generate random wallets
- Multi-threaded balance checking (DeBank + RPC)
- Nonce checking (detect used wallets even with 0 balance)
- Save ONLY wallets with balance or history to hasil.json
- Real-time statistics and progress tracking
"""

import os
import json
import time
from decimal import Decimal, getcontext
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import requests
from eth_account import Account
from dotenv import load_dotenv

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

# ----------------- Global Stats -----------------
STATS = {
    "total_generated": 0,
    "total_checked": 0,
    "wallets_found": 0,
    "start_time": None,
    "last_found": None
}

# ----------------- Helpers -----------------
def debug(*args):
    if DEBUG_MODE:
        print("[DEBUG]", *args)

def load_json_file(path):
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
        return data if isinstance(data, list) else []

def save_json_file(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def append_to_results(wallet_data):
    """Append single wallet to hasil.json (incremental save)"""
    existing = load_json_file(OUTPUT_FILE)
    existing.append(wallet_data)
    save_json_file(OUTPUT_FILE, existing)

def print_stats():
    """Print real-time statistics"""
    elapsed = time.time() - STATS["start_time"] if STATS["start_time"] else 0
    rate = STATS["total_checked"] / elapsed if elapsed > 0 else 0
    
    print(f"\n{'='*60}")
    print(f"📊 STATISTICS")
    print(f"{'='*60}")
    print(f"Generated    : {STATS['total_generated']:,} wallets")
    print(f"Checked      : {STATS['total_checked']:,} wallets")
    print(f"Found (💰)   : {STATS['wallets_found']:,} wallets with balance/history")
    print(f"Success Rate : {(STATS['wallets_found']/STATS['total_checked']*100) if STATS['total_checked'] > 0 else 0:.8f}%")
    print(f"Speed        : {rate:.2f} wallet/s")
    print(f"Runtime      : {elapsed:.2f}s")
    if STATS["last_found"]:
        print(f"Last Found   : {STATS['last_found']}")
    print(f"{'='*60}\n")

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
            if not w3.is_connected():
                debug(f"Web3 not connected for {chain} @ {url[:60]}...")
                continue
            clients[chain] = {
                "w3": w3, 
                "native_symbol": info.get("native_symbol", "ETH"),
                "name": info.get("name", chain)
            }
            debug(f"Connected RPC: {chain}")
        except Exception as e:
            debug(f"Failed RPC init {chain}: {e}")
    return clients

# ----------------- Wallet generation -----------------
def create_wallet_random():
    """Create a completely new random wallet"""
    acct = Account.create()
    priv = None
    if hasattr(acct, "key") and acct.key is not None:
        try:
            priv = acct.key.hex()
        except Exception:
            priv = getattr(acct, "_private_key", b"").hex()
    else:
        priv = getattr(acct, "_private_key", b"").hex()
    
    STATS["total_generated"] += 1
    return {"address": acct.address, "private_key": priv}

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
                amt = Decimal(str(t.get("amount", 0)))
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

# ----------------- RPC checks -----------------
def fetch_native_balance_for_chain(client, address):
    """Get native balance for chain"""
    try:
        w3 = client["w3"]
        bal_wei = w3.eth.get_balance(address)
        val = Decimal(bal_wei) / Decimal(10 ** 18)
        return float(val)
    except Exception as e:
        debug("RPC balance error:", e)
        return None

def fetch_nonce_for_chain(client, address):
    """Check transaction count (nonce) - if > 0, wallet has been used"""
    try:
        w3 = client["w3"]
        nonce = w3.eth.get_transaction_count(address)
        return nonce
    except Exception as e:
        debug("RPC nonce error:", e)
        return 0

# ----------------- Single wallet check -----------------
def check_single_wallet(wallet, web3_clients):
    """
    Check if wallet has balance or transaction history
    Returns enriched wallet dict if found, None if empty
    """
    address = wallet["address"]
    STATS["total_checked"] += 1
    
    # Result structure
    result = {
        "address": address,
        "private_key": wallet["private_key"],
        "balance_usd": 0.0,
        "coins": {},
        "chains": [],
        "nonce": 0,
        "found_at": datetime.now().isoformat()
    }
    
    has_value = False
    
    # 1) Check DeBank
    debank_data = fetch_debank_for_address(address)
    if debank_data:
        coins = debank_data.get("coins", {})
        balance_usd = debank_data.get("balance_usd", 0.0)
        if coins or balance_usd > 0:
            result["coins"].update(coins)
            result["balance_usd"] = balance_usd
            has_value = True
            debug(f"💰 DeBank: {address[:10]}... has ${balance_usd:.2f}")
    
    # 2) Check native balances + nonce across all chains
    max_nonce = 0
    for chain, client in web3_clients.items():
        # Check balance
        bal = fetch_native_balance_for_chain(client, address)
        if bal and bal > 0:
            sym = client.get("native_symbol", chain.upper())
            result["chains"].append(chain)
            prev = Decimal(str(result["coins"].get(sym, 0.0)))
            result["coins"][sym] = float(prev + Decimal(str(bal)))
            has_value = True
            debug(f"💰 {chain}: {address[:10]}... has {bal} {sym}")
        
        # Check nonce (transaction history)
        nonce = fetch_nonce_for_chain(client, address)
        if nonce > max_nonce:
            max_nonce = nonce
    
    result["nonce"] = max_nonce
    
    # Wallet has been used before (even if balance is 0 now)
    if max_nonce > 0:
        has_value = True
        debug(f"📝 {address[:10]}... has {max_nonce} transactions")
    
    # Return result only if wallet has value or history
    if has_value:
        return result
    
    return None

# ----------------- Batch processing -----------------
def scan_wallets_batch(count, web3_clients, max_workers=DEFAULT_WORKERS):
    """
    Generate and scan wallets in batch
    Only save wallets with balance/history
    """
    print(f"\n[+] Starting scan for {count:,} wallets...")
    print(f"[+] Using {max_workers} concurrent workers")
    print(f"[+] Mode: STEALTH (only save wallets with balance/history)\n")
    
    STATS["start_time"] = time.time()
    found_count = 0
    
    # Progress tracking
    last_update = time.time()
    update_interval = 5  # Update stats every 5 seconds
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all wallet generation + checking tasks
        futures = {}
        for i in range(count):
            wallet = create_wallet_random()
            future = executor.submit(check_single_wallet, wallet, web3_clients)
            futures[future] = i
        
        # Process results as they complete
        for future in as_completed(futures):
            idx = futures[future]
            try:
                result = future.result()
                
                # If wallet has value, save immediately
                if result:
                    found_count += 1
                    STATS["wallets_found"] = found_count
                    STATS["last_found"] = result["address"]
                    
                    # Save to file immediately (incremental)
                    append_to_results(result)
                    
                    # Print found wallet info
                    print(f"\n{'🎉'*30}")
                    print(f"💰 WALLET FOUND #{found_count}!")
                    print(f"{'🎉'*30}")
                    print(f"Address    : {result['address']}")
                    print(f"Balance USD: ${result['balance_usd']:.2f}")
                    print(f"Coins      : {result['coins']}")
                    print(f"Chains     : {result['chains']}")
                    print(f"Nonce      : {result['nonce']}")
                    print(f"{'🎉'*30}\n")
                
                # Update stats periodically
                if time.time() - last_update > update_interval:
                    print_stats()
                    last_update = time.time()
                    
            except Exception as e:
                debug(f"Error processing wallet: {e}")
    
    # Final stats
    print_stats()
    print(f"[+] Scan completed!")
    print(f"[+] Results saved to: {OUTPUT_FILE}")
    if found_count > 0:
        print(f"[+] {found_count} wallet(s) with balance/history saved! 🎉\n")
    else:
        print(f"[+] No wallets with balance found in this batch.\n")

# ----------------- Menu / CLI -----------------
def menu_loop(cfg, web3_clients):
    print("""
╔══════════════════════════════════════════════════════════╗
║     WALLET SCANNER - STEALTH MODE (Only Save Found)     ║
╚══════════════════════════════════════════════════════════╝
""")
    
    while True:
        print("""
=== MAIN MENU ===
1) Quick scan (10 wallets)
2) Medium scan (100 wallets)
3) Large scan (1,000 wallets)
4) Mega scan (10,000 wallets)
5) Custom scan (enter amount)
6) View statistics
7) Exit
""")
        ch = input("Choose (1-7): ").strip()
        
        if ch == "1":
            n = 10
        elif ch == "2":
            n = 100
        elif ch == "3":
            n = 1000
        elif ch == "4":
            n = 10000
        elif ch == "5":
            try:
                n = int(input("Enter number of wallets to scan: "))
                if n < 1:
                    print("[!] Number must be >= 1")
                    continue
            except ValueError:
                print("[!] Invalid number")
                continue
        elif ch == "6":
            print_stats()
            continue
        elif ch == "7":
            print("\n[+] Exiting. Good luck! 🍀\n")
            break
        else:
            print("[!] Invalid choice")
            continue
        
        # Confirm large scans
        if n >= 10000:
            confirm = input(f"\n⚠️  Scanning {n:,} wallets may take a while. Continue? (yes/no): ").strip().lower()
            if confirm != "yes":
                print("[+] Cancelled.\n")
                continue
        
        # Run scan
        max_workers = cfg.get("concurrent_workers") or DEFAULT_WORKERS
        scan_wallets_batch(n, web3_clients, max_workers=int(max_workers))

# ----------------- Main -----------------
def main():
    print("""
╔══════════════════════════════════════════════════════════╗
║          WALLET GENERATOR & SCANNER v2.0                 ║
║              STEALTH MODE - Mode A                       ║
╚══════════════════════════════════════════════════════════╝
""")
    
    cfg = load_json_file(CONFIG_FILE)
    if not cfg:
        print(f"[!] '{CONFIG_FILE}' empty or missing. Create it first.")
        return

    # Inject alchemy key into URLs
    cfg = inject_alchemy_key(cfg)

    # Prepare web3 clients
    web3_clients = build_web3_clients(cfg)
    
    if not web3_clients:
        print("[!] Warning: No RPC connections established. Balance checking will be limited.")
    else:
        print(f"[+] Connected to {len(web3_clients)} chain(s): {', '.join(web3_clients.keys())}")

    # Security notes
    if not DEBANK_ACCESS_KEY:
        print("[!] Warning: DEBANK_ACCESS_KEY not found. DeBank integration disabled.")
    else:
        print("[+] DeBank API connected")
    
    if not ALCHEMY_API_KEY:
        print("[!] Warning: ALCHEMY_API_KEY not found. Some RPC URLs may fail.")
    else:
        print("[+] Alchemy API key loaded")

    print(f"\n[+] Output file: {OUTPUT_FILE}")
    print("[+] Only wallets with balance/history will be saved\n")

    # Run menu
    menu_loop(cfg, web3_clients)

if __name__ == "__main__":
    main()
