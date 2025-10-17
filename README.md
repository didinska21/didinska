# didinska
## project wallet gen, dibuat sejak 17 okt 2025 


# 🤖 Setup Telegram Bot untuk DIDINSKA Wallet Hunter

## 📋 Cara Membuat Bot Telegram

### Step 1: Buat Bot Baru
1. Buka Telegram dan cari **@BotFather**
2. Kirim command: `/newbot`
3. Masukkan nama bot (contoh: `DIDINSKA Wallet Hunter`)
4. Masukkan username bot (contoh: `didinska_wallet_bot`)
5. BotFather akan memberikan **BOT TOKEN** seperti ini:
   ```
   123456789:ABCdefGHIjklMNOpqrsTUVwxyz
   ```
6. **Simpan token ini!** Ini adalah `TELEGRAM_BOT_TOKEN`

### Step 2: Dapatkan Chat ID

#### Metode 1: Menggunakan Bot GetID
1. Cari bot **@userinfobot** di Telegram
2. Klik Start
3. Bot akan menampilkan **Chat ID** Anda
4. Salin angka tersebut (contoh: `123456789`)

#### Metode 2: Manual via API
1. Kirim pesan ke bot Anda yang baru dibuat
2. Buka browser dan akses:
   ```
   https://api.telegram.org/bot<BOT_TOKEN>/getUpdates
   ```
   Ganti `<BOT_TOKEN>` dengan token bot Anda
3. Cari field `"chat":{"id":123456789}`
4. Angka tersebut adalah **Chat ID** Anda

### Step 3: Konfigurasi di .env

Edit file `.env` dan tambahkan:
```bash
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz
TELEGRAM_CHAT_ID=123456789
```

## 🔔 Fitur Notifikasi Telegram

### 1. Notifikasi Wallet Found (dengan Balance)
Bot akan mengirim pesan ketika menemukan wallet dengan balance:
```
🎉 WALLET FOUND! 🎉

💰 Balance: $1234.56
📍 Address: 0x1234...5678
🔑 Private Key: abc123...
📝 Phrase: word1 word2 ... word12

💎 Coins:
  • ETH: 0.5
  • USDT: 1000

🌐 Chains: ethereum, polygon
📊 Transactions: 15
⏰ Found at: 2025-10-18T10:30:45
```

### 2. Notifikasi Empty Wallets (Batch)
Bot akan mengirim ringkasan setiap 60 detik untuk wallet kosong:
```
📭 Empty Wallets Report

🔍 Scanned: 150 wallets
❌ Empty: 150
📊 Total Checked: 1,500
⏰ Time: 2025-10-18 10:30:45
```

### 3. Notifikasi Scan Start
```
🚀 Scan Started

🎯 Target: 1,000 wallets
⚡ Workers: 16
🕐 Started: 2025-10-18 10:00:00
```

### 4. Notifikasi Scan Complete
```
✅ Scan Completed

📊 Statistics:
  • Generated: 1,000
  • Checked: 1,000
  • Found: 3
  • Empty: 997
  • Speed: 5.23 wallet/s
  • Runtime: 191.23s
```

## 🔧 Testing Koneksi

Jalankan script berikut untuk test koneksi:

```bash
python3 << 'EOF'
import os
import requests
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

if not BOT_TOKEN or not CHAT_ID:
    print("❌ TELEGRAM_BOT_TOKEN atau TELEGRAM_CHAT_ID belum diset!")
else:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = {
        "chat_id": CHAT_ID,
        "text": "🚀 Test message from DIDINSKA Wallet Hunter!",
        "parse_mode": "HTML"
    }
    
    response = requests.post(url, data=data)
    
    if response.status_code == 200:
        print("✅ Telegram bot connected successfully!")
        print("Check your Telegram for test message")
    else:
        print(f"❌ Failed: {response.status_code}")
        print(f"Response: {response.text}")
EOF
```

## 📱 Tips & Best Practices

### 1. Buat Grup Khusus (Opsional)
- Buat grup Telegram baru
- Tambahkan bot Anda ke grup
- Gunakan Chat ID grup untuk notifikasi terpusat

### 2. Mute Notifikasi untuk Batch Empty
Karena notifikasi empty wallets dikirim setiap 60 detik, Anda bisa:
- Mute chat dengan bot
- Atau disable notifikasi empty wallet di code (comment line `notify_empty_wallets_batch`)

### 3. Rate Limit Telegram
Telegram API memiliki limit:
- Max 30 messages per second
- Max 20 messages per minute ke satu user
- Script sudah dioptimasi untuk tidak exceed limit

## 🐛 Troubleshooting

### Error: "Forbidden: bot was blocked by the user"
**Solusi**: Buka chat dengan bot Anda dan klik tombol **Start** atau kirim `/start`

### Error: "Bad Request: chat not found"
**Solusi**: Chat ID salah. Pastikan Anda sudah mengirim pesan ke bot minimal 1x

### Error: "Unauthorized"
**Solusi**: Bot token salah. Periksa kembali token dari BotFather

### Notifikasi tidak terkirim tapi tidak ada error
**Solusi**: 
1. Pastikan bot sudah di-start
2. Cek apakah bot token dan chat ID benar
3. Test koneksi dengan script di atas

## 🔒 Keamanan

- **JANGAN SHARE** bot token ke siapapun
- **JANGAN COMMIT** file .env ke git
- Tambahkan `.env` ke `.gitignore`
- Gunakan bot private (jangan publish ke @BotFather discovery)

## 📊 File Output

Script akan membuat 2 file JSON:

1. **hasil.json** - Wallet dengan balance (notif ke Telegram ✅)
2. **empty_wallets.json** - Wallet kosong (batch notif setiap 60s)

Kedua file ini disimpan secara terpisah untuk kemudahan filtering.

## 💡 Customization

Jika ingin mengubah interval notifikasi, edit di `wallet_gen.py`:

```python
# Line ~685
telegram_batch_interval = 60  # Ganti angka ini (dalam detik)
```

Untuk disable notifikasi empty wallets:
```python
# Comment line ~707-709
# if TELEGRAM_ENABLED and time.time() - last_telegram_batch > telegram_batch_interval:
#     if empty_count > 0:
#         notify_empty_wallets_batch(empty_count, STATS["total_checked"])
```

---

**Happy Hunting! 🎯**

*DIDINSKA Wallet Hunter v4.0*
