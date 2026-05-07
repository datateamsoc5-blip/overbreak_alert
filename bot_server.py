"""
SeaTalk Bot Server for Overbreak Monitoring
Handles event callbacks, Google Sheets monitoring, and sends messages to group chat
"""
import os
import json
import hashlib
import time
import threading
import traceback
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from flask import Flask, request, jsonify

import sys
print("[STARTUP] Bot server starting...", flush=True)

# Load configuration with error handling
try:
    from config import config
    print("[STARTUP] Config loaded successfully", flush=True)
except Exception as e:
    print(f"[FATAL] Failed to load config: {e}", flush=True)
    traceback.print_exc()
    sys.exit(1)

# Configuration from config module
try:
    GOOGLE_SHEET_ID = config.GOOGLE_SHEET_ID
    SEATALK_APP_ID = config.SEATALK_APP_ID
    SEATALK_APP_SECRET = config.SEATALK_APP_SECRET
    SEATALK_SIGNING_SECRET = config.SEATALK_SIGNING_SECRET
    CC_USER_IDS: List[str] = config.CC_USER_IDS
    print(f"[STARTUP] Config vars loaded. Sheet ID: {GOOGLE_SHEET_ID[:10] if GOOGLE_SHEET_ID else 'NONE'}...", flush=True)
except Exception as e:
    print(f"[FATAL] Failed to read config values: {e}", flush=True)
    traceback.print_exc()
    sys.exit(1)

# Import other modules
try:
    from seatalk_api import SeaTalkAPI
    from sheets_monitor import SheetsMonitor
    print("[STARTUP] Modules imported successfully", flush=True)
except Exception as e:
    print(f"[FATAL] Failed to import modules: {e}", flush=True)
    traceback.print_exc()
    sys.exit(1)

# Flask app
print("[STARTUP] Creating Flask app...", flush=True)
app = Flask(__name__)

# Initialize SeaTalk API
seatalk_api = SeaTalkAPI(
    app_id=SEATALK_APP_ID,
    app_secret=SEATALK_APP_SECRET,
    signing_secret=SEATALK_SIGNING_SECRET
)

# Initialize Sheets Monitor
sheets_monitor: Optional[SheetsMonitor] = None

# Event types
EVENT_VERIFICATION = "event_verification"
BOT_ADDED_TO_GROUP_CHAT = "bot_added_to_group_chat"
BOT_REMOVED_FROM_GROUP_CHAT = "bot_removed_from_group_chat"
NEW_MENTIONED_MESSAGE_RECEIVED = "new_mentioned_message_received_from_group_chat"


def format_overbreak_message(attendance_data: Dict[str, Any]) -> str:
    """Format the overbreak monitoring message for SeaTalk"""
    # Get current time
    # Get timestamp from N2 (as shown in sheet)
    time_str = attendance_data.get("as_of_timestamp", "N/A")

    # Get threshold from N4
    threshold = attendance_data.get("overbreak_threshold", "N/A")

    # Build employee list
    employee_codes = attendance_data.get("employee_codes", [])
    overbreak_hours = attendance_data.get("overbreak_hours", [])

    employee_lines = []
    for i, (code, hours) in enumerate(zip(employee_codes, overbreak_hours)):
        if code:  # Only include if employee code exists
            employee_lines.append(f"{code} - {hours}")

    employee_list = "\n".join(employee_lines) if employee_lines else "No overbreak records found"

    # Build cc mentions for CC_USER_IDS from config (each on new line)
    cc_lines = []
    for user_id in CC_USER_IDS:
        if user_id:
            cc_lines.append(f"<mention-tag target=\"seatalk://user?id={user_id}\"/>")
    cc_section = "\n".join(cc_lines) if cc_lines else ""

    # Format message with bold title using markdown
    message = f"""**Inbound Overbreak Monitoring**
as of: {time_str}

>1Hour = {threshold} HC

Ops _id list of Overbreak:
{employee_list}

cc: Ma'am/Sir's
{cc_section}"""

    return message


def send_overbreak_message(group_id: str) -> bool:
    """Send overbreak monitoring message to group chat"""
    try:
        if not sheets_monitor:
            print("[Error] Sheets monitor not initialized")
            return False
        
        # Get attendance data
        attendance_data = sheets_monitor.get_attendance_timein_data()
        
        # Format message
        message = format_overbreak_message(attendance_data)
        
        # Send to group
        result = seatalk_api.send_group_message(group_id, message, format_type=1)
        
        if result.get("code") == 0:
            print(f"[Success] Message sent to group {group_id}")
            return True
        else:
            print(f"[Error] Failed to send message: {result}")
            return False
            
    except Exception as e:
        print(f"[Error] Exception sending message: {e}")
        return False


