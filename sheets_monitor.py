"""
Google Sheets Monitor Module
Monitors workstation_dump sheet for new data and triggers SeaTalk messages
"""
import os
import time
import threading
from typing import Optional, List, Dict, Any, Callable
from datetime import datetime, timezone
import gspread
from google.oauth2.service_account import Credentials
from google.auth.transport.requests import Request

SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive.readonly'
]

class SheetsMonitor:
    def __init__(self, 
                 spreadsheet_id: str, 
                 service_account_file: str = 'google-service-account.json',
                 on_new_data_callback: Optional[Callable] = None,
                 seatalk_api = None):
        self.spreadsheet_id = spreadsheet_id
        self.service_account_file = service_account_file
        self.on_new_data_callback = on_new_data_callback
        self.seatalk_api = seatalk_api
        self._client: Optional[gspread.Client] = None
        self._last_data_hash: Optional[str] = None
        self._monitoring = False
        self._monitor_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        
    def _get_client(self) -> gspread.Client:
        """Get or create authenticated Google Sheets client"""
        if self._client is None:
            creds = Credentials.from_service_account_file(
                self.service_account_file,
                scopes=SCOPES
            )
            self._client = gspread.authorize(creds)
        return self._client
    
    def get_workstation_dump_data(self) -> Dict[str, Any]:
        """Get data from workstation_dump tab (A3:H - all rows)"""
        try:
            client = self._get_client()
            spreadsheet = client.open_by_key(self.spreadsheet_id)

            # Get the workstation_dump worksheet
            try:
                worksheet = spreadsheet.worksheet("1. workstation_dump")
            except gspread.WorksheetNotFound:
                # Try without the number prefix
                worksheet = spreadsheet.worksheet("workstation_dump")

            # Get data from A3:H (all data rows)
            data = worksheet.get('A3:H')

            return {
                "data": data,
                "worksheet": worksheet
            }
        except Exception as e:
            print(f"Error getting workstation dump data: {e}")
            return {"data": [], "worksheet": None, "error": str(e)}

    def get_workstation_data(self) -> Dict[str, Any]:
        """Get data from A3:H range for change detection"""
        try:
            client = self._get_client()
            spreadsheet = client.open_by_key(self.spreadsheet_id)

            try:
                worksheet = spreadsheet.worksheet("1. workstation_dump")
            except gspread.WorksheetNotFound:
                worksheet = spreadsheet.worksheet("workstation_dump")

            # Get full range A3:H (all data rows)
            data_range = worksheet.get('A3:H')

            # Flatten all data for comparison
            all_data = []
            for row in data_range:
                all_data.extend(row)

            return {
                "data": data_range,
                "flat_data": all_data,
                "worksheet": worksheet,
                "is_empty": not data_range or all(cell == "" for cell in all_data)
            }
        except Exception as e:
            print(f"Error getting workstation data: {e}")
            return {"data": [], "flat_data": [], "worksheet": None, "is_empty": True, "error": str(e)}
    
    def get_attendance_timein_data(self) -> Dict[str, Any]:
        """Get data from attendance_timein_data tab"""
        try:
            client = self._get_client()
            spreadsheet = client.open_by_key(self.spreadsheet_id)

            try:
                worksheet = spreadsheet.worksheet("[do_not_edit] attendance_timein_data")
            except gspread.WorksheetNotFound:
                worksheet = spreadsheet.worksheet("attendance_timein_data")

            # Get N2 (timestamp for "as of"), N4 (overbreak threshold),
            # M7:M17/O7:O17 (overbreak list), P2:P50 (no breaktime scan),
            # and R2:R50 (ongoing breaktime).
            n2_timestamp = worksheet.acell('N2').value
            n4_value = worksheet.acell('N4').value
            m_values = worksheet.get('M7:M17')
            o_values = worksheet.get('O7:O17')
            p_values = worksheet.get('P2:P50')
            r_values = worksheet.get('R2:R50')

            return {
                "as_of_timestamp": n2_timestamp,
                "overbreak_threshold": n4_value,
                "employee_codes": [row[0] for row in m_values if row],
                "overbreak_hours": [row[0] for row in o_values if row],
                "no_breaktime_scan": [str(row[0]).strip() for row in p_values if row and str(row[0]).strip()],
                "ongoing_breaktime": [str(row[0]).strip() for row in r_values if row and str(row[0]).strip()]
            }
        except Exception as e:
            print(f"Error getting attendance data: {e}")
            return {
                "as_of_timestamp": None,
                "overbreak_threshold": None,
                "employee_codes": [],
                "overbreak_hours": [],
                "no_breaktime_scan": [],
                "ongoing_breaktime": [],
                "error": str(e)
            }
    
    def _get_group_id_worksheet(self):
        """Get or create the group_id worksheet"""
        try:
            client = self._get_client()
            spreadsheet = client.open_by_key(self.spreadsheet_id)
            
            try:
                worksheet = spreadsheet.worksheet("group_id")
            except gspread.WorksheetNotFound:
                # Create the worksheet if it doesn't exist
                worksheet = spreadsheet.add_worksheet(title="group_id", rows=10, cols=2)
                print("[DEBUG] Created new 'group_id' worksheet")
            
            return worksheet
        except Exception as e:
            print(f"[Error] Failed to get group_id worksheet: {e}")
            return None
    
    def get_stored_group_ids(self) -> List[str]:
        """Get all stored group_ids from column A starting at A2."""
        try:
            worksheet = self._get_group_id_worksheet()
            if worksheet:
                values = worksheet.get('A2:A')
                group_ids = []
                seen = set()
                for row in values:
                    if not row:
                        continue

                    group_id = str(row[0]).strip()
                    if group_id and group_id not in seen:
                        group_ids.append(group_id)
                        seen.add(group_id)

                return group_ids
        except Exception as e:
            print(f"Error getting stored group IDs: {e}")
        return []

    def get_stored_group_id(self) -> Optional[str]:
        """Get the first stored group_id from group_id sheet for compatibility."""
        group_ids = self.get_stored_group_ids()
        if group_ids:
            return group_ids[0]
        return None
    
    def store_group_id(self, group_id: str) -> bool:
        """Store group_id in the next empty cell in column A, starting at A2."""
        import traceback
        try:
            group_id = str(group_id).strip()
            if not group_id:
                print("[DEBUG store_group_id] Empty group_id, skipping store")
                return False

            print(f"[DEBUG store_group_id] Storing group_id: {group_id}")
            worksheet = self._get_group_id_worksheet()
            print(f"[DEBUG store_group_id] Got worksheet: {worksheet}")
            if worksheet:
                existing_group_ids = self.get_stored_group_ids()
                if group_id in existing_group_ids:
                    print(f"[DEBUG store_group_id] group_id already stored: {group_id}")
                    return True

                next_row = len(worksheet.get('A2:A')) + 2
                print(f"[DEBUG store_group_id] Updating cell A{next_row} with {group_id}")
                worksheet.update(f'A{next_row}', [[group_id]])
                print(f"[DEBUG store_group_id] Update successful")
                return True
            else:
                print(f"[DEBUG store_group_id] No worksheet found!")
        except Exception as e:
            print(f"[Error store_group_id] {e}")
            traceback.print_exc()
        return False
    
    def _compute_data_hash(self, data: List) -> str:
        """Compute a simple hash of the data to detect changes"""
        import json
        return json.dumps(data, sort_keys=True)

    def _has_data_changed(self, current_data: List) -> tuple[bool, bool, bool]:
        """Check if data in A3:H has changed or was deleted/replaced.
        Returns: (has_changed, was_deleted_and_replaced, has_data)"""
        current_hash = self._compute_data_hash(current_data)
        is_empty = not current_data or all(cell == "" for cell in current_data)
        has_data = not is_empty

        with self._lock:
            if self._last_data_hash is None:
                # First run
                self._last_data_hash = current_hash
                return False, False, has_data

            # Check if data was deleted (became empty) and now has data
            last_was_empty = self._last_data_hash == self._compute_data_hash([])

            if last_was_empty and has_data:
                # Data was deleted and replaced with new data
                self._last_data_hash = current_hash
                return True, True, has_data

            if current_hash != self._last_data_hash:
                # Data content changed
                self._last_data_hash = current_hash
                return True, False, has_data

            return False, False, has_data
    
    def _monitor_loop(self, check_interval: int = 10):
        """Background monitoring loop"""
        while self._monitoring:
            try:
                self._check_for_changes()
            except Exception as e:
                print(f"Error in monitor loop: {e}")
            
            time.sleep(check_interval)
    
    def _check_for_changes(self):
        """Check if data in A3:H range has changed or been deleted/replaced"""
        result = self.get_workstation_data()
        current_data = result.get("flat_data", [])

        has_changed, was_replaced, has_data = self._has_data_changed(current_data)

        if has_changed:
            change_type = "deleted and replaced" if was_replaced else "modified"
            print(f"[Monitor] Data in A3:H {change_type} at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

            if not has_data:
                print("[Monitor] A3:H is empty, skipping SeaTalk send")
                return

            # Wait 7 seconds before processing
            time.sleep(7)

            # Trigger callback if set
            if self.on_new_data_callback:
                self.on_new_data_callback(current_data)
    
    def start_monitoring(self, check_interval: int = 10):
        """Start background monitoring thread"""
        if self._monitoring:
            return
        
        self._monitoring = True
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            args=(check_interval,),
            daemon=True
        )
        self._monitor_thread.start()
        print(f"[Monitor] Started monitoring with {check_interval}s interval")
    
    def stop_monitoring(self):
        """Stop background monitoring"""
        self._monitoring = False
        if self._monitor_thread:
            self._monitor_thread.join(timeout=2)
        print("[Monitor] Stopped monitoring")
