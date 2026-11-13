import os
import sys
import time
import urllib.parse
from dataclasses import dataclass
from typing import Any, Dict, Optional
import requests


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import config


@dataclass
class PlaybackState:
   
    time: float         
    duration: float    
    state: str          
    file_path: str       
    is_seek: bool       


def uri_to_path(raw_uri: str) -> str:
    """
    Converts a VLC URI like 'file:///D:/Movies/My%20Movie.mkv'
    into a clean, OS-valid file path like 'D:\\Movies\\My Movie.mkv'.
    Safe against filenames with '#' or '?' and existing Windows drive paths.
    """
    if not raw_uri:
        return ""
    if raw_uri.startswith("file://"):
        parsed = urllib.parse.urlparse(raw_uri)
        path = urllib.parse.unquote(parsed.path)
        if path.startswith("/") and len(path) > 2 and path[2] == ":":
            path = path[1:]
        return os.path.normpath(path)
    return os.path.normpath(urllib.parse.unquote(raw_uri))


def find_uri_in_playlist(node: Dict[str, Any], target_id: str) -> Optional[str]:
    """Recursively searches the VLC playlist tree for an item matching target_id."""
    if str(node.get("id")) == str(target_id) and "uri" in node:
        return node["uri"]
    
    for child in node.get("children", []):
        result = find_uri_in_playlist(child, target_id)
        if result:
            return result
            
    return None


class VLCClient:
    """Thread-safe client for polling VLC's Lua HTTP API."""

    def __init__(self):
        self.session = requests.Session()
        self.auth = ("", config.VLC_PASSWORD)
        
        # State tracking
        self.last_time = 0.0
        self.last_state = "stopped"
        self.current_plid = -1
        self.cached_file_path = ""

    def _resolve_file_path(self, plid: int, meta_filename: str) -> str:
        """
        Resolves the absolute path of the current file.
        Uses playlist.json lookup if meta.filename is only a relative name.
        """
        # If meta_filename is already an absolute URI or path, use it directly
        if meta_filename.startswith("file://") or (len(meta_filename) > 2 and meta_filename[1] == ":"):
            return uri_to_path(meta_filename)

        # Otherwise, query the playlist tree for the full URI
        try:
            response = self.session.get(config.VLC_PLAYLIST_URL, auth=self.auth, timeout=1.0)
            if response.status_code == 200:
                full_uri = find_uri_in_playlist(response.json(), str(plid))
                if full_uri:
                    return uri_to_path(full_uri)
        except requests.RequestException:
            pass

        # Fallback to whatever string VLC returned in meta
        return meta_filename

    def get_status(self) -> PlaybackState:
        """Polls VLC status and returns the current PlaybackState."""
        try:
            response = self.session.get(config.VLC_STATUS_URL, auth=self.auth, timeout=0.5)
            if response.status_code != 200:
                return PlaybackState(0.0, 0.0, "stopped", "", False)

            data = response.json()
            current_time = float(data.get("time", 0.0))
            duration = float(data.get("length", 0.0))
            state = str(data.get("state", "stopped"))
            plid = int(data.get("currentplid", -1))

            # Extract filename from metadata
            meta = data.get("information", {}).get("category", {}).get("meta", {})
            raw_filename = meta.get("filename", "")

            # If playlist item changed or not yet resolved, lookup full file path
            if plid != self.current_plid or not self.cached_file_path:
                self.current_plid = plid
                self.cached_file_path = self._resolve_file_path(plid, raw_filename)

            # Seek Detection:
            # If playing, and the time jumped by more than SEEK_THRESHOLD_SEC
            is_seek = False
            if state == "playing" and self.last_state == "playing":
                delta = abs(current_time - self.last_time)
                if delta > config.SEEK_THRESHOLD_SEC:
                    is_seek = True

            # Update history
            self.last_time = current_time
            self.last_state = state

            return PlaybackState(
                time=current_time,
                duration=duration,
                state=state,
                file_path=self.cached_file_path,
                is_seek=is_seek
            )

        except requests.RequestException:
            # VLC is closed or unreachable
            self.last_state = "stopped"
            return PlaybackState(0.0, 0.0, "stopped", "", False)


if __name__ == "__main__":
    client = VLCClient()
    print("=" * 70)
    print("VLC Poller Running. Ensure VLC is open and playing a video.")
    print("Press Ctrl+C to terminate.")
    print("=" * 70)

    try:
        while True:
            ps = client.get_status()
            
            # Format time into HH:MM:SS
            m, s = divmod(int(ps.time), 60)
            h, m = divmod(m, 60)
            time_str = f"{h:02d}:{m:02d}:{s:02d}"

            # Shorten path for console readability if long
            display_path = ps.file_path if len(ps.file_path) < 45 else "..." + ps.file_path[-42:]

            print(
                f"\r[{time_str}] State: {ps.state:<7} | "
                f"Seek: {str(ps.is_seek):<5} | "
                f"File: {display_path}",
                end="",
                flush=True
            )
            time.sleep(config.POLL_INTERVAL_SEC)
            
    except KeyboardInterrupt:
        print("\n\nPoller terminated cleanly.")