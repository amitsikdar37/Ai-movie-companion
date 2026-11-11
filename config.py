
VLC_HOST = "127.0.0.1"
VLC_PORT = 8080
VLC_PASSWORD = "admin"
VLC_STATUS_URL = f"http://{VLC_HOST}:{VLC_PORT}/requests/status.json"
VLC_PLAYLIST_URL = f"http://{VLC_HOST}:{VLC_PORT}/requests/playlist.json"

# Ingestion & Synchronization Settings
POLL_INTERVAL_SEC = 0.2        # Polling frequency (5 times per second)
SEEK_THRESHOLD_SEC = 2.0       # Time delta (seconds) triggering a pipeline flush
LOOKAHEAD_SEC = 12.0           # Look-ahead window into the future (seconds)
SLICE_DURATION_SEC = 5.0       # Demuxer extraction slice duration (seconds)

# Sensory Settings
AUDIO_SAMPLE_RATE = 16000      # 16 kHz mono for Faster-Whisper
ACOUSTIC_SPIKE_DB = 15.0       # Sudden dB jump threshold for jump scares