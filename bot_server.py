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

# Keep grouped message bursts below SeaTalk limits and make delivery more reliable.
MESSAGE_SEND_DELAY_SECONDS = float(os.getenv("SEATALK_MESSAGE_SEND_DELAY_SECONDS", "1"))
MESSAGE_SEND_MAX_RETRIES = int(os.getenv("SEATALK_MESSAGE_SEND_MAX_RETRIES", "3"))
MESSAGE_SEND_RETRY_DELAY_SECONDS = float(os.getenv("SEATALK_MESSAGE_SEND_RETRY_DELAY_SECONDS", "3"))

# SeaTalk can throttle bursts or return temporary upstream failures. Retry only
# errors that are likely to succeed on a later attempt.
RETRYABLE_SEATALK_CODES = {
    429,
    500,
    502,
    503,
    504,
}

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

    employee_list = "\n".join(employee_lines) if employee_lines else "No overbreak logs"

    # Build cc mentions for CC_USER_IDS from config (each on new line)
    cc_lines = []
    for user_id in CC_USER_IDS:
        if user_id:
            cc_lines.append(f"<mention-tag target=\"seatalk://user?id={user_id}\"/>")
    cc_section = "\n".join(cc_lines) if cc_lines else ""

    # Format message with bold title using markdown
    message = f"""**Inbound Overbreak Monitoring**
as of: {time_str}

>1.5Hours = {threshold} HC

Ops _id list of Overbreak:
{employee_list}

cc: Ma'am/Sir's
{cc_section}"""

    return message


def format_list_message(title: str, values: List[str], empty_text: str = "No records") -> str:
    """Format a title plus one value per line for SeaTalk."""
    item_list = "\n".join(values) if values else empty_text
    cc_lines = []
    for user_id in CC_USER_IDS:
        if user_id:
            cc_lines.append(f"<mention-tag target=\"seatalk://user?id={user_id}\"/>")
    cc_section = "\n".join(cc_lines) if cc_lines else ""

    return f"""**{title}**
{item_list}

cc: Ma'am/Sir's
{cc_section}"""


def format_attendance_messages(attendance_data: Dict[str, Any]) -> List[str]:
    """Format all attendance monitoring messages for SeaTalk."""
    return [
        format_overbreak_message(attendance_data),
        format_list_message(
            "No Breaktime Scan in FMS Workstation",
            attendance_data.get("no_breaktime_scan", [])
        ),
        format_list_message(
            "Ongoing Breaktime",
            attendance_data.get("ongoing_breaktime", [])
        )
    ]


def send_overbreak_message(
    group_id: str,
    message: Optional[str] = None,
    message_label: str = "message"
) -> bool:
    """Send overbreak monitoring message to one group chat."""
    try:
        if not sheets_monitor:
            print("[Error] Sheets monitor not initialized")
            return False

        if message is None:
            attendance_data = sheets_monitor.get_attendance_timein_data()
            message = format_overbreak_message(attendance_data)
        
        max_attempts = MESSAGE_SEND_MAX_RETRIES + 1
        for attempt in range(1, max_attempts + 1):
            try:
                print(f"[Send] Sending {message_label} to group {group_id} (attempt {attempt}/{max_attempts})")
                result = seatalk_api.send_group_message(group_id, message, format_type=1)

                if result.get("code") == 0:
                    print(f"[Success] Sent {message_label} to group {group_id}; message_id={result.get('message_id')}")
                    return True

                code = result.get("code")
                print(f"[Error] Failed to send {message_label} to group {group_id}: {result}")
                should_retry = code in RETRYABLE_SEATALK_CODES
            except Exception as send_error:
                status_code = getattr(getattr(send_error, "response", None), "status_code", None)
                should_retry = status_code in RETRYABLE_SEATALK_CODES or status_code is None
                print(f"[Error] Exception sending {message_label} to group {group_id}: {send_error}")

            if not should_retry or attempt == max_attempts:
                return False

            retry_delay = MESSAGE_SEND_RETRY_DELAY_SECONDS * attempt
            print(f"[Retry] Waiting {retry_delay}s before retrying {message_label} to group {group_id}")
            time.sleep(retry_delay)

        return False
            
    except Exception as e:
        print(f"[Error] Exception sending {message_label} to group {group_id}: {e}")
        return False


