# SeaTalk Overbreak Monitoring Bot

A Flask-based bot server for SeaTalk that monitors Google Sheets for overbreak data and sends formatted messages to group chats.

## Features

- **Event Callback Handling**: Handles `bot_added_to_group_chat` events and stores each `group_id` in Google Sheets column A starting at A2
- **Google Sheets Monitoring**: Continuously monitors `workstation_dump` tab (range A3:H) for new data
- **7-Second Delay**: Waits 7 seconds before sending messages when new data is detected
- **Attendance Message Format**: Sends three formatted messages with:
  - Bold title: **Inbound Overbreak Monitoring**
  - Bold title: **No Breaktime Scan in FMS Workstation**
  - Bold title: **Ongoing Breaktime**
  - Sheet timestamp
  - Overbreak threshold (>1 HR = value from N4)
  - Employee list with overbreak hours (M7:M17 paired with O7:O17)
  - No breaktime scan list from P2:P50
  - Ongoing breaktime list from R2:R50
- **Signature Verification**: Validates SeaTalk event callbacks using SHA-256 signing secret

## Prerequisites

1. Python 3.8+
2. SeaTalk Bot App with:
   - App ID
   - App Secret  
   - Signing Secret
3. Google Cloud Service Account with Sheets API access
4. Google Sheet with the following tabs:
   - `1. workstation_dump` (data in A3:H)
   - `[do_not_edit] attendance_timein_data` (N4 = threshold, M6:M25 = employee codes, O6:O25 = hours)

## Installation

```bash
pip install -r requirements.txt
```

## Configuration

### Environment Variables

1. Copy the example environment file:
```bash
cp .env.example .env
```

2. Edit `.env` with your actual values:

```env
GOOGLE_SHEET_ID=your_google_sheet_id_here
SEATALK_APP_ID=your_seatalk_app_id_here
SEATALK_APP_SECRET=your_seatalk_app_secret_here
SEATALK_SIGNING_SECRET=your_signing_secret_here
CC_USER_IDS=user_id_1,user_id_2,user_id_3
```

### Google Service Account

Place your `google-service-account.json` file in the project root. **This file is automatically ignored by git.**

### Security Notes

- `.env` and `google-service-account.json` are in `.gitignore` - never commit these files
- Keep your SeaTalk secrets and Google credentials secure
- Use different credentials for development and production

## Usage

### Start the Server

```bash
python bot_server.py
```

The server will automatically use the PORT provided by the cloud platform (Render, etc.) or default to 5000 for local development.

### Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Simple OK response (best for uptime monitoring) |
| `/bot-callback` | POST | SeaTalk event callback handler |
| `/health` | GET | Health check endpoint |
| `/healthz` | GET | Kubernetes-style health check |
| `/send-test-message` | POST | Manually trigger message send |
| `/test-sheets` | GET | Test Google Sheets connectivity |

### Uptime Monitoring (UptimeRobot)

For UptimeRobot or similar services, use these endpoints:
- **URL**: `https://your-service.onrender.com/` or `/healthz`
- **Method**: GET
- **Expected Response**: `OK` or `ok`
- **Port**: 443 (HTTPS)

The server will start even if Google Sheets initialization fails, so health checks will always work.

### SeaTalk Callback URL

Configure your callback URL in the SeaTalk Developer Portal:
```
https://your-server.com/bot-callback
```

## How It Works

1. **Bot Added to Group**: When the bot is added to a group chat:
   - Receives `bot_added_to_group_chat` event
   - Stores `group_id` in column A of **"group_id"** sheet, starting at A2 (creates sheet if needed)
   - Waits 7 seconds
   - Sends initial overbreak monitoring message

2. **Data Change Detection**: When new data is pasted in A3:H of workstation_dump:
   - Monitor detects the change (10-second polling interval)
   - Waits 7 seconds
   - Sends the three updated attendance messages to every group_id in `group_id!A2:A`

## Message Format Example

```
**Inbound Overbreak Monitoring**
as of: [9:42AM May 3]

>1 HR = [1.5]
Ops _id list of Overbreak
e_12345 - 1.5
e_67890 - 2.0

**No Breaktime Scan in FMS Workstation**
e_11111
e_22222

**Ongoing Breaktime**
e_33333
e_44444
```

## Project Structure

```
.
├── bot_server.py           # Main Flask application
├── seatalk_api.py         # SeaTalk API wrapper
├── sheets_monitor.py      # Google Sheets monitoring
├── config.py              # Configuration management
├── requirements.txt       # Python dependencies
├── .env                   # Environment variables (not in git)
├── .env.example          # Environment template
├── .gitignore            # Git ignore rules
├── google-service-account.json  # Google credentials (not in git)
├── test_bot.py           # Test script
├── README.md             # Documentation
└── seatalk_docs/          # SeaTalk API documentation
```

## Google Sheets Tabs

The bot expects these tabs in your Google Sheet:

| Tab | Purpose | Data Location |
|-----|---------|---------------|
| `1. workstation_dump` | Monitor for data changes | A3:H3 (first row trigger) |
| `[do_not_edit] attendance_timein_data` | Attendance message data source | N2 (timestamp), N4 (threshold), M7:O17 (overbreak), P2:P50 (no breaktime scan), R2:R50 (ongoing breaktime) |
| `group_id` | Stores SeaTalk group IDs | A2:A (auto-created if missing) |
