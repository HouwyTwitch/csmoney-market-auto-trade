# CS.Money Market Auto-Sale Tool

Automatically processes sale offers on CS.Money market by submitting your encrypted Steam session to CS.Money's servers when a buyer purchases one of your listings.

## How it works

The tool mirrors the logic of the official CS.Money Market Chrome extension:

1. **Polls** `GET /1.0/market/notifications` every 10 seconds for `OFFER_BOUGHT` events.
2. **Marks** notifications as viewed via `POST /1.0/market/notifications/mark-viewed`.
3. **Checks** `GET /3.0/market/active-offers` for pending trades.
4. **Sends** your encrypted Steam session when CS.Money requests it (`historyOutdate=true` or offers in `CREATING` state):
   - Fetches an RSA-6144 public key from `POST /1.0/market/secure/key`.
   - Encrypts `steamLoginSecure` → `sessionData` (RSA-OAEP / SHA-256).
   - Encrypts `sessionid` → `sessionId` (RSA-OAEP / SHA-256).
   - POSTs to `POST /4.0/market/offers/session`.
5. CS.Money's server uses the decrypted session to send the Steam trade offer to the buyer on your behalf.

The active-offers check also runs independently every 6 minutes as a fallback.

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Get your cookies

You need three cookies. Open your browser, go to `https://cs.money/market/sell/`, open DevTools → Application → Cookies.

| Variable | Cookie name | Domain |
|---|---|---|
| `CSMONEY_SESSION` | `csgo_ses` | cs.money |
| `STEAM_LOGIN_SECURE` | `steamLoginSecure` | steamcommunity.com |
| `STEAM_SESSION_ID` | `sessionid` | steamcommunity.com |

### 3. Configure

```bash
cp .env.example .env
# Edit .env with your values
```

### 4. Run

```bash
python main.py
```

## Configuration

| Variable | Default | Description |
|---|---|---|
| `CSMONEY_SESSION` | — | `csgo_ses` cookie value from cs.money |
| `STEAM_LOGIN_SECURE` | — | `steamLoginSecure` cookie from steamcommunity.com |
| `STEAM_SESSION_ID` | — | `sessionid` cookie from steamcommunity.com |
| `POLL_INTERVAL` | `10` | Seconds between notification polls |
| `LOG_LEVEL` | `INFO` | Logging verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |

## Notes

- The tool does **not** send trade offers directly — CS.Money's server does, using your encrypted session.
- The Steam `steamLoginSecure` cookie expires periodically. Restart the tool with a fresh cookie when it does.
- Keep your `.env` file private — it contains sensitive session data.