def on_new_workstation_data(first_row: list):
    """Callback when first row (A3:H3) changes in workstation_dump"""
    print(f"[Callback] First row modified: {first_row}")

    # Get stored group_id
    if not sheets_monitor:
        return

    group_id = sheets_monitor.get_stored_group_id()
    if not group_id:
        print("[Warning] No group_id stored (A2 is empty), cannot send message")
        return

    # Send the overbreak message
    send_overbreak_message(group_id)


def handle_bot_added_to_group_chat(event_data: Dict[str, Any]):
    """Handle bot_added_to_group_chat event"""
    import traceback
    try:
        print(f"[DEBUG] handle_bot_added_to_group_chat called with: {json.dumps(event_data, indent=2)[:500]}")

        group = event_data.get("group", {})
        group_id = group.get("group_id")
        group_name = group.get("group_name", "Unknown")

        inviter = event_data.get("inviter", {})
        inviter_email = inviter.get("email", "Unknown")

        print(f"[Event] Bot added to group: {group_name} (ID: {group_id}) by {inviter_email}")

        if not group_id:
            print("[Error] No group_id in event data!")
            return

        if not sheets_monitor:
            print("[Error] Sheets monitor not initialized")
            return

        # Store group_id in A2
        print(f"[DEBUG] Attempting to store group_id {group_id}...")
        if sheets_monitor.store_group_id(group_id):
            print(f"[Success] Stored group_id {group_id} in cell A2")
        else:
            print("[Error] Failed to store group_id")
            return

        # Wait 7 seconds before sending initial message
        print("[Info] Waiting 7 seconds before sending initial message...")
        time.sleep(7)

        # Send welcome/overbreak message
        send_overbreak_message(group_id)

    except Exception as e:
        print(f"[Error] Handling bot_added_to_group_chat: {e}")
        traceback.print_exc()


def handle_event_verification(event_data: Dict[str, Any]) -> Dict[str, Any]:
    """Handle event_verification - return the challenge"""
    challenge = event_data.get("seatalk_challenge", "")
    print(f"[Event] Verification challenge received: {challenge[:20]}...")
    return {"seatalk_challenge": challenge}


@app.route("/bot-callback", methods=["POST"])
def bot_callback():
    """Handle SeaTalk event callbacks"""
    import traceback
    try:
        # Get request data
        body = request.get_data()
        signature = request.headers.get("signature", "")

        print(f"[DEBUG] Received callback. Headers: {dict(request.headers)}")
        print(f"[DEBUG] Body: {body.decode('utf-8', errors='replace')[:500]}")

        # Verify signature
        if not seatalk_api.verify_signature(body, signature):
            print(f"[Warning] Invalid signature received. Sig: {signature}")
            return jsonify({"error": "Invalid signature"}), 403

        # Parse event
        data = json.loads(body)
        event_type = data.get("event_type", "")
        event_data = data.get("event", {})

        print(f"[Event] Received: {event_type}")

        # Handle verification
        if event_type == EVENT_VERIFICATION:
            response = handle_event_verification(event_data)
            return jsonify(response)

        # Handle bot added to group chat
        elif event_type == BOT_ADDED_TO_GROUP_CHAT:
            print("[DEBUG] Starting thread for bot_added_to_group_chat")
            def run_handler():
                try:
                    handle_bot_added_to_group_chat(event_data)
                except Exception as thread_e:
                    print(f"[Error] Thread exception: {thread_e}")
                    traceback.print_exc()

            threading.Thread(target=run_handler, daemon=True).start()
            print("[DEBUG] Thread started")

        # Handle other events (log only)
        elif event_type == BOT_REMOVED_FROM_GROUP_CHAT:
            print("[Event] Bot removed from group chat")
        elif event_type == NEW_MENTIONED_MESSAGE_RECEIVED:
            print("[Event] New mentioned message received")
        else:
            print(f"[Event] Unknown event type: {event_type}")

        # Return empty 200 for all non-verification events
        return "", 200

    except Exception as e:
        print(f"[Error] Exception in callback handler: {e}")
        traceback.print_exc()
        return jsonify({"error": "Internal error"}), 500


@app.route("/", methods=["GET"])
def root():
    """Simple root endpoint for uptime monitoring"""
    return "OK", 200


