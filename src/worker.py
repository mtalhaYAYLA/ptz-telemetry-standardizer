# (Asenkron Döngü) Telemetriyi ana sistemi yormadan arka planda okur.

import threading
import time
from src.scanner import PTZScanner

class TelemetryWorker(threading.Thread):
    def __init__(self, client, transformer, scan_params):
        super().__init__(daemon=True)
        self.client = client
        self.transformer = transformer
        self.scan_params = scan_params
        self.current_data = {"pan": 0, "tilt": 0}
        self.running = True
        # Rotayı oluştur
        self.points = PTZScanner.generate_tour_points(scan_params)
        
    def run(self):
        import itertools
        tour_iterator = itertools.cycle(self.points)
        wait_sec = self.scan_params.get('wait_sec', 3)
        post_move_delay = self.scan_params.get("post_move_delay_sec", 1.0)

        while self.running:
            # 1. Rotadaki sıradaki noktaya git (X ve Y)
            target_pan, target_tilt = next(tour_iterator)
            
            # AbsoluteMove Komutu (Orijinal _go_to_degree mantığı)
            self.client.move_to(target_pan, target_tilt)
            
            # 2. Hareket sonrası yerleşme süresi (Orijinal kodundaki bekleme)
            time.sleep(post_move_delay)
            
            # 3. Gözlem Süresi (Telemetri verisini bu sırada güncelle)
            end_wait = time.time() + wait_sec
            while time.time() < end_wait and self.running:
                raw = self.client.get_status()
                if raw:
                    p, t = self.transformer.onvif_to_degree(raw.x, raw.y)
                    self.current_data = {"pan": p, "tilt": t}
                time.sleep(0.5)

    def stop(self):
        self.running = False