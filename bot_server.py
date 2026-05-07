"""
SeaTalk Bot Server for Overbreak Monitoring
Handles event callbacks, Google Sheets monitoring, and sends messages to group chat
"""
import os
import json
import hashlib
import time
import threading
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from flask import Flask, request, jsonify

from config import config
from seatalk_api import SeaTalkAPI
from sheets_monitor import SheetsMonitor

# Configuration from config module
GOOGLE_SHEET_ID = config.GOOGLE_SHEET_ID
SEATALK_APP_ID = config.SEATALK_APP_ID
SEATALK_APP_SECRET = config.SEATALK_APP_SECRET
SEATALK_SIGNING_SECRET = config.SEATALK_SIGNING_SECRET
CC_USER_IDS: List[str] = config.CC_USER_IDS

# Flask app
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
    now = datetime.now()
    time_str = now.strftime("%I:%M%p %B %d")

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

    # Build cc mentions for CC_USER_IDS from config
    cc_mentions = []
    for user_id in CC_USER_IDS:
        if user_id:
            cc_mentions.append(f"<mention-tag target=\"seatalk://user?id={user_id}\"/>")
    cc_section = " ".join(cc_mentions) if cc_mentions else ""

    # Format message with bold title using markdown
    message = f"""**Inbound Overbreak Monitoring**
as of: [{time_str}]

>1 HR = [{threshold}]
Ops _id list of Overbreak
{employee_list}

cc: {cc_section}"""

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
    try:
        group = event_data.get("group", {})
        group_id = group.get("group_id")
        group_name = group.get("group_name", "Unknown")
        
        inviter = event_data.get("inviter", {})
        inviter_email = inviter.get("email", "Unknown")
        
        print(f"[Event] Bot added to group: {group_name} (ID: {group_id}) by {inviter_email}")
        
        if not sheets_monitor:
            print("[Error] Sheets monitor not initialized")
            return
        
        # Store group_id in A2
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


def handle_event_verification(event_data: Dict[str, Any]) -> Dict[str, Any]:
    """Handle event_verification - return the challenge"""
    challenge = event_data.get("seatalk_challenge", "")
    print(f"[Event] Verification challenge received: {challenge[:20]}...")
    return {"seatalk_challenge": challenge}


@app.route("/bot-callback", methods=["POST"])
def bot_callback():
    """Handle SeaTalk event callbacks"""
    try:
        # Get request data
        body = request.get_data()
        signature = request.headers.get("signature", "")
        
        # Verify signature
        if not seatalk_api.verify_signature(body, signature):
            print("[Warning] Invalid signature received")
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
            threading.Thread(
                target=handle_bot_added_to_group_chat,
                args=(event_data,),
                daemon=True
            ).start()
        
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
        return jsonify({"error": "Internal error"}), 500


@app.route("/health", methods=["GET"])
def health_check():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "monitoring": sheets_monitor is not None
    })


@app.route("/healthz", methods=["GET"])
def healthz_check():
    """Kubernetes-style health check endpoint"""
    return jsonify({
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat()
    }), 200


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


def initialize_monitor():
    """Initialize the sheets monitor"""
    global sheets_monitor
    
    try:
        sheets_monitor = SheetsMonitor(
            spreadsheet_id=GOOGLE_SHEET_ID,
            service_account_file='google-service-account.json',
            on_new_data_callback=on_new_workstation_data,
            seatalk_api=seatalk_api
        )
        
        # Start monitoring
        sheets_monitor.start_monitoring(check_interval=10)
        
        print("[Init] Sheets monitor initialized and started")
        
    except Exception as e:
        print(f"[Error] Failed to initialize sheets monitor: {e}")
        raise


if __name__ == "__main__":
    # Validate required configuration
    missing = config.validate()

    if missing:
        print(f"[Fatal] Missing required configuration: {', '.join(missing)}")
        print("[Info] Please check your .env file or environment variables")
        exit(1)
    
    # Initialize monitor
    initialize_monitor()
    
    # Start Flask server
    # PORT is provided by cloud platforms (Render, Heroku, etc.)
    # For local development, defaults to 5000
    port = int(os.getenv("PORT", "5000"))

    print(f"[Server] Starting on port {port}")

    # Use threaded=True to handle concurrent requests
    app.run(host="0.0.0.0", port=port, threaded=True)
