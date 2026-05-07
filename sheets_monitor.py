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

    def get_first_row_data(self) -> Dict[str, Any]:
        """Get only the first row (A3:H3) for change detection"""
        try:
            client = self._get_client()
            spreadsheet = client.open_by_key(self.spreadsheet_id)

            try:
                worksheet = spreadsheet.worksheet("1. workstation_dump")
            except gspread.WorksheetNotFound:
                worksheet = spreadsheet.worksheet("workstation_dump")

            # Get only A3:H3 (first row)
            first_row = worksheet.get('A3:H3')

            return {
                "first_row": first_row[0] if first_row else [],
                "worksheet": worksheet,
                "is_empty": not first_row or all(cell == "" for cell in first_row[0]) if first_row else True
            }
        except Exception as e:
            print(f"Error getting first row data: {e}")
            return {"first_row": [], "worksheet": None, "is_empty": True, "error": str(e)}
    
    def get_attendance_timein_data(self) -> Dict[str, Any]:
        """Get data from attendance_timein_data tab"""
        try:
            client = self._get_client()
            spreadsheet = client.open_by_key(self.spreadsheet_id)
            
            try:
                worksheet = spreadsheet.worksheet("[do_not_edit] attendance_timein_data")
            except gspread.WorksheetNotFound:
                worksheet = spreadsheet.worksheet("attendance_timein_data")
            
            # Get N4 (overbreak threshold) and M6:M25, O6:O25 (employee list)
            n4_value = worksheet.acell('N4').value
            m_values = worksheet.get('M6:M25')
            o_values = worksheet.get('O6:O25')
            
            return {
                "overbreak_threshold": n4_value,
                "employee_codes": [row[0] for row in m_values if row],
                "overbreak_hours": [row[0] for row in o_values if row]
            }
        except Exception as e:
            print(f"Error getting attendance data: {e}")
            return {"overbreak_threshold": None, "employee_codes": [], "overbreak_hours": [], "error": str(e)}
    
    def get_stored_group_id(self) -> Optional[str]:
        """Get stored group_id from cell A2 of workstation_dump sheet"""
        try:
            result = self.get_workstation_dump_data()
            worksheet = result.get("worksheet")
            if worksheet:
                return worksheet.acell('A2').value
        except Exception as e:
            print(f"Error getting stored group ID: {e}")
        return None
    
    def store_group_id(self, group_id: str) -> bool:
        """Store group_id in cell A2 of workstation_dump sheet"""
        try:
            result = self.get_workstation_dump_data()
            worksheet = result.get("worksheet")
            if worksheet:
                worksheet.update('A2', [[group_id]])
                return True
        except Exception as e:
            print(f"Error storing group ID: {e}")
        return False
    
    def _compute_data_hash(self, data: List) -> str:
        """Compute a simple hash of the data to detect changes"""
        import json
        return json.dumps(data, sort_keys=True)

    def _has_first_row_changed(self, current_row: List) -> tuple[bool, bool]:
        """Check if first row has changed or was deleted/replaced.
        Returns: (has_changed, was_deleted_and_replaced)"""
        current_hash = self._compute_data_hash(current_row)
        is_empty = not current_row or all(cell == "" for cell in current_row)

        with self._lock:
            if self._last_data_hash is None:
                # First run
                self._last_data_hash = current_hash
                return False, False

            # Check if row was deleted (became empty) and now has data
            last_was_empty = self._last_data_hash == self._compute_data_hash([])
            now_has_data = not is_empty

            if last_was_empty and now_has_data:
                # Row was deleted and replaced with new data
                self._last_data_hash = current_hash
                return True, True

            if current_hash != self._last_data_hash:
                # Row content changed
                self._last_data_hash = current_hash
                return True, False

            return False, False
    
    def _monitor_loop(self, check_interval: int = 10):
        """Background monitoring loop"""
        while self._monitoring:
            try:
                self._check_for_changes()
            except Exception as e:
                print(f"Error in monitor loop: {e}")
            
            time.sleep(check_interval)
    
    def _check_for_changes(self):
        """Check if first row (A3:H3) has changed or been deleted/replaced"""
        result = self.get_first_row_data()
        current_row = result.get("first_row", [])

        has_changed, was_replaced = self._has_first_row_changed(current_row)

        if has_changed:
            change_type = "deleted and replaced" if was_replaced else "modified"
            print(f"[Monitor] First row (A3:H3) {change_type} at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

            # Wait 7 seconds before processing
            time.sleep(7)

            # Trigger callback if set
            if self.on_new_data_callback:
                self.on_new_data_callback(current_row)
    
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
