"""
Test script for SeaTalk Bot functionality
"""
import os
import time
from dotenv import load_dotenv
from seatalk_api import SeaTalkAPI
from sheets_monitor import SheetsMonitor

load_dotenv()

def test_seatalk_api():
    """Test SeaTalk API connectivity"""
    print("=" * 50)
    print("Testing SeaTalk API")
    print("=" * 50)
    
    api = SeaTalkAPI(
        app_id=os.getenv("SEATALK_APP_ID"),
        app_secret=os.getenv("SEATALK_APP_SECRET"),
        signing_secret=os.getenv("SEATALK_SIGNING_SECRET")
    )
    
    try:
        token = api._get_app_access_token()
        print(f"✓ Access token obtained: {token[:20]}...")
        return True
    except Exception as e:
        print(f"✗ Failed to get access token: {e}")
        return False


def test_sheets_monitor():
    """Test Google Sheets monitoring"""
    print("\n" + "=" * 50)
    print("Testing Google Sheets Monitor")
    print("=" * 50)
    
    monitor = SheetsMonitor(
        spreadsheet_id=os.getenv("GOOGLE_SHEET_ID"),
        service_account_file='google-service-account.json'
    )
    
    try:
        # Test workstation dump
        result = monitor.get_workstation_dump_data()
        data = result.get("data", [])
        print(f"✓ Workstation dump data: {len(data)} rows in A3:H")
        
        # Test attendance data
        attendance = monitor.get_attendance_timein_data()
        print(f"✓ Overbreak threshold (N4): {attendance.get('overbreak_threshold')}")
        print(f"✓ Employee codes count: {len(attendance.get('employee_codes', []))}")
        print(f"✓ No breaktime scan count: {len(attendance.get('no_breaktime_scan', []))}")
        print(f"✓ Ongoing breaktime count: {len(attendance.get('ongoing_breaktime', []))}")
        
        # Test stored group_ids
        group_ids = monitor.get_stored_group_ids()
        print(f"✓ Stored group_ids (A2:A): {group_ids}")
        
        return True
    except Exception as e:
        print(f"✗ Error: {e}")
        return False


def test_send_message():
    """Test sending a message to group chat"""
    print("\n" + "=" * 50)
    print("Testing Send Message to Group Chat")
    print("=" * 50)
    
    api = SeaTalkAPI(
        app_id=os.getenv("SEATALK_APP_ID"),
        app_secret=os.getenv("SEATALK_APP_SECRET"),
        signing_secret=os.getenv("SEATALK_SIGNING_SECRET")
    )
    
    monitor = SheetsMonitor(
        spreadsheet_id=os.getenv("GOOGLE_SHEET_ID"),
        service_account_file='google-service-account.json'
    )
    
    group_ids = monitor.get_stored_group_ids()
    if not group_ids:
        print("✗ No group_id stored in A2:A. Add bot to a group first.")
        return False
    
    # Get attendance data and format message
    attendance_data = monitor.get_attendance_timein_data()
    
    # Format message
    from datetime import datetime
    now = datetime.now()
    time_str = now.strftime("%I:%M%p %B %d")
    threshold = attendance_data.get("overbreak_threshold", "N/A")
    employee_codes = attendance_data.get("employee_codes", [])
    overbreak_hours = attendance_data.get("overbreak_hours", [])

    employee_lines = []
    for code, hours in zip(employee_codes, overbreak_hours):
        if code:
            employee_lines.append(f"{code} - {hours}")

    employee_list = "\n".join(employee_lines) if employee_lines else "No overbreak records found"

    # Build cc mentions
    cc_user_ids = os.getenv("CC_USER_IDS", "").split(",")
    cc_mentions = []
    for user_id in cc_user_ids:
        if user_id.strip():
            cc_mentions.append(f"<mention-tag target=\"seatalk://user?id={user_id.strip()}\"/>")
    cc_section = " ".join(cc_mentions) if cc_mentions else ""

    overbreak_message = f"""**Inbound Overbreak Monitoring**
as of: [{time_str}]

>1 HR = [{threshold}]
Ops _id list of Overbreak
{employee_list}

cc: {cc_section}"""

    no_breaktime_scan = attendance_data.get("no_breaktime_scan", [])
    ongoing_breaktime = attendance_data.get("ongoing_breaktime", [])
    messages = [
        overbreak_message,
        f"""**No Breaktime Scan in FMS Workstation**
{chr(10).join(no_breaktime_scan) if no_breaktime_scan else "No records"}

cc: Ma'am/Sir's
{chr(10).join(cc_mentions)}""",
        f"""**Ongoing Breaktime**
{chr(10).join(ongoing_breaktime) if ongoing_breaktime else "No records"}

cc: Ma'am/Sir's
{chr(10).join(cc_mentions)}"""
    ]
    
    print(f"Messages to send:\n\n" + "\n\n".join(messages) + "\n")
    
    try:
        sent = []
        failed = []
        for group_id in group_ids:
            group_success = True
            for message in messages:
                result = api.send_group_message(group_id, message, format_type=1)
                if result.get("code") == 0:
                    print(f"✓ Message sent successfully to {group_id}! Message ID: {result.get('message_id')}")
                else:
                    print(f"✗ Failed to send to {group_id}: {result}")
                    group_success = False

            if group_success:
                sent.append(group_id)
            else:
                failed.append(group_id)

        return not failed and bool(sent)
    except Exception as e:
        print(f"✗ Exception: {e}")
        return False


def test_store_group_id():
    """Test storing group_id in sheet"""
    print("\n" + "=" * 50)
    print("Testing Store Group ID")
    print("=" * 50)
    
    monitor = SheetsMonitor(
        spreadsheet_id=os.getenv("GOOGLE_SHEET_ID"),
        service_account_file='google-service-account.json'
    )
    
    test_group_id = "test_group_12345"
    
    try:
        success = monitor.store_group_id(test_group_id)
        if success:
            stored = monitor.get_stored_group_ids()
            if test_group_id in stored:
                print(f"✓ Successfully stored and retrieved group_id: {test_group_id}")
                return True
        print("✗ Failed to store group_id")
        return False
    except Exception as e:
        print(f"✗ Error: {e}")
        return False


if __name__ == "__main__":
    print("SeaTalk Bot Test Suite\n")
    
    # Run tests
    tests = [
        ("SeaTalk API", test_seatalk_api),
        ("Google Sheets Monitor", test_sheets_monitor),
        ("Store Group ID", test_store_group_id),
    ]
    
    results = []
    for name, test_func in tests:
        try:
            result = test_func()
            results.append((name, result))
        except Exception as e:
            print(f"✗ {name} crashed: {e}")
            results.append((name, False))
    
    # Summary
    print("\n" + "=" * 50)
    print("Test Summary")
    print("=" * 50)
    
    for name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"{status}: {name}")
    
    passed = sum(1 for _, r in results if r)
    total = len(results)
    print(f"\nTotal: {passed}/{total} tests passed")