def send_attendance_messages(group_id: str, messages: List[str]) -> Dict[str, Any]:
    """Send all attendance monitoring messages to one group chat."""
    sent = []
    failed = []
    total_messages = len(messages)
    for index, message in enumerate(messages, start=1):
        message_label = f"attendance message {index}/{total_messages}"
        if send_overbreak_message(group_id, message=message, message_label=message_label):
            sent.append(index)
        else:
            failed.append(index)
        if index < total_messages and MESSAGE_SEND_DELAY_SECONDS > 0:
            time.sleep(MESSAGE_SEND_DELAY_SECONDS)
    return {
        "success": not failed,
        "sent_messages": sent,
        "failed_messages": failed,
    }


def send_overbreak_message_to_all_groups() -> Dict[str, Any]:
    """Send attendance monitoring messages to every group_id in group_id!A2:A."""
    if not sheets_monitor:
        print("[Error] Sheets monitor not initialized")
        return {"success": False, "sent": [], "failed": [], "error": "Sheets monitor not initialized"}

    group_ids = sheets_monitor.get_stored_group_ids()
    if not group_ids:
        print("[Warning] No group_ids stored in 'group_id' sheet (A2:A is empty), cannot send message")
        return {"success": False, "sent": [], "failed": [], "error": "No group_ids stored in A2:A"}

    attendance_data = sheets_monitor.get_attendance_timein_data()
    messages = format_attendance_messages(attendance_data)
    print(f"[Broadcast] Preparing {len(messages)} attendance messages for {len(group_ids)} groups: {group_ids}")

    sent = []
    failed = []
    group_results = {}
    for group_index, group_id in enumerate(group_ids, start=1):
        print(f"[Broadcast] Sending to group {group_index}/{len(group_ids)}: {group_id}")
        group_result = send_attendance_messages(group_id, messages)
        group_results[group_id] = group_result
        if group_result["success"]:
            sent.append(group_id)
        else:
            failed.append(group_id)
        if group_index < len(group_ids) and MESSAGE_SEND_DELAY_SECONDS > 0:
            time.sleep(MESSAGE_SEND_DELAY_SECONDS)

    print(f"[Broadcast] Sent {len(messages)} attendance messages to {len(sent)}/{len(group_ids)} groups")
    return {
        "success": not failed,
        "sent": sent,
        "failed": failed,
        "total": len(group_ids),
        "messages_per_group": len(messages),
        "group_results": group_results,
    }


def on_new_workstation_data(data: list):
    """Callback when data in A3:H range changes in workstation_dump"""
    print(f"[Callback] Data in A3:H modified, {len(data)} cells affected")

    if not sheets_monitor:
        return

    send_overbreak_message_to_all_groups()


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

        # Store group_id in column A starting at A2
        print(f"[DEBUG] Attempting to store group_id {group_id}...")
        if sheets_monitor.store_group_id(group_id):
            print(f"[Success] Stored group_id {group_id} in group_id column A")
        else:
            print("[Error] Failed to store group_id")
            return

        # Wait 7 seconds before sending initial message
        print("[Info] Waiting 7 seconds before sending initial message...")
        time.sleep(7)

        # Send attendance monitoring messages
        attendance_data = sheets_monitor.get_attendance_timein_data()
        messages = format_attendance_messages(attendance_data)
        result = send_attendance_messages(group_id, messages)
        print(f"[Event] Initial attendance message result for {group_id}: {result}")

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
    """Manual endpoint to send test message to all configured groups."""
    try:
        if not sheets_monitor:
            return jsonify({"error": "Sheets monitor not initialized"}), 500

        result = send_overbreak_message_to_all_groups()
        if not result["sent"] and result.get("error"):
            return jsonify({"error": result["error"]}), 400

        return jsonify(result)

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/test-sheets", methods=["GET"])
def test_sheets():
    """Test Google Sheets connectivity without adding fake group IDs."""
    import traceback
    try:
        if not sheets_monitor:
            return jsonify({"error": "Sheets monitor not initialized"}), 500

        # Test reading
        print("[TEST] Reading current group_ids from A2:A...")
        current = sheets_monitor.get_stored_group_ids()

        # Test writing outside the group_id list so broadcasts do not use fake IDs.
        test_val = f"test_{datetime.now().strftime('%H%M%S')}"
        print(f"[TEST] Writing test value '{test_val}' to B2...")
        worksheet = sheets_monitor._get_group_id_worksheet()
        success = False
        if worksheet:
            worksheet.update('B2', [[test_val]])
            success = True

        if success:
            # Verify write
            verify = worksheet.acell('B2').value
            return jsonify({
                "group_ids": current,
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
