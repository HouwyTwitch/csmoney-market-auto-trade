# CS.Money Market Auto-Sale Tool

Automatically processes sale offers on the CS.Money market. When a buyer purchases one of your listings, the tool creates and confirms the Steam trade offer on your behalf — no manual action required.

## How it works

The tool replicates the logic of the official CS.Money Chrome extension:

1. **Notifications** — polls `GET /1.0/market/notifications` every `poll_interval` seconds. On an `OFFER_BOUGHT` event, immediately checks for pending trades.
2. **Active-offers check** — polls `GET /3.0/market/active-offers` every 30 seconds as a fallback.
3. For each offer in `CREATING` state (no Steam trade yet):
   - Notifies CS.Money via `POST /3.0/market/offers/tradeoffer`.
   - Sends the Steam trade offer directly to the buyer (`POST steamcommunity.com/tradeoffer/new/send`).
   - Fetches an RSA-6144 public key (`POST /1.0/market/secure/key`) and encrypts the Steam session credentials.
   - Reports the trade offer ID and encrypted session back to CS.Money (`PATCH /4.0/market/offers/tradeoffer`).
   - Confirms the trade offer in Steam via mobile authenticator.
4. **historyOutdate re-sync** — when CS.Money signals `historyOutdate=true`, sends the encrypted session via `POST /4.0/market/offers/session` so CS.Money can re-sync trade state.

Sessions and cookies are persisted to disk so restarts do not require a new login.

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure

```bash
cp config.example.json config.json
```

Edit `config.json` with your Steam account details:

```json
{
    "steam_id": 76561198000000000,
    "steam_username": "your_username",
    "steam_password": "your_password",
    "steam_shared_secret": "base64_shared_secret",
    "steam_identity_secret": "base64_identity_secret",

    "csmoney_proxy": "",
    "steam_proxy": "",

    "poll_interval": 10,
    "log_level": "INFO"
}
```

| Field | Required | Description |
|---|---|---|
| `steam_id` | Yes | Your Steam64 ID |
| `steam_username` | Yes | Steam account username |
| `steam_password` | Yes | Steam account password |
| `steam_shared_secret` | Yes | TOTP shared secret (from Steam authenticator) |
| `steam_identity_secret` | Yes | Identity secret for trade confirmations |
| `csmoney_proxy` | No | Proxy for CS.Money requests (`http://user:pass@host:port`) |
| `steam_proxy` | No | Proxy for Steam requests (falls back to `csmoney_proxy`) |
| `poll_interval` | No | Seconds between notification polls (default: `10`) |
| `log_level` | No | `DEBUG`, `INFO`, `WARNING`, or `ERROR` (default: `INFO`) |

### 3. Run

```bash
python main.py
```

On first run the tool logs in to Steam and CS.Money automatically. Sessions are saved to `steam_session.json` and `csmoney_cookies.json` so subsequent starts are instant.

## Notes

- **Keep `config.json` private** — it contains your Steam credentials.
- The tool only confirms trade offers that it created itself; it will never touch unrelated pending confirmations.
- Rate-limit responses from CS.Money (error code `9999`) are handled gracefully — the current cycle is skipped and the offer retried on the next poll.
- Set `log_level` to `DEBUG` for full diagnostic output including API response bodies.