@app.route("/health", methods=["GET"])
def health_check():
    """Health check endpoint - lightweight, always returns 200 if server is up"""
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "sheets_monitor_initialized": sheets_monitor is not None
    }), 200


@app.route("/healthz", methods=["GET"])
def healthz_check():
    """Kubernetes-style health check endpoint - ultra lightweight"""
    return "ok", 200


@app.route("/send-test-message", methods=["POST"])
def send_test_message():
    """Manual endpoint to send test message (for debugging)"""
    try:
        if not sheets_monitor:
            return jsonify({"error": "Sheets monitor not initialized"}), 500

        group_id = sheets_monitor.get_stored_group_id()
        if not group_id:
            return jsonify({"error": "No group_id stored in A2"}), 400

        success = send_overbreak_message(group_id)
        return jsonify({"success": success, "group_id": group_id})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/test-sheets", methods=["GET"])
def test_sheets():
    """Test Google Sheets connectivity and write test value to 'group_id' sheet A2"""
    import traceback
    try:
        if not sheets_monitor:
            return jsonify({"error": "Sheets monitor not initialized"}), 500

        # Test reading
        print("[TEST] Reading current group_id from A2...")
        current = sheets_monitor.get_stored_group_id()

        # Test writing
        test_val = f"test_{datetime.now().strftime('%H%M%S')}"
        print(f"[TEST] Writing test value '{test_val}' to A2...")
        success = sheets_monitor.store_group_id(test_val)

        if success:
            # Verify write
            verify = sheets_monitor.get_stored_group_id()
            return jsonify({
                "previous_value": current,
                "test_value_written": test_val,
                "verified_value": verify,
                "write_success": verify == test_val
            })
        else:
            return jsonify({"error": "Failed to write to sheet"}), 500

    except Exception as e:
        print(f"[TEST ERROR] {e}")
        traceback.print_exc()
        return jsonify({"error": str(e), "traceback": traceback.format_exc()}), 500


def initialize_monitor():
    """Initialize the sheets monitor"""
    import traceback
    global sheets_monitor

    try:
        print(f"[Init] Checking for google-service-account.json...")
        import os
        if not os.path.exists('google-service-account.json'):
            print("[ERROR] google-service-account.json NOT FOUND!")
            print("[ERROR] Please ensure Google credentials are set up correctly on Render.")
            raise FileNotFoundError("google-service-account.json not found")

        print("[Init] Creating SheetsMonitor...")
        sheets_monitor = SheetsMonitor(
            spreadsheet_id=GOOGLE_SHEET_ID,
            service_account_file='google-service-account.json',
            on_new_data_callback=on_new_workstation_data,
            seatalk_api=seatalk_api
        )

        # Test connectivity
        print("[Init] Testing Google Sheets connectivity...")
        test_data = sheets_monitor.get_workstation_dump_data()
        if test_data.get("error"):
            print(f"[ERROR] Sheets test failed: {test_data['error']}")
        else:
            print(f"[Init] Sheets connectivity OK, got {len(test_data.get('data', []))} rows")

        # Start monitoring
        sheets_monitor.start_monitoring(check_interval=10)

        print("[Init] Sheets monitor initialized and started")

    except Exception as e:
        print(f"[Error] Failed to initialize sheets monitor: {e}")
        traceback.print_exc()
        # Don't raise - let server start so health checks work
        sheets_monitor = None


if __name__ == "__main__":
    print("[STARTUP] Entering main block...", flush=True)

    # Validate required configuration
    missing = config.validate()

    if missing:
        print(f"[Fatal] Missing required configuration: {', '.join(missing)}", flush=True)
        print("[Info] Please check your .env file or environment variables", flush=True)
        sys.exit(1)

    print("[STARTUP] Configuration validated", flush=True)

    # Initialize monitor (non-blocking - server starts even if this fails)
    try:
        initialize_monitor()
    except Exception as e:
        print(f"[Warning] Sheets monitor failed to initialize, but server will start: {e}", flush=True)

    # Print registered routes for debugging
    print("[STARTUP] Registered routes:", flush=True)
    for rule in app.url_map.iter_rules():
        methods = ','.join(rule.methods - {'OPTIONS', 'HEAD'})
        print(f"  {rule.endpoint}: {rule.rule} [{methods}]", flush=True)

    # Start Flask server
    port = int(os.getenv("PORT", "5000"))
    print(f"[STARTUP] Starting server on port {port}...", flush=True)

    # Use threaded=True to handle concurrent requests
    app.run(host="0.0.0.0", port=port, threaded=True)
