# (Asenkron Döngü) Telemetriyi ana sistemi yormadan arka planda okur.

import threading
import itertools
import time
from src.scanner import PTZScanner


class TelemetryWorker(threading.Thread):
    """
    Her kamera için bağımsız bir daemon thread çalıştırır.

    Sorumlulukları:
        1. PTZScanner'dan aldığı tur noktalarını sırayla kameraya gönderir.
        2. Her hareket sonrası wait_sec boyunca 500ms aralıklarla telemetri okur.
        3. Okunan Pan/Tilt verisini thread-safe biçimde current_data'ya yazar.
        4. Ağ hatası veya istisna durumunda 10 saniye bekleyip döngüye devam eder.

    Attributes:
        client (CameraClient)    : Kamera bağlantı ve kontrol nesnesi.
        transformer (PTZTransformer): ONVIF → derece dönüştürücü.
        scan_params (dict)       : config.json'dan gelen tarama parametreleri.
        current_data (dict)      : {"pan": float, "tilt": float} — son okunan konum.
        running (bool)           : False yapılınca döngü güvenli şekilde durur.
        points (list)            : Scanner'ın ürettiği (pan, tilt) tur noktaları.
    """

    def __init__(self, client, transformer, scan_params):
        """
        Args:
            client (CameraClient)      : Bağlantısı kurulmuş kamera nesnesi.
            transformer (PTZTransformer): Koordinat dönüştürücü.
            scan_params (dict)         : scan_parameters bloğu (config.json'dan).
                Beklenen anahtarlar:
                    - wait_sec           (float): Her noktada bekleme süresi. Varsayılan: 3.
                    - post_move_delay_sec (float): Hareket sonrası yerleşme süresi. Varsayılan: 1.0.
        """
        super().__init__(daemon=True)
        self.client = client
        self.transformer = transformer
        self.scan_params = scan_params
        self.current_data = {"pan": 0, "tilt": 0}
        self._lock = threading.Lock()
        self.running = True
        self.points = PTZScanner.generate_tour_points(scan_params)

    @property
    def safe_data(self):
        """
        current_data'yı thread-safe okur.

        Returns:
            dict: {"pan": float, "tilt": float} — anlık kopya.
        """
        with self._lock:
            return dict(self.current_data)

    def run(self):
        """
        Thread giriş noktası. Tur noktalarını sonsuz döngüyle işler.

        Adımlar (her tur noktası için):
            1. move_to()  : Kamerayı hedef Pan/Tilt'e gönder.
            2. sleep()    : post_move_delay_sec kadar yerleşme süresi bekle.
            3. Telemetri  : wait_sec boyunca 500ms'de bir get_status() ile konum oku,
                            okunan değeri lock altında current_data'ya yaz.
            4. Hata       : İstisna olursa 10 saniye bekle, döngüye devam et.

        Returns:
            None
        """
        tour_iterator = itertools.cycle(self.points)
        wait_sec = self.scan_params.get('wait_sec', 3)
        post_move_delay = self.scan_params.get("post_move_delay_sec", 1.0)

        while self.running:
            try:
                target_pan, target_tilt = next(tour_iterator)
                self.client.move_to(target_pan, target_tilt)
                time.sleep(post_move_delay)

                end_wait = time.time() + wait_sec
                while time.time() < end_wait and self.running:
                    raw = self.client.get_status()
                    if raw:
                        p, t = self.transformer.onvif_to_degree(raw.x, raw.y)
                        with self._lock:
                            self.current_data = {"pan": p, "tilt": t}
                    time.sleep(0.5)

            except Exception as e:
                print(f"[{self.client.name}] Thread hatası: {e}. 10 saniye sonra devam ediliyor...")
                time.sleep(10)

    def stop(self):
        """
        Tarama döngüsünü güvenli şekilde durdurur.

        running bayrağını False yapar; mevcut bekleme süresi dolduğunda
        thread kendiliğinden sonlanır (daemon=True olduğundan zorla kill gerekmez).

        Returns:
            None
        """
        self.running = False
