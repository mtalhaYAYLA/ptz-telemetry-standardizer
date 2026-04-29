import sys
import os
import cv2
import time
import threading
import requests
import json
import logging
import numpy as np
from onvif import ONVIFCamera
from onvif.exceptions import ONVIFError
from requests.auth import HTTPDigestAuth
from dotenv import load_dotenv
import sqlite3
from datetime import datetime
from queue import Queue, Empty
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
import pprint  # en üste eklendiğinden emin ol

# .env dosyasını yükle
load_dotenv()

# Sabitleri .env dosyasından al
LOGS_DIR = os.getenv("LOGS_DIR", "logs")
EVENTS_DIR = os.getenv("EVENTS_DIR", "events")
CONFIG_FILE = os.getenv("CONFIG_FILE", "config.json")
DB_PATH = os.getenv("SQLITE_DB_PATH", "events.db")
API_KEY = os.getenv("API_KEY", "deneme-key")
BASE_URL = os.getenv("BASE_URL", "https://localhost:7300/api/AI")


# RTSP akışları için ortam değişkenleri
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"

# Kameranın fiziksel ve ONVIF tilt limitleri. Genellikle standarttır.
TILT_DEGREE_RANGE = (-5, 90)
ONVIF_TILT_RANGE = (-1.0, 1.0)


def setup_logging():
    """
    Loglama sistemini yapılandırır. İki farklı log dosyası oluşturur:
    1. system.log: Genel sistem olayları için.
    2. anomaly.log: Sadece sıcaklık alarmları (anomaliler) için.
    Ayrıca tüm logları anlık takip için konsola da basar.
    """
    os.makedirs(LOGS_DIR, exist_ok=True)
    log_formatter = logging.Formatter('%(asctime)s - %(threadName)s - %(levelname)s - %(message)s')

    # Konsol logları
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(log_formatter)

    # Sistem log dosyası
    system_log_handler = logging.FileHandler(os.path.join(LOGS_DIR, "system.log"))
    system_log_handler.setFormatter(log_formatter)

    # Kök (root) logger'ı ayarla (system.log ve konsol için)
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(system_log_handler)

    # Anomali log dosyası için ayrı bir logger oluştur
    anomaly_logger = logging.getLogger("AnomalyLogger")
    anomaly_logger.setLevel(logging.WARNING) # Sadece WARNING ve üstü (ERROR, CRITICAL) logları alır
    anomaly_log_handler = logging.FileHandler(os.path.join(LOGS_DIR, "anomaly.log"))
    anomaly_log_handler.setFormatter(log_formatter)
    anomaly_logger.addHandler(anomaly_log_handler)
    anomaly_logger.propagate = False # Logların root logger'a tekrar gitmesini engeller

    return root_logger, anomaly_logger

def load_config_from_file():
    """
    config.json dosyasını okur ve içeriğini döndürür.
    Dosya bulunamazsa veya hatalıysa programı sonlandırır.
    """
    if not os.path.exists(CONFIG_FILE):
        logging.critical(f"Yapılandırma dosyası bulunamadı: {CONFIG_FILE}")
        sys.exit(1)
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        logging.critical(f"Yapılandırma dosyası hatalı (JSON formatında değil): {e}")
        sys.exit(1)
    except Exception as e:
        logging.critical(f"Yapılandırma dosyası okunurken hata: {e}")
        sys.exit(1)

def load_config_from_URL():
    """
    Kamera yapılandırmalarını API'den çeker.
    """
    try:
        response = requests.get(f"{BASE_URL}/GetCameraJson", headers={"API-KEY": API_KEY}, verify=False, timeout=10)
        response.raise_for_status()
        data = response.json()
        print("cameras", data)
        if not data.get("cameras"):
            logging.critical("API'den kamera verisi alınamadı.")
            sys.exit(1)
        return data
    except Exception as e:
        logging.critical(f"API yapılandırma alınırken hata: {e}")
        sys.exit(1)

def load_hybrid_config():
    """
    Yerel dosyadan (config.json) gelen tarama/alarm ayarlarını,
    API'den gelen kamera bilgilerine entegre eder.
    """
    local_config = load_config_from_file()
    remote_config = load_config_from_URL()

    # 1. Yerel dosyadaki kameraları camera_id'ye göre indeksle
    local_camera_map = {
        cam["camera_id"]: cam
        for cam in local_config.get("cameras", [])
        if cam.get("isActive", "True")
    }

    # print("local_camera_map ", local_camera_map )
    
    # 2. En küçük priority limitStart değeri belirle
    priorities = remote_config.get("priorities", [])
    active_priorities = [p for p in priorities if p.get("isActive")]
    min_threshold = min((p["limitStart"] for p in active_priorities if p.get("limitStart") is not None), default=25.0)
    print("Minimum threshold değeri: ", min_threshold)
    logging.info(f"[HYBRID] Minimum threshold değeri: {min_threshold}°C")


    # 2. API'den gelen her kameraya uygun parametreleri ekle
    enriched_cameras = []
    for cam in remote_config.get("cameras", []):
        if not cam.get("isActive", True):
            continue  # API tarafındaki pasif kamerayı direkt atla
                
        cam_id = cam["id"]
        # Yerel yapılandırmayı bul
        local = local_camera_map.get(cam_id)
        if local:
            cam["scan_parameters"] = local.get("scan_parameters", {})
            cam["alarm_settings"] = local.get("alarm_settings", {})
        else:
            logging.warning(f"[HYBRID] Kamera ID {cam_id} için yerel ayar bulunamadı.")
        # # API tarafındaki alan adlarını projeyle uyumlu hâle getir
        # cam["camera_id"] = cam["id"]  # CameraMonitor class için
        # cam["ip_address"] = cam["ip"]
        # cam["credentials"] = {
        #     "user": cam.get("username", ""),
        #     "pass": cam.get("password", "")
        # }
        
        # threshold_c her durumda minimum değere eşitlenir
        cam["alarm_settings"]["threshold_c"] = min_threshold
        
        enriched_cameras.append(cam)
    print(remote_config.get("priorities", []))
    return {
        "cameras": enriched_cameras,
        "usecases": remote_config.get("usecases", []),
        "priorities": remote_config.get("priorities", [])
    }

# def find_priority_id_from_temp(temp_value, priorities):
#     """
#     Belirtilen sıcaklık değerine göre uygun priority_id'yi bulur.
#     - Sıcaklık, priorities listesinde limitStart ve limitEnd aralığında olmalıdır.
#     - limitEnd = None ise sonsuz kabul edilir.
#     """
#     # print(f"📊 Sıcaklık: {temp_value:.1f}°C, Öncelik listesi: {priorities}")
#     for priority in priorities:
#         # if not priority.get("isActive", True):
#         #     continue
#         start = priority.get("limitStart")
#         end = priority.get("limitEnd")
#         if start is None:
#             continue
#         if end is None:
#             if temp_value >= start:
#                 return priority["id"]
#         elif start <= temp_value <= end:
#             return priority["id"], priority["name"]
#     return None  # eşleşme yoksa
def find_priority_id_from_temp(temp_value, priorities):
    """
    Belirtilen sıcaklık değerine göre uygun priority_id ve ismini bulur.

    - Sıcaklık, priorities listesindeki limitStart ve limitEnd aralığında olmalıdır.
    - limitEnd = None ise, limitStart'tan büyük veya eşit tüm değerler eşleşir.
    - Eşleşme bulunamazsa, varsayılan olarak (10, "Normal") döner.
    """
    # priorities listesindeki her bir öncelik tanımı için döngü başlatılır
    for priority in priorities:
        start = priority.get("limitStart")
        end = priority.get("limitEnd")

        # Geçerli bir başlangıç limiti yoksa bu önceliği atla
        if start is None:
            continue

        # Bitiş limiti tanımsız ise (örn: 130 ve üstü)
        if end is None:
            if temp_value >= start:
                return priority["id"], priority["name"]  # Eşleşme bulundu
        # Başlangıç ve bitiş limiti tanımlı ise
        elif start <= temp_value <= end:
            return priority["id"], priority["name"]  # Eşleşme bulundu

    # Döngü tamamlandı ve hiçbir aralık eşleşmediyse varsayılan değeri döndür
    return 10, "Normal"

def find_usecase_info_by_camera_id(camera_id, usecases):
    """
    Verilen camera_id'ye karşılık gelen usecaseId ve usecase adını bulur.
    
    Geri dönüş:
        - Eşleşme varsa: (usecase_id, usecase_name)
        - Eşleşme yoksa: (None, None)
    """
    for usecase in usecases:
        for cu in usecase.get("cameraUsecases", []):
            if cu.get("cameraId") == camera_id:
                return usecase.get("id"), usecase.get("name")
    return None, None

def setup_database():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            camera_id TEXT NOT NULL,
            temperature REAL NOT NULL,
            folder_path TEXT NOT NULL,
            api_sent INTEGER DEFAULT 0,
            api_event_id INTEGER,
            file_sent INTEGER DEFAULT 0,
            retry_count INTEGER DEFAULT 0,
            last_try_ts TEXT,
            usecase_id INTEGER,
            usecase_name TEXT,
            priority_id INTEGER,
            priority_name TEXT,
            is_event INTEGER DEFAULT 5 -- 5: Anomali, 6: Normal 
        )
    """)
    conn.commit()
    conn.close()


def save_event_to_db(camera_id, timestamp, temperature, folder_path,
                     usecase_id=None, priority_id=None,
                     usecase_name=None, priority_name=None,
                     is_event=0):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO events (
            camera_id, timestamp, temperature, folder_path,
            usecase_id, priority_id,
            usecase_name, priority_name, is_event
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        camera_id, timestamp, temperature, folder_path,
        usecase_id, priority_id,
        usecase_name, priority_name, is_event
    ))
    event_id = cur.lastrowid
    conn.commit()
    conn.close()
    return event_id


# CameraMonitor ve ThermalDataThread sınıfları öncekiyle büyük ölçüde aynı,
# ancak loglama ve yapılandırma entegrasyonu için küçük değişiklikler içeriyor.

class ThermalDataThread(threading.Thread):
    # Bu sınıf, bir önceki versiyonla tamamen aynı kalabilir.
    # Anlaşılırlık için tekrar eklenmiştir.
    def __init__(self, name, url, auth, data_callback, status_callback):
        super().__init__(name=f"ThermalData-{name}")
        self._run_flag = True
        self.url = url
        self.auth = auth
        self.data_callback = data_callback
        self.status_callback = status_callback

    def run(self):
        logging.info("Termal veri dinleyici başlatıldı.")
        while self._run_flag:
            try:
                with requests.get(self.url, auth=self.auth, stream=True, timeout=(5, 20)) as response:
                    if response.status_code == 200:
                        self.status_callback("Bağlandı")
                        buffer = b''
                        for chunk in response.iter_content(chunk_size=1024):
                            if not self._run_flag: break
                            buffer += chunk
                            while b'--boundary' in buffer:
                                parts = buffer.split(b'--boundary', 1)
                                block, buffer = parts[0], parts[1]
                                if b'Content-Type: application/json' in block:
                                    json_start = block.find(b'{'); json_end = block.rfind(b'}')
                                    if json_start != -1 and json_end != -1:
                                        # try:
                                        #     self.data_callback(json.loads(block[json_start:json_end+1].decode('utf-8')))
                                        # except json.JSONDecodeError:
                                        #     logging.warning("JSON çözme hatası alındı.")
                                        # 1. Ham veriyi string'e çevir.
                                        raw_json_string = block[json_start:json_end+1].decode('utf-8', errors='ignore')

                                        try:
                                            # 2. String'i JSON'a ayrıştır.
                                            parsed_data = json.loads(raw_json_string)

                                            # 3. GÜVENLİK KONTROLLERİ BAŞLANGICI
                                            # Gelen veri bir sözlük (dictionary) değilse atla.
                                            if not isinstance(parsed_data, dict):
                                                logging.debug(f"Atlanan veri (sözlük değil): {parsed_data}")
                                                continue # Bu veriyi atla ve döngüye devam et

                                            # İçinde beklediğimiz anahtar yapıları yoksa atla.
                                            if 'ThermometryUploadList' in parsed_data and \
                                            isinstance(parsed_data.get('ThermometryUploadList'), dict) and \
                                            'ThermometryUpload' in parsed_data.get('ThermometryUploadList'):
                                                
                                                # Tüm kontrollerden geçtiyse, bu veri güvenilirdir.
                                                self.data_callback(parsed_data)
                                            else:
                                                logging.debug(f"Atlanan veri (beklenen anahtarlar yok): {parsed_data}")
                                            # GÜVENLİK KONTROLLERİ SONU

                                        except json.JSONDecodeError:
                                            # Hata durumunda ham veriyi logla ki sorunu görebilelim.
                                            logging.warning(f"JSON çözme hatası. Atlanan ham veri: {raw_json_string}")
                    else:
                        msg = f"Bağlantı hatası (HTTP {response.status_code})"
                        self.status_callback(msg); logging.warning(f"[{self.name.replace('ThermalData-','')}] {msg}")
                        time.sleep(5)
            except requests.exceptions.RequestException as e:
                msg = f"Bağlantı kesildi: {e}"
                self.status_callback(msg); logging.error(f"[{self.name.replace('ThermalData-','')}] {msg}")
                time.sleep(5)
        logging.info("Termal veri dinleyici durduruldu.")

    def stop(self):
        self._run_flag = False

class CameraMonitor():
    """
    Bu en güncel versiyon, RTSP akışının kilitlenmesini önlemek için
    zaman aşımlı görüntü yakalama mekanizması içerir.
    """
    def __init__(self, config, anomaly_logger, ram_queue, priorities=None, usecases=None):
        self._run_flag = True
        self.config = config
        self.name = config["name"]
        self.camera_id = config.get("id", "unknown")
        # ... (init metodunun geri kalanı aynı)
        self.anomaly_logger = anomaly_logger
        self.ram_queue = ram_queue
        self.priorities = priorities or []
        self.usecases = usecases or []
        self.rtsp_normal_url = f'rtsp://{self.config["username"]}:{self.config["password"]}@{config["ip"]}:554/Streaming/Channels/101'
        self.rtsp_thermal_url = f'rtsp://{self.config["username"]}:{self.config["password"]}@{config["ip"]}:554/Streaming/Channels/201'
        self.normal_snapshot_url = f'http://{config["ip"]}/ISAPI/Streaming/channels/1/picture'
        self.thermal_snapshot_url = f'http://{config["ip"]}/ISAPI/Streaming/channels/2/picture'
        self.thermal_api_url = f'http://{config["ip"]}/ISAPI/Thermal/channels/2/thermometry/realTimethermometry/rules?format=json'
        self.onvif_cam, self.ptz, self.profile_token = None, None, None
        self.lock = threading.Lock()
        self.last_thermal_data, self.last_max_temp = None, None
        self.last_alarm_time = 0
        self.is_healthy = False
        self.status_message = "Başlatılıyor..."
        self.thermal_thread_status = "Bekliyor..."
        self.auth = HTTPDigestAuth(self.config["username"], self.config["password"])
        #v4 eklemeleri
        self.scan_paused = False
        self.scan_pause_end_time = 0
        self.anomaly_candidate_buffer = []

        # === YENİ: AKILLI FİLTRELEME (BUFFER SİSTEMİ) DEĞİŞKENLERİ ===
        smart_config = self.config['alarm_settings'].get('smart_filtering', {})
        self.smart_filtering_enabled = smart_config.get('enabled', False)
        self.buffer_duration = smart_config.get('buffer_duration_sec', 60)
        self.buffer_max_count = smart_config.get('buffer_max_count', 10)
        self.buffer_min_count = smart_config.get('buffer_min_count', 3)
        self.selection_method = smart_config.get('selection_method', 'median')
        self.smart_priority_ids = smart_config.get('priority_ids', [9])
        self.pause_duration = smart_config.get('pause_duration_sec', 60)

        self.anomaly_buffer = []  # Buffer: {timestamp, temperature, priority_id, priority_name, temp_folder, data_snapshot}
        self.buffer_start_time = None

    def _activate_scan_pause(self):
        """Öncelik 9 algılandığında taramayı config'den gelen süre kadar duraklatır."""
        if not self.scan_paused: # Eğer zaten duraklatılmış değilse
            duration_seconds = self.config['alarm_settings'].get('priority_9_pause_duration_sec', 120)
            logging.warning(f"[{self.name}] Özel kontrol ID'si algılandı! Tarama {duration_seconds} saniye duraklatılıyor.")
            self.scan_paused = True
            self.scan_pause_end_time = time.time() + duration_seconds
            self.status_message = f"Özel Gözlem için {duration_seconds}sn duraklatıldı."
    
    def _handle_smart_filtering(self, max_temp, data_snapshot, priority_id, priorityName):
        """
        Akıllı filtreleme: Buffer'a hem JSON hem de RESİMLER eklenir
        """
        
        # === İLK VERİ: TARAMAYI DURDUR ===
        if self.buffer_start_time is None:
            self._activate_scan_pause()
            self.buffer_start_time = time.time()
            logging.info(f"[{self.name}] 📦 Buffer başlatıldı ({self.buffer_duration}s / {self.buffer_max_count} adet)")
        
        # === GEÇİCİ KLASÖR OLUŞTUR ===
        timestamp = time.strftime("%Y-%m-%d_%H-%M-%S_%f")[:-3]
        temp_folder = os.path.join(EVENTS_DIR, "temp_buffer", self.name, timestamp)
        os.makedirs(temp_folder, exist_ok=True)
        
        # === RESİMLERİ ÇEK VE KAYDET ===
        normal_frame = self._capture_frame_http(self.normal_snapshot_url)
        thermal_frame = self._capture_frame_http(self.thermal_snapshot_url)
        
        if normal_frame is not None:
            cv2.imwrite(os.path.join(temp_folder, "normal.jpg"), normal_frame)
        
        if thermal_frame is not None:
            cv2.imwrite(os.path.join(temp_folder, "thermal.jpg"), thermal_frame)
        
        # === JSON VERİSİNİ KAYDET ===
        if data_snapshot:
            with open(os.path.join(temp_folder, "data.json"), 'w', encoding='utf-8') as f:
                json.dump(data_snapshot, f, ensure_ascii=False, indent=4)
        
        # === BUFFER'A EKLE ===
        self.anomaly_buffer.append({
            'timestamp': time.time(),
            'temperature': max_temp,
            'priority_id': priority_id,
            'priority_name': priorityName,
            'temp_folder': temp_folder,
            'data_snapshot': data_snapshot
        })
        
        logging.info(f"[{self.name}] 📦 Buffer'a eklendi ({len(self.anomaly_buffer)}/{self.buffer_max_count}): {max_temp:.1f}°C")
        
        # === BUFFER KONTROL (YENİ MİNİMUM KONTROL İLE) ===
        current_count = len(self.anomaly_buffer)
        elapsed = time.time() - self.buffer_start_time
        
        # KONTROL 1: Buffer dolu mu? (10 adet)
        if current_count >= self.buffer_max_count:
            logging.info(f"[{self.name}] ✅ Buffer DOLU ({self.buffer_max_count} adet)")
            self._process_buffer()
        
        # KONTROL 2: Süre doldu MU + Minimum adet kontrolü (en az 3 adet)
        elif elapsed >= self.buffer_duration:
            if current_count >= self.buffer_min_count:
                logging.info(f"[{self.name}] ⏰ Buffer SÜRESİ DOLDU ({elapsed:.1f}s) ve yeterli veri var ({current_count} adet)")
                self._process_buffer()
            else:
                logging.warning(f"[{self.name}] ⚠️  Buffer süresi doldu AMMA yetersiz veri! ({current_count}/{self.buffer_min_count} adet)")
                logging.warning(f"[{self.name}] ⏳ Minimum {self.buffer_min_count} adet bekleniyor...")
        
        # # === BUFFER KONTROL === ilksorunsuz sayaç yok
        
        # elapsed = time.time() - self.buffer_start_time
        
        # if len(self.anomaly_buffer) >= self.buffer_max_count:
        #     logging.info(f"[{self.name}] ✅ Buffer DOLU ({self.buffer_max_count} adet)")
        #     self._process_buffer()
        
        # elif elapsed >= self.buffer_duration:
        #     logging.info(f"[{self.name}] ⏰ Buffer SÜRESİ DOLDU ({elapsed:.1f}s)")
        #     self._process_buffer()

    def _process_buffer(self):
        """Buffer'daki verileri değerlendir ve EN İYİSİNİ _trigger_log2() ile kaydet"""
        
        if not self.anomaly_buffer:
            return
        
        # 1. SIRALAMA
        sorted_buffer = sorted(self.anomaly_buffer, key=lambda x: x['temperature'])
        
        # 2. SEÇİM METODU (CONFIG'DEN)
        if self.selection_method == "median":
            mid_index = len(sorted_buffer) // 2
            selected = sorted_buffer[mid_index]
            logging.info(f"[{self.name}] 📊 MEDIAN seçildi: {selected['temperature']:.1f}°C")
        
        elif self.selection_method == "average":
            avg_temp = sum(x['temperature'] for x in sorted_buffer) / len(sorted_buffer)
            selected = min(sorted_buffer, key=lambda x: abs(x['temperature'] - avg_temp))
            logging.info(f"[{self.name}] 📊 AVERAGE seçildi: {selected['temperature']:.1f}°C")
        
        elif self.selection_method == "max":
            selected = sorted_buffer[-1]
            logging.info(f"[{self.name}] 📊 MAX seçildi: {selected['temperature']:.1f}°C")
        
        else:  # "last" (sonuncu)
            selected = self.anomaly_buffer[-1]
            logging.info(f"[{self.name}] 📊 LAST seçildi: {selected['temperature']:.1f}°C")
        
        # 3. YENİ FONKSİYON İLE KAYDET (_trigger_log2)
        logging.info(f"[{self.name}] 💾 Kaydediliyor: {selected['temperature']:.1f}°C")
        self.last_alarm_time = time.time()
        
        self._trigger_log2(
            temp_value=selected['temperature'],
            event_data_snapshot=selected['data_snapshot'],
            temp_folder_path=selected['temp_folder'],
            is_event=5
        )
        
        # 4. GEÇİCİ DOSYALARI TEMİZLE
        import shutil
        for item in self.anomaly_buffer:
            try:
                if os.path.exists(item['temp_folder']):
                    shutil.rmtree(item['temp_folder'])
            except Exception as e:
                logging.warning(f"[{self.name}] Geçici klasör silinemedi: {e}")
        
        # 5. BUFFER SIFIRLA
        count = len(self.anomaly_buffer)
        self.anomaly_buffer.clear()
        self.buffer_start_time = None
        logging.info(f"[{self.name}] 🔄 Buffer sıfırlandı ({count} adet)")

    def _capture_frame_http(self, url, timeout=5):
        """
        Belirtilen URL'e bir HTTP GET isteği göndererek anlık bir görüntü yakalar.
        Bu yöntem, RTSP akışına bağlanmaktan çok daha hızlıdır.
        """
        try:
            response = requests.get(url, auth=self.auth, timeout=timeout)
            response.raise_for_status()
            image_array = np.frombuffer(response.content, dtype=np.uint8)
            frame = cv2.imdecode(image_array, cv2.IMREAD_COLOR)

            if frame is not None:
                logging.info(f"[{self.name}] HTTP üzerinden görüntü başarıyla yakalandı.")
                return frame
            else:
                logging.warning(f"[{self.name}] HTTP üzerinden gelen görüntü verisi decode edilemedi.")
                return None
        except requests.exceptions.RequestException as e:
            logging.error(f"[{self.name}] HTTP görüntü yakalama hatası: {e}")
            return None

    # === YENİ VE GÜVENLİ GÖRÜNTÜ YAKALAMA FONKSİYONU ===
    def _capture_frame_with_timeout(self, rtsp_url, timeout=15):
        """
        RTSP akışından görüntü yakalamayı dener. Belirtilen sürede
        başarılı olamazsa None döner ve kilitlenmeyi önler.
        """
        frame_queue = Queue()

        def capture_worker(url, q):
            cap = None
            try:
                cap = cv2.VideoCapture(url)
                if not cap.isOpened():
                    q.put(None)
                    return
                # Birkaç kare atlayarak buffer'ı temizle
                for _ in range(5):
                    cap.grab()
                ret, frame = cap.read()
                q.put(frame if ret and frame is not None else None)
            except Exception as e:
                logging.error(f"[{self.name}] Görüntü yakalama (worker) hatası: {e}")
                q.put(None)
            finally:
                if cap:
                    cap.release()

        capture_thread = threading.Thread(target=capture_worker, args=(rtsp_url, frame_queue))
        capture_thread.daemon = True
        capture_thread.start()

        try:
            # Belirtilen süre kadar bekle
            frame = frame_queue.get(timeout=timeout)
            if frame is not None:
                logging.info(f"[{self.name}] Görüntü başarıyla yakalandı (URL: ...{rtsp_url[-20:]}).")
            else:
                logging.warning(f"[{self.name}] Görüntü yakalanamadı (boş kare) (URL: ...{rtsp_url[-20:]}).")
            return frame
        except Empty:
            logging.error(f"[{self.name}] Görüntü yakalama ZAMAN AŞIMINA UĞRADI ({timeout}s)! Kilitlenme önlendi. (URL: ...{rtsp_url[-20:]})")
            return None

    
    # DİĞER TÜM METODLAR (run, stop, _connect_onvif, vb.) ÖNCEKİ MESAJDAKİ GİBİ AYNI KALIYOR.
    # Bu metotları da tamlık için aşağıya ekliyorum.
    def _update_thermal_data(self, data):
        with self.lock:
            # Gelen JSON verisine, bizim sistemimiz tarafından alındığı anın
            # zaman damgasını ekliyoruz. Bu, verinin "tazeliğini" ölçecek.
            data['system_receive_time'] = time.time()
            self.last_thermal_data = data
            try:
                therm_data = data.get('ThermometryUploadList', {}).get('ThermometryUpload', [{}])[0]
                self.last_max_temp = therm_data.get('LinePolygonThermCfg', {}).get('MaxTemperature')
            except (IndexError, KeyError, TypeError):
                self.last_max_temp = None

    def _connect_onvif(self):
        try:
            logging.info(f"[{self.name}] ONVIF ile bağlantı kuruluyor...")
            self.onvif_cam = ONVIFCamera(self.config["ip"], 80, self.config["username"], self.config["password"])
            self.ptz = self.onvif_cam.create_ptz_service()
            media_service = self.onvif_cam.create_media_service()
            profiles = media_service.GetProfiles()
            ptz_profile = next((p for p in profiles if hasattr(p, 'PTZConfiguration') and p.PTZConfiguration is not None), None)

            if not ptz_profile:
                self.status_message = "HATA: PTZ destekli profil bulunamadı."
                logging.error(f"[{self.name}] {self.status_message}")
                self.is_healthy = False
                return False

            self.profile_token = ptz_profile.token
            self.status_message = "ONVIF Bağlantısı Başarılı."
            logging.info(f"[{self.name}] {self.status_message}")
            self.is_healthy = True
            return True
        except Exception as e:
            self.status_message = f"ONVIF Bağlantı Hatası: {e}"
            logging.critical(f"[{self.name}] {self.status_message}")
            self.is_healthy = False
            return False
            
    def _convert_degrees_to_ptz(self, pan_deg, tilt_deg):
        corrected_pan_deg = (pan_deg - 180 + 360) % 360
        pan_onvif = np.interp(corrected_pan_deg, [0, 360], [-1, 1])
        tilt_onvif = np.interp(tilt_deg, TILT_DEGREE_RANGE, [ONVIF_TILT_RANGE[1], ONVIF_TILT_RANGE[0]])
        return pan_onvif, tilt_onvif

    def _go_to_degree(self, pan_deg, tilt_deg):
        if not self.ptz: return
        pan_onvif, tilt_onvif = self._convert_degrees_to_ptz(pan_deg, tilt_deg)
        req = self.ptz.create_type('AbsoluteMove')
        req.ProfileToken = self.profile_token
        req.Position = {'PanTilt': {'x': pan_onvif, 'y': tilt_onvif},'Zoom': {'x': 0.0} }
        req.Speed = {'PanTilt': {'x': 1.0, 'y': 1.0},'Zoom': {'x': 1.0}}
        try:
            self.ptz.AbsoluteMove(req)
            logging.info(f"[{self.name}] Pozisyona gidiliyor: Pan={pan_deg}°, Tilt={tilt_deg}°")
            time.sleep(2)
        except ONVIFError as e:
            logging.error(f"[{self.name}] PTZ hareket hatası: {e}")
            self._connect_onvif()

    # _trigger_log artık yeni güvenli fonksiyonu kullanacak
    # def _trigger_log(self, temp_value, is_event=5):
    #     timestamp = time.strftime("%Y-%m-%d_%H-%M-%S")
    #     event_folder_path = os.path.join(EVENTS_DIR, self.name, timestamp)
    #     try:
    #         os.makedirs(event_folder_path, exist_ok=True)
    #     except OSError as e:
    #         logging.error(f"[{self.name}] Olay klasörü oluşturulamadı: {e}")
    #         return
    #     if is_event == 5:
    #         log_msg = f"ANOMALİ TESPİT EDİLDİ! Sıcaklık: {temp_value:.1f}°C."
    #         self.anomaly_logger.warning(f"[{self.name}] {log_msg}")
    #         logging.warning(f"[{self.name}] {log_msg} Kayıtlar '{event_folder_path}' klasörüne yapılıyor.")
        
    #     # === DEĞİŞİKLİK BURADA: Artık zaman aşımlı fonksiyonu çağırıyoruz ===
    #     time.sleep(1.5)
    #     normal_frame = self._capture_frame_with_timeout(self.rtsp_normal_url)
    #     # if normal_frame is not None:
    #     #     cv2.imwrite(os.path.join(event_folder_path, "normal.jpg"), normal_frame)
    #     if normal_frame is not None:
    #         # --- ROI ÇİZME ve KAYDETME BAŞLANGICI ---
    #         try:
    #             # Sabit koordinatlarla dikdörtgeni doğrudan resmin üzerine çiz
    #             pt1 = (1008, 588)#(1017, 450)
    #             pt2 = (1680, 928)#(1858, 1085)
    #             cv2.rectangle(normal_frame, pt1, pt2, (0, 255, 0), 3)

    #             # İşlenmiş bu son halini "normal.jpg" olarak kaydet
    #             cv2.imwrite(os.path.join(event_folder_path, "normal.jpg"), normal_frame)
    #             logging.info(f"[{self.name}] ROI çizilmiş normal resim kaydedildi.")
    #         except Exception as e:
    #             logging.error(f"[{self.name}] Normal resme ROI çizilirken/kaydedilirken hata oluştu: {e}")
    #         # --- ROI ÇİZME ve KAYDETME SONU ---
            
    #     thermal_frame = self._capture_frame_with_timeout(self.rtsp_thermal_url)
    #     # if thermal_frame is not None:
    #     #     cv2.imwrite(os.path.join(event_folder_path, "thermal.jpg"), thermal_frame)
    #     if thermal_frame is not None:
    #         # --- Termal görüntüyü video ayarlarından 1280x960 boyutunda ayarlandığı için bu işlemi yapmaktayız. 
    #         # --- 16:9 FORMATLAMA ve KAYDETME BAŞLANGICI ---
    #         try:
    #             target_w, target_h = 1710, 960
    #             background = np.full((target_h, target_w, 3), 255, dtype=np.uint8)
    #             th, tw, _ = thermal_frame.shape
    #             x_offset = (target_w - tw) // 2
                
    #             # Orijinal termal resmi beyaz arka planın üzerine kopyala
    #             background[0:th, x_offset:x_offset + tw] = thermal_frame
                
    #             # İşlenmiş bu son halini "thermal.jpg" olarak kaydet
    #             cv2.imwrite(os.path.join(event_folder_path, "thermal.jpg"), background)
    #             logging.info(f"[{self.name}] 16:9 formatlı termal resim kaydedildi.")
    #         except Exception as e:
    #             logging.error(f"[{self.name}] Termal resmi 16:9 yaparken/kaydederken hata oluştu: {e}")
    #         # --- 16:9 FORMATLAMA ve KAYDETME SONU ---

    #     with self.lock:
    #         if self.last_thermal_data:
    #             with open(os.path.join(event_folder_path, "data.json"), 'w', encoding='utf-8') as f:
    #                 json.dump(self.last_thermal_data, f, ensure_ascii=False, indent=4)
    #     #--------------------
    #     #priority_id, priorityName = find_priority_id_from_temp(temp_value, self.priorities) #!!
    #     priority_id = None
    #     priorityName = "TANIMSIZ" # Varsayılan değer
    #     priority_result = find_priority_id_from_temp(temp_value, self.priorities)
        
    #     if priority_result is not None:
    #         # Eğer fonksiyon bir sonuç döndürdüyse (None değilse), o zaman parçala
    #         priority_id, priorityName = priority_result
    #     else:
    #         # Eğer None döndürdüyse, logla ve varsayılan değerlerle devam et
    #         logging.warning(f"[{self.name}] Sıcaklık değeri ({temp_value}°C) için uygun bir öncelik aralığı bulunamadı.")
    #     #--------------------
    #     usecase_id, usecase_name = find_usecase_info_by_camera_id(self.camera_id, self.usecases)
    #     #print(f"[{self.name}] Anomali için öncelik ID: {priority_id}, Usecase ID: {usecase_id}")
    #     # SQLite veritabanına kaydet
    #     event_folder_relative_path = "/" + event_folder_path.split("/database/", 1)[-1]
    #     event_id = save_event_to_db(
    #         self.camera_id,
    #         timestamp,
    #         temp_value,
    #         event_folder_relative_path,
    #         usecase_id=usecase_id,
    #         usecase_name=usecase_name,
    #         priority_id=priority_id,
    #         priority_name=priorityName,
    #         is_event=is_event,
    #     )
    #     if event_id: self.ram_queue.put(event_id)
    # YENİ VE GELİŞTİRİLMİŞ HALİ (Artık 3 parametre alıyor)
    def _trigger_log(self, temp_value, event_data_snapshot, is_event=5):
        trigger_start_time = time.time() # Yeni: Olay tetikleme başlangıç zamanı
        base_folder_name = "anomalies" if is_event == 5 else "normal_logs"
        # === YENİ: Tarihe Göre Organize Klasör Yapısı ===
        now = datetime.now()
        date_path = now.strftime("%Y" + os.sep + "%m" + os.sep + "%d")
        time_folder = now.strftime("%H-%M-%S")
        #event_folder_path = os.path.join(EVENTS_DIR, self.name, date_path, time_folder) eski sistem
        event_folder_path = os.path.join(EVENTS_DIR, base_folder_name, self.name, date_path, time_folder)
        os.makedirs(event_folder_path, exist_ok=True)
        #---------
        # if event_data_snapshot:
        #     now_time = time.time()
        #     json_time = event_data_snapshot.get('system_receive_time', now_time)
        #     delay_seconds = now_time - json_time
            
        #     # Bu bilgiyi, anomali durumunda loglara yazdıralım.
        #     if is_event == 5:
        #         logging.info(f"[{self.name}] Anomali verisi, olay anından {delay_seconds:.2f} saniye önce alınmış.")
            
        #     # Bu gecikme bilgisini, her zaman data.json'a da ekleyelim.
        #     event_data_snapshot['event_trigger_time'] = now_time
        #     event_data_snapshot['data_age_seconds'] = delay_seconds
        # # === YENİ KOD SONU ===
        # YENİ ve GENİŞLETİLMİŞ BÖLÜM
        # Adım 3: Gecikme ve zamanlama bilgilerini önceden hesapla ve sakla.
        timing_info = {}
        if event_data_snapshot:
            json_time = event_data_snapshot.get('system_receive_time', trigger_start_time)
            delay_seconds = trigger_start_time - json_time
            
            if is_event == 5:
                logging.info(f"[{self.name}] Anomali verisi, olay anından {delay_seconds:.2f} saniye önce alınmış.")
            
            # Zamanlama bilgilerini bir sözlükte toplayalım
            timing_info = {
                "system_receive_time": json_time,
                "zaman_veri_alis": json_time,
                "event_trigger_time": trigger_start_time,
                "zaman_olay_tetiklenme": trigger_start_time,
                "data_age_seconds": delay_seconds,
                "veri_yasi_saniye": delay_seconds
            }
            # zaman ss
        #---------

        # === YENİ: Koşullu Görüntü Kaydı ===
        # Sadece ANOMALİ (is_event=5) varsa resimleri kaydet.
        if is_event == 5:
            log_msg = f"ANOMALİ KAYDI: Sıcaklık: {temp_value:.1f}°C. Resimler kaydediliyor..."
            self.anomaly_logger.warning(f"[{self.name}] {log_msg}")
            
            #normal_frame = self._capture_frame_with_timeout(self.rtsp_normal_url)
            capture_start = time.time() # <-- ÖNCESİNE EKLE    
            # YENİ HAL
            # normal_frame = self._capture_frame_with_timeout(self.rtsp_normal_url) # Eski, yavaş yöntem
            normal_frame = self._capture_frame_http(self.normal_snapshot_url)      # Yeni, hızlı yöntem
            capture_end = time.time()   # <-- SONRASINA EKLE
            timing_info['capture_normal_duration_sn'] = capture_end - capture_start
            timing_info['sure_normal_goruntu_yakalama_sn'] = capture_end - capture_start
            # zaman ss
            if normal_frame is not None:
                try:
                    pt1, pt2 = (1017, 450), (1858, 1085)#(1008, 588), (1680, 928)
                    cv2.rectangle(normal_frame, pt1, pt2, (0, 255, 0), 3)
                    cv2.imwrite(os.path.join(event_folder_path, "normal.jpg"), normal_frame)
                except Exception as e:
                    logging.error(f"[{self.name}] Normal resim işlenirken hata: {e}")

            #thermal_frame = self._capture_frame_with_timeout(self.rtsp_thermal_url)
            capture_start = time.time() # <-- ÖNCESİNE EKLE           
            # YENİ HAL
            # thermal_frame = self._capture_frame_with_timeout(self.rtsp_thermal_url) # Eski, yavaş yöntem
            thermal_frame = self._capture_frame_http(self.thermal_snapshot_url)       # Yeni, hızlı yöntem
            capture_end = time.time()   # <-- SONRASINA EKLE
            timing_info['capture_thermal_duration_sn'] = capture_end - capture_start
            timing_info['sure_termal_goruntu_yakalama_sn'] = capture_end - capture_start
            # zaman ss

            if thermal_frame is not None:
                try:
                    # Güvenli snapshot verisi varsa, üzerine doğru sıcaklığı ve işaretçiyi çizelim.
                    if event_data_snapshot:
                        try:
                            therm_upload = event_data_snapshot.get('ThermometryUploadList', {}).get('ThermometryUpload', [{}])[0]
                            correct_temp = therm_upload.get('LinePolygonThermCfg', {}).get('MaxTemperature')
                            highest_point = therm_upload.get('HighestPoint')

                            if correct_temp is not None and highest_point:
                                h, w = thermal_frame.shape[:2]
                                px = int(w * highest_point.get('positionX', 0))
                                py = int(h * highest_point.get('positionY', 0))
                                
                                text_to_display = f"{correct_temp:.1f}C"
                                
                                # ===============================================================
                                # === GÖRSEL AYARLAR (Buradan kolayca değiştirebilirsiniz) ===
                                # ===============================================================
                                font_scale     = 1.9  # Yazının büyüklük ölçeği. Standart: 0.6
                                thickness      = 5    # Çizgilerin ve yazının kalınlığı. Standart: 2
                                crosshair_size = 20   # Artı (+) işaretinin merkezden kol uzunluğu (piksel). Standart: 10
                                # ===============================================================

                                # İşaretçi: Daha belirgin kırmızı bir artı (+) çiz
                                cv2.line(thermal_frame, (px - crosshair_size, py), (px + crosshair_size, py), (0, 0, 255), thickness)
                                cv2.line(thermal_frame, (px, py - crosshair_size), (px, py + crosshair_size), (0, 0, 255), thickness)
                                
                                # Metin: Daha büyük ve okunaklı metin için kutu ve yazı konumlarını hesapla
                                (text_w, text_h), _ = cv2.getTextSize(text_to_display, cv2.FONT_HERSHEY_SIMPLEX, font_scale, thickness)
                                box_start_point   = (px + crosshair_size + 5, py - text_h - 10)
                                box_end_point     = (px + crosshair_size + 15 + text_w, py)
                                text_start_point  = (px + crosshair_size + 10, py - 5)

                                # Önce beyaz arka plan kutusunu, sonra üzerine siyah metni çiz
                                cv2.rectangle(thermal_frame, box_start_point, box_end_point, (255, 255, 255), -1)
                                cv2.putText(thermal_frame, text_to_display, text_start_point, cv2.FONT_HERSHEY_SIMPLEX, font_scale, (0, 0, 0), thickness)

                        except (IndexError, KeyError, TypeError) as e:
                            logging.warning(f"[{self.name}] Termal resim üzerine çizim için veri ayrıştırılamadı: {e}")

                    # Her durumda (çizim yapılsın veya yapılamasın) 16:9 formatlama ve kaydetme işlemine devam et
                    target_w, target_h = 1710, 960
                    background = np.full((target_h, target_w, 3), 255, dtype=np.uint8)
                    th, tw, _ = thermal_frame.shape
                    x_offset = (target_w - tw) // 2
                    background[0:th, x_offset:x_offset + tw] = thermal_frame
                    cv2.imwrite(os.path.join(event_folder_path, "thermal.jpg"), background)
                except Exception as e:
                    logging.error(f"[{self.name}] Termal resim işlenirken veya kaydedilirken genel hata: {e}")
                
        # === GÜVENLİ VERİ KAYDI ===
        # JSON verisi HER DURUMDA kaydedilir.
        # Güvenli olan kopyalanmış veriyi (`event_data_snapshot`) kullanır.
        if event_data_snapshot:
             # Son zamanlama bilgisini ekle
            processing_end_time = time.time()
            timing_info['total_processing_duration_sn'] = processing_end_time - trigger_start_time
            timing_info['toplam_islem_suresi_sn'] = processing_end_time - trigger_start_time
            
            # Tüm zamanlama bilgilerini 'timing_analysis' adında yeni bir anahtar altına ekle
            event_data_snapshot['timing_analysis'] = timing_info
            # zaman ss
            with open(os.path.join(event_folder_path, "data.json"), 'w', encoding='utf-8') as f:
                json.dump(event_data_snapshot, f, ensure_ascii=False, indent=4)
        
        # --- Veritabanına Kayıt Kısmı (Yeni path ile güncellendi) ---
        priority_id, priorityName = None, "TANIMSIZ"
        priority_result = find_priority_id_from_temp(temp_value, self.priorities)
        if priority_result:
            priority_id, priorityName = priority_result
        elif is_event == 5:
            logging.warning(f"[{self.name}] Sıcaklık ({temp_value}°C) için öncelik aralığı bulunamadı.")
        
        usecase_id, usecase_name = find_usecase_info_by_camera_id(self.camera_id, self.usecases)
        
        # db_path = os.path.join(self.name, date_path, time_folder).replace(os.sep, '/')
        # #db_path = os.path.relpath(event_folder_path, os.path.dirname(EVENTS_DIR)).replace(os.sep, '/')
        # db_path = "termal/events/" + db_path 
        # YENİ ve STABİL HALİ
        # İstenen tam göreceli yolu tek bir adımda, güvenli bir şekilde oluşturuyoruz.
        # db_path = os.path.join("termal", os.path.basename(EVENTS_DIR), self.name, date_path, time_folder).replace(os.sep, '/')
        # YENİ SATIR
        # Tam göreceli yolu tek adımda oluştur: termal/events/anomalies/Kamera/Tarih/Zaman
        db_path = os.path.join("termal", os.path.basename(EVENTS_DIR), base_folder_name, self.name, date_path, time_folder).replace(os.sep, '/')
        event_timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
        event_timestamp_db = datetime.strptime(event_timestamp, "%Y-%m-%d %H:%M:%S").strftime("%Y-%m-%d_%H-%M-%S")
     
        event_id = save_event_to_db(
            self.camera_id, event_timestamp_db, temp_value, db_path,
            usecase_id=usecase_id, usecase_name=usecase_name,
            priority_id=priority_id, priority_name=priorityName, is_event=is_event
        )
        if event_id:
            self.ram_queue.put(event_id)

    def _trigger_log2(self, temp_value, event_data_snapshot, temp_folder_path, is_event=5):
        """
        BUFFER'DAN GELEN RESİMLERİ KULLANARAK KAYIT YAPAR
        (HTTP snapshot ÇEKİLMEZ, zaten buffer'da var!)
        """
        trigger_start_time = time.time()
        base_folder_name = "anomalies" if is_event == 5 else "normal_logs"
        
        # === KALICI KLASÖR OLUŞTUR ===
        now = datetime.now()
        date_path = now.strftime("%Y" + os.sep + "%m" + os.sep + "%d")
        time_folder = now.strftime("%H-%M-%S")
        event_folder_path = os.path.join(EVENTS_DIR, base_folder_name, self.name, date_path, time_folder)
        os.makedirs(event_folder_path, exist_ok=True)
        
        # === GEÇİCİ KLASÖRDEN RESİMLERİ TAŞI ===
        import shutil
        try:
            # Normal resim
            src_normal = os.path.join(temp_folder_path, "normal.jpg")
            if os.path.exists(src_normal):
                normal_frame = cv2.imread(src_normal)
                # ROI çiz
                pt1, pt2 = (1017, 450), (1858, 1085)
                cv2.rectangle(normal_frame, pt1, pt2, (0, 255, 0), 3)
                cv2.imwrite(os.path.join(event_folder_path, "normal.jpg"), normal_frame)
            
            # Termal resim
            src_thermal = os.path.join(temp_folder_path, "thermal.jpg")
            if os.path.exists(src_thermal):
                thermal_frame = cv2.imread(src_thermal)
                
                # Sıcaklık ve işaretçi çiz
                if event_data_snapshot:
                    try:
                        therm_upload = event_data_snapshot['ThermometryUploadList']['ThermometryUpload'][0]
                        correct_temp = therm_upload['LinePolygonThermCfg']['MaxTemperature']
                        highest_point = therm_upload['HighestPoint']
                        
                        if correct_temp and highest_point:
                            h, w = thermal_frame.shape[:2]
                            px = int(w * highest_point['positionX'])
                            py = int(h * highest_point['positionY'])
                            
                            # Artı işareti
                            cv2.line(thermal_frame, (px - 20, py), (px + 20, py), (0, 0, 255), 5)
                            cv2.line(thermal_frame, (px, py - 20), (px, py + 20), (0, 0, 255), 5)
                            
                            # Sıcaklık metni
                            text = f"{correct_temp:.1f}C"
                            (text_w, text_h), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 1.9, 5)
                            box_start = (px + 25, py - text_h - 10)
                            box_end = (px + 35 + text_w, py)
                            cv2.rectangle(thermal_frame, box_start, box_end, (255, 255, 255), -1)
                            cv2.putText(thermal_frame, text, (px + 30, py - 5), cv2.FONT_HERSHEY_SIMPLEX, 1.9, (0, 0, 0), 5)
                    except:
                        pass
                
                # 16:9 formatlama
                target_w, target_h = 1710, 960
                background = np.full((target_h, target_w, 3), 255, dtype=np.uint8)
                th, tw, _ = thermal_frame.shape
                x_offset = (target_w - tw) // 2
                background[0:th, x_offset:x_offset + tw] = thermal_frame
                cv2.imwrite(os.path.join(event_folder_path, "thermal.jpg"), background)
            
            # JSON
            src_json = os.path.join(temp_folder_path, "data.json")
            if os.path.exists(src_json):
                shutil.copy(src_json, os.path.join(event_folder_path, "data.json"))
        
        except Exception as e:
            logging.error(f"[{self.name}] Dosya taşıma hatası: {e}")
        
        # === VERİTABANINA KAYDET (_trigger_log ile aynı) ===
        priority_id, priorityName = None, "TANIMSIZ"
        priority_result = find_priority_id_from_temp(temp_value, self.priorities)
        if priority_result:
            priority_id, priorityName = priority_result
        elif is_event == 5:
            logging.warning(f"[{self.name}] Sıcaklık ({temp_value}°C) için öncelik aralığı bulunamadı.")

        usecase_id, usecase_name = find_usecase_info_by_camera_id(self.camera_id, self.usecases)
        
        
        
        db_path = os.path.join("termal", os.path.basename(EVENTS_DIR), base_folder_name, 
                            self.name, date_path, time_folder).replace(os.sep, '/')
        
        event_timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
        event_timestamp_db = datetime.strptime(event_timestamp, "%Y-%m-%d %H:%M:%S").strftime("%Y-%m-%d_%H-%M-%S")
        
        event_id = save_event_to_db(
            self.camera_id, event_timestamp_db, temp_value, db_path,
            usecase_id=usecase_id, usecase_name=usecase_name,
            priority_id=priority_id, priority_name=priorityName, is_event=is_event
        )
        
        if event_id:
            self.ram_queue.put(event_id)

    # def _check_for_anomaly(self):
    #     cooldown = self.config['alarm_settings']['cooldown_sec']
    #     if time.time() - self.last_alarm_time < cooldown:
    #         return

    #     with self.lock:
    #         max_temp = self.last_max_temp

    #     if max_temp is None:
    #         return
        
    #     threshold = self.config['alarm_settings']['threshold_c']
    #     print(f"[{self.name}] Maksimum sıcaklık: {max_temp:.1f}°C, Eşik: {threshold:.1f}°C")
    #     if max_temp > threshold:
    #         print(f"[{self.name}] Anomali tespit edildi! Sıcaklık: {max_temp:.1f}°C, Eşik: {threshold:.1f}°C")
    #         logging.warning(f"[{self.name}] Anomali tespit edildi! Sıcaklık: {max_temp:.1f}°C, Eşik: {threshold:.1f}°C")
    #         self.last_alarm_time = time.time()
    #         self._trigger_log(max_temp, is_event=5) # Anomali durumu için is_event=5
    #     else:
    #         self._trigger_log(max_temp, is_event=6) # Normal durum için is_event=6
    # YENİ VE GÜVENLİ HALİ
    # def _check_for_anomaly(self):
    #     cooldown = self.config['alarm_settings']['cooldown_sec']
    #     if time.time() - self.last_alarm_time < cooldown:
    #         return

    #     # === KRİTİK ADIM: ATOMİK KİLİTLEME VE SNAPSHOT ===
    #     # Bu 'with self.lock:' bloğu, bu kamera monitörüne ait kilidi alır.
    #     # Bu sırada, bu kameraya ait ThermalDataThread'in 'self.last_thermal_data' 
    #     # üzerine yazması anlık olarak engellenir. Bu, bölünemez bir okuma sağlar.
    #     with self.lock:
    #         max_temp = self.last_max_temp
    #         # O anki tüm JSON verisinin bir kopyasını (snapshot) alıyoruz.
    #         data_snapshot = self.last_thermal_data.copy() if self.last_thermal_data else None

    #     # Kilit bu noktada serbest bırakıldı. Diğer thread'ler normal işleyişine devam ediyor.
    #     # Bizim elimizde ise olayın "dondurulmuş", tutarlı bir kopyası var.

    #     if max_temp is None:
    #         return
        
    #     threshold = self.config['alarm_settings']['threshold_c']
        
    #     # Anomali kontrolünü, kopyaladığımız güvenli veri üzerinden yapıyoruz.
    #     if max_temp > threshold:
    #         logging.warning(f"[{self.name}] ANOMALİ TESPİT EDİLDİ: {max_temp:.1f}°C > Eşik: {threshold:.1f}°C")
    #         self.last_alarm_time = time.time()
    #         # Kayıt fonksiyonuna hem sıcaklığı hem de dondurulmuş veri kopyasını gönderiyoruz.
    #         self._trigger_log(max_temp, data_snapshot, is_event=5)
    #     else:
    #         # Normal durumda da o anın verisini (resimsiz) kaydetmek için gönderiyoruz.
    #         self._trigger_log(max_temp, data_snapshot, is_event=6)
      
    # def _check_for_anomaly(self):
    # # Yeni ve GELİŞTİRİLMİŞ HALİ 06.11.2025 trihli son aktif olan konum doğrulamalı içeren sürüm
    #     cooldown = self.config['alarm_settings'].get('cooldown_sec', 10)
    #     if time.time() - self.last_alarm_time < cooldown:
    #         return

    #     with self.lock:
    #         max_temp = self.last_max_temp
    #         data_snapshot = self.last_thermal_data.copy() if self.last_thermal_data else None

    #     if max_temp is None or data_snapshot is None:
    #         return
        
    #     ### 1. ADIM: ÖNCE ÖNCELİK TESPİTİ YAP ###
    #     priority_id, priorityName = find_priority_id_from_temp(max_temp, self.priorities)

    #     ### 2. ADIM: SENARYOYA GÖRE KARAR VER ###

    #     # =====================================================================
    #     # === SENARYO 1: ÖZEL KONTROL GEREKEN ID'LER - AKILLI DOĞRULAMA ===
    #     # =====================================================================
    #     positional_check_ids = self.config['alarm_settings'].get('positional_check_priority_ids', [9])

    #     if priority_id in positional_check_ids:
    #         self._activate_scan_pause()
    #         logging.info(f"[{self.name}] ÖZEL KONTROL SEVİYESİNDE ADAY TESPİT EDİLDİ (ID: {priority_id}, Temp: {max_temp:.1f}°C). Konumsal doğrulama başlıyor...")

    #         confirm_period = self.config['alarm_settings'].get('confirmation_period_sec', 8)
    #         confirm_size = self.config['alarm_settings'].get('confirmation_buffer_size', 3)
    #         pos_tolerance = self.config['alarm_settings'].get('positional_tolerance', 0.05)
            
    #         try:
    #             highest_point = data_snapshot['ThermometryUploadList']['ThermometryUpload'][0]['HighestPoint']
    #             if not highest_point: raise ValueError("HighestPoint verisi boş")
    #         except (KeyError, IndexError, ValueError) as e:
    #             logging.warning(f"[{self.name}] Özel aday ({max_temp:.1f}°C) için pozisyon verisi yok: {e}. Direkt alarm veriliyor.")
    #             self.last_alarm_time = time.time()
    #             self._trigger_log(max_temp, data_snapshot, is_event=5)
    #             return

    #         current_time = time.time()
    #         pos_x = highest_point.get('positionX', 0)
    #         pos_y = highest_point.get('positionY', 0)

    #         self.anomaly_candidate_buffer = [p for p in self.anomaly_candidate_buffer if current_time - p['time'] < confirm_period]
    #         self.anomaly_candidate_buffer.append({'time': current_time, 'temp': max_temp, 'pos': (pos_x, pos_y)})

    #         if len(self.anomaly_candidate_buffer) >= confirm_size:
    #             positions = [p['pos'] for p in self.anomaly_candidate_buffer]
    #             avg_pos_x = np.mean([p[0] for p in positions])
    #             avg_pos_y = np.mean([p[1] for p in positions])
    #             max_deviation = max(np.sqrt((p[0] - avg_pos_x)**2 + (p[1] - avg_pos_y)**2) for p in positions)

    #             if max_deviation <= pos_tolerance:
    #                 logging.warning(f"[{self.name}] ANOMALİ DOĞRULANDI! (ID: {priority_id}, Temp: {max_temp:.1f}°C). Konum sabit.")
    #                 self.last_alarm_time = time.time()
    #                 self._trigger_log(max_temp, data_snapshot, is_event=5)
    #                 self.anomaly_candidate_buffer.clear()
    #             else:
    #                 logging.warning(f"[{self.name}] HAREKETLİ NESNE TESPİT EDİLDİ (ID: {priority_id}, Temp: {max_temp:.1f}°C). Konum değişiyor. Alarm verilmedi.")
    #         return

    #     # =====================================================================
    #     # === SENARYO 2: YÜKSEK SICAKLIK (Diğer anomali ID'leri) - DİREKT ALARM ===
    #     # =====================================================================
    #     elif priority_id < 10: # 10 "Normal" ID'si varsayılarak
    #         logging.warning(f"[{self.name}] YÜKSEK SICAKLIK TESPİT EDİLDİ (ID: {priority_id}): {max_temp:.1f}°C. Direkt alarm oluşturuluyor.")
    #         self.last_alarm_time = time.time()
    #         self._trigger_log(max_temp, data_snapshot, is_event=5)
    #         self.anomaly_candidate_buffer.clear()
    #         return

    #     # =====================================================================
    #     # === SENARYO 3: NORMAL SICAKLIK (ID 10) - SADECE KAYDET ===
    #     # =====================================================================
    #     else:
    #         self._trigger_log(max_temp, data_snapshot, is_event=6)
    #         if self.anomaly_candidate_buffer:
    #             self.anomaly_candidate_buffer.clear()
    #         return
  
    def _check_for_anomaly(self):
        cooldown = self.config['alarm_settings'].get('cooldown_sec', 10)
        if time.time() - self.last_alarm_time < cooldown:
            return

        with self.lock:
            max_temp = self.last_max_temp
            data_snapshot = self.last_thermal_data.copy() if self.last_thermal_data else None

        if max_temp is None or data_snapshot is None:
            return
        
        priority_id, priorityName = find_priority_id_from_temp(max_temp, self.priorities)
        
        # === SENARYO 1: AKILLI FİLTRELEME (PRIORITY 9) ===
        if self.smart_filtering_enabled and priority_id in self.smart_priority_ids:
            self._handle_smart_filtering(max_temp, data_snapshot, priority_id, priorityName)
            return  # ÖNEMLİ: Fonksiyondan çık!
        
        # === SENARYO 2: YÜKSEK SICAKLIK (DİĞER PRİORİTYLER) ===
        elif priority_id < 10:
            logging.warning(f"[{self.name}] YÜKSEK SICAKLIK (ID: {priority_id}): {max_temp:.1f}°C")
            self.last_alarm_time = time.time()
            self._trigger_log(max_temp, data_snapshot, is_event=5)
            return
        
        # === SENARYO 3: NORMAL SICAKLIK ===
        else:
            self._trigger_log(max_temp, data_snapshot, is_event=6)
            return
    
    def _check_onvif_connection(self):
        if not self.onvif_cam: return self._connect_onvif()
        try:
            device_service = self.onvif_cam.create_devicemgmt_service()
            device_service.GetSystemDateAndTime()
            return True
        except Exception as e:
            logging.warning(f"[{self.name}] ONVIF bağlantı testi başarısız: {e}. Yeniden bağlanılıyor...")
            self.is_healthy = False
            return self._connect_onvif()

    # def run(self):
    #     try:
    #         if not self._connect_onvif():
    #             self.status_message = "ONVIF bağlantısı kurulamadı, tarama durdu."
    #             return
    #         scan_p = self.config['scan_parameters']
    #         start_deg = scan_p['pan_start_deg']
    #         end_deg = scan_p['pan_end_deg']
    #         step = scan_p['step_deg']

    #         # Durum 1: Tarama yok, kamera sabit pozisyonda durmalı.
    #         if start_deg == end_deg:
    #             self.status_message = f"Sabit pozisyon: {start_deg}°"
    #             logging.info(f"[{self.name}] Tarama devre dışı, sabit pozisyonda bekleniyor: {start_deg}°")
    #             self._go_to_degree(start_deg, scan_p['tilt_fixed_deg'])
    #             while self._run_flag:
    #                 self._check_for_anomaly()
    #                 time.sleep(5)
    #             return
    #         if start_deg < end_deg:  # Durum 2: Normal tarama (küçükten büyüğe).
    #             forward_path = list(np.arange(start_deg, end_deg + 1, step))
    #             backward_path = list(np.arange(end_deg, start_deg - 1, -step))
    #         else:  # Durum 3: 360/0 derecesini geçen tarama (büyükten küçüğe).
    #             path1 = list(np.arange(start_deg, 360, step))
    #             path2 = list(np.arange(0, end_deg + 1, step))
    #             forward_path = path1 + path2
                
    #             path1_rev = list(np.arange(end_deg, -1, -step))
    #             path2_rev = list(np.arange(359, start_deg - 1, -step))
    #             backward_path = path1_rev + path2_rev

    #         full_tour = forward_path + backward_path
    #         import itertools
    #         tour_iterator = itertools.cycle(full_tour)
    #         logging.info(f"[{self.name}] Tarama rotası oluşturuldu: {start_deg}° <-> {end_deg}°")
    #         while self._run_flag:
    #             if not self._check_onvif_connection():
    #                 time.sleep(10)
    #                 continue
    #             current_pan = next(tour_iterator)
    #             self._go_to_degree(current_pan, scan_p['tilt_fixed_deg'])
    #             self.status_message = f"Tarama: {current_pan}° pozisyonunda bekleniyor."
    #             wait_end_time = time.time() + scan_p['wait_sec']
    #             while time.time() < wait_end_time and self._run_flag:
    #                 self._check_for_anomaly()
    #                 time.sleep(1)
    #     except Exception as e:
    #         logging.critical(f"[{self.name}] TARAMA THREAD'İNDE KRİTİK HATA: {e}", exc_info=True)
    #         if self._run_flag:
    #             time.sleep(30)
    #             self.run()
    #     finally:
    #         if not self._run_flag: logging.info(f"[{self.name}] Tarama döngüsü durduruldu.")
    # CameraMonitor sınıfının içine bu yeni metodu ekle

    def _should_scan_continue(self):
        """Merkezi API'yi sorgulayarak taramanın devam edip etmeyeceğini kontrol eder."""
        scan_key = self.config.get("scan_parameters", {}).get("scan_control_key")
        
        # Eğer bu kamera için bir kontrol anahtarı tanımlanmamışsa, her zaman devam et.
        if not scan_key:
            return True

        api_url = f"{BASE_URL}/GetOneByApiKey"
        headers = {"API-KEY": API_KEY}
        payload = {"InnerValue": scan_key}
        
        try:
            # API'ye POST isteği gönder (çünkü bir 'payload' gönderiyoruz)
            response = requests.get(api_url, headers=headers, json=payload, verify=False, timeout=5)
            response.raise_for_status() # HTTP 4xx veya 5xx hatası varsa exception fırlat
            
            data = response.json()
            
            # API'den gelen yanıttaki 'value' değerini kontrol et
            if data.get("success"):
                scan_status_str = data.get("data", {}).get("value", "true").lower()
                if scan_status_str == "false":
                    # EĞER 'false' GELDİYSE, TARAMA YAPMA
                    self.status_message = "Tarama merkezi sistem tarafından duraklatıldı."
                    logging.warning(f"[{self.name}] Tarama, API'den gelen 'false' durumu nedeniyle duraklatıldı.")
                    return False
            
            # Diğer tüm durumlarda (başarılı ve 'true' ise, veya bir hata oluşursa) taramaya devam et
            return True

        except requests.exceptions.RequestException as e:
            logging.error(f"[{self.name}] Tarama durumu API'si sorgulanırken hata: {e}. Tarama devam edecek.")
            # API'ye ulaşılamazsa sistemin kilitlenmemesi için taramaya devam et
            return True

    # def run(self):
    #     try:
    #         if not self._connect_onvif():
    #             self.status_message = "ONVIF bağlantısı kurulamadı, tarama durdu."
    #             return

    #         scan_p = self.config['scan_parameters']
    #         pan_start = scan_p['pan_start_deg']
    #         pan_end = scan_p['pan_end_deg']
    #         pan_step = scan_p.get('step_deg', 10)
    #         wait_sec = scan_p.get('wait_sec', 5)

    #         # Durum 1: Tarama yok (sabit pozisyon).
    #         if pan_start == pan_end:
    #             tilt_fixed = scan_p.get('tilt_fixed_deg', 0)
    #             self.status_message = f"Sabit pozisyon: Pan={pan_start}°, Tilt={tilt_fixed}°"
    #             logging.info(f"[{self.name}] Tarama devre dışı, sabit pozisyonda bekleniyor: {self.status_message}")
    #             self._go_to_degree(pan_start, tilt_fixed)
    #             while self._run_flag:
    #                 self._check_for_anomaly()
    #                 time.sleep(5)
    #             return

    #         # ---- YENİ ROTA OLUŞTURMA MANTIĞI BAŞLANGICI ----

    #         # Yatay (Pan) rotalarını oluştur (0/360 derece geçişini hesaba katarak)
    #         if pan_start < pan_end:
    #             forward_pan_path = list(np.arange(pan_start, pan_end + 1, pan_step))
    #             backward_pan_path = list(np.arange(pan_end, pan_start - 1, -pan_step))
    #         else:  # 360/0 derecesini geçen tarama
    #             path1 = list(np.arange(pan_start, 360, pan_step))
    #             path2 = list(np.arange(0, pan_end + 1, pan_step))
    #             forward_pan_path = path1 + path2
                
    #             path1_rev = list(np.arange(pan_end, -1, -pan_step))
    #             path2_rev = list(np.arange(359, pan_start - 1, -pan_step))
    #             backward_pan_path = path1_rev + path2_rev

    #         full_tour_points = []
            
    #         # Kontrol: 2D S-Tarama mı yoksa 1D Yatay Tarama mı yapılacak?
    #         # Yeni Y ekseni parametreleri (pan_start_Ydeg vb.) varsa S-Tarama yapılır.
    #         if 'pan_start_Ydeg' in scan_p and 'pan_end_Ydeg' in scan_p and 'step_Ydeg' in scan_p:
    #             logging.info(f"[{self.name}] 2D 'S' Taraması başlatılıyor.")
    #             tilt_start = scan_p['pan_start_Ydeg']
    #             tilt_end = scan_p['pan_end_Ydeg']
    #             tilt_step = scan_p.get('step_Ydeg', 10)
                
    #             # Dikey (Tilt) adımlarını oluştur
    #             tilt_levels = list(np.arange(tilt_start, tilt_end + 1, tilt_step))
                
    #             # "S" rotasını oluştur: Her dikey seviyede yatay yönü değiştir
    #             for i, tilt_deg in enumerate(tilt_levels):
    #                 current_pan_path = forward_pan_path if i % 2 == 0 else backward_pan_path
    #                 for pan_deg in current_pan_path:
    #                     full_tour_points.append((pan_deg, tilt_deg))
    #             logging.info(f"[{self.name}] S-Tarama rotası oluşturuldu: {len(full_tour_points)} nokta.")

    #         # Eski sistemle uyumluluk: Y ekseni parametreleri yoksa sadece yatay tarama yapılır.
    #         else:
    #             logging.info(f"[{self.name}] 1D Yatay Tarama başlatılıyor (sabit dikey açı).")
    #             tilt_fixed = scan_p.get('tilt_fixed_deg', 0)
    #             full_pan_tour = forward_pan_path + backward_pan_path
    #             for pan_deg in full_pan_tour:
    #                 full_tour_points.append((pan_deg, tilt_fixed))
    #             logging.info(f"[{self.name}] Yatay tarama rotası oluşturuldu: {len(full_tour_points)} nokta.")
            
    #         if not full_tour_points:
    #             logging.error(f"[{self.name}] Tarama rotası oluşturulamadı. Lütfen konfigürasyonu kontrol edin.")
    #             return

    #         # ---- YENİ ROTA OLUŞTURMA MANTIĞI SONU ----

    #         import itertools
    #         tour_iterator = itertools.cycle(full_tour_points)

    #         while self._run_flag:
    #             # --- MERKEZİ TARAMA KONTROLÜ ---
    #             if not self._should_scan_continue():
    #                 # Eğer API "dur" dediyse, 15 saniye bekle ve döngünün başına dön
    #                 time.sleep(15)
    #                 continue
    #             # --- BİTİŞ ---
    #             if not self._check_onvif_connection():
    #                 time.sleep(10)
    #                 continue
                
    #             # Rotadaki bir sonraki (pan, tilt) noktasına git
    #             current_pan, current_tilt = next(tour_iterator)
    #             self._go_to_degree(current_pan, current_tilt)
    #             # Durum mesajı güncellendi: Hem pan hem tilt gösteriliyor
    #             self.status_message = f"Tarama: Pan={current_pan}°, Tilt={current_tilt}° pozisyonunda bekleniyor."
    #             #----
    #             self.status_message = f"Tarama: Pan={current_pan}°, Tilt={current_tilt}° pozisyonunda."
    #             # === YENİ KOD BAŞLANGICI: HAREKET SONRASI BEKLEME ===
    #             # 1. Yapılandırmadan "yerleşme süresini" oku.
    #             post_move_delay = scan_p.get("post_move_delay_sec", 1.0) # Varsayılan 1 sn

    #             # 2. Kameranın yeni sahneyi analiz etmesi için bekle.
    #             logging.info(f"[{self.name}] Hareket sonrası {post_move_delay} sn yerleşme süresi bekleniyor.")
    #             time.sleep(post_move_delay)

    #             # 3. (ÖNEMLİ) Eski pozisyondan kalmış olabilecek son veriyi temizle.
    #             #    Bu, ilk kontrolün kesinlikle yeni pozisyondan gelen taze veri ile yapılmasını garanti eder.
    #             with self.lock:
    #                 self.last_max_temp = None
    #             #----
    #             wait_end_time = time.time() + wait_sec
    #             while time.time() < wait_end_time and self._run_flag:
    #                 self._check_for_anomaly()
    #                 time.sleep(1)

    #     except Exception as e:
    #         logging.critical(f"[{self.name}] TARAMA THREAD'İNDE KRİTİK HATA: {e}", exc_info=True)
    #         if self._run_flag:
    #             time.sleep(30)
    #             self.run()
    #     finally:
    #         if not self._run_flag: logging.info(f"[{self.name}] Tarama döngüsü durduruldu.")

    # def stop(self):
    #     self._run_flag = False
    # def run(self):
    #     try:
    #         if not self._connect_onvif():
    #             self.status_message = "ONVIF bağlantısı kurulamadı, tarama durdu."
    #             return

    #         scan_p = self.config['scan_parameters']
    #         pan_start = scan_p['pan_start_deg']
    #         pan_end = scan_p['pan_end_deg']
    #         pan_step = scan_p.get('step_deg', 10)
    #         wait_sec = scan_p.get('wait_sec', 5)

    #         # Durum 1: Tarama yok (sabit pozisyon). Bu bölümde değişiklik yok.
    #         if pan_start == pan_end:
    #             tilt_fixed = scan_p.get('tilt_fixed_deg', 0)
    #             self.status_message = f"Sabit pozisyon: Pan={pan_start}°, Tilt={tilt_fixed}°"
    #             logging.info(f"[{self.name}] Tarama devre dışı, sabit pozisyonda bekleniyor: {self.status_message}")
    #             self._go_to_degree(pan_start, tilt_fixed)
    #             while self._run_flag:
    #                 self._check_for_anomaly()
    #                 time.sleep(5)
    #             return

    #         # ---- ROTA OLUŞTURMA MANTIĞINIZ (DEĞİŞTİRİLMEDİ) ----
    #         # Yatay (Pan) rotalarını oluştur (0/360 derece geçişini hesaba katarak)
    #         if pan_start < pan_end:
    #             forward_pan_path = list(np.arange(pan_start, pan_end + 1, pan_step))
    #             backward_pan_path = list(np.arange(pan_end, pan_start - 1, -pan_step))
    #         else:  # 360/0 derecesini geçen tarama
    #             path1 = list(np.arange(pan_start, 360, pan_step))
    #             # ### KRİTİK HATA DÜZELTMESİ ###
    #             # 'end_deg' değişkeni 'pan_end' olarak düzeltildi.
    #             path2 = list(np.arange(0, pan_end + 1, pan_step))
    #             forward_pan_path = path1 + path2
                
    #             path1_rev = list(np.arange(pan_end, -1, -pan_step))
    #             path2_rev = list(np.arange(359, pan_start - 1, -pan_step))
    #             backward_pan_path = path1_rev + path2_rev

    #         full_tour_points = []
            
    #         # Kontrol: 2D S-Tarama mı yoksa 1D Yatay Tarama mı yapılacak?
    #         if 'pan_start_Ydeg' in scan_p and 'pan_end_Ydeg' in scan_p and 'step_Ydeg' in scan_p:
    #             logging.info(f"[{self.name}] 2D 'S' Taraması başlatılıyor.")
    #             tilt_start = scan_p['pan_start_Ydeg']
    #             tilt_end = scan_p['pan_end_Ydeg']
    #             tilt_step = scan_p.get('step_Ydeg', 10)
    #             tilt_levels = list(np.arange(tilt_start, tilt_end + 1, tilt_step))
    #             for i, tilt_deg in enumerate(tilt_levels):
    #                 current_pan_path = forward_pan_path if i % 2 == 0 else backward_pan_path
    #                 for pan_deg in current_pan_path:
    #                     full_tour_points.append((pan_deg, tilt_deg))
    #             logging.info(f"[{self.name}] S-Tarama rotası oluşturuldu: {len(full_tour_points)} nokta.")
    #         else:
    #             logging.info(f"[{self.name}] 1D Yatay Tarama başlatılıyor (sabit dikey açı).")
    #             tilt_fixed = scan_p.get('tilt_fixed_deg', 0)
    #             full_pan_tour = forward_pan_path + backward_pan_path
    #             for pan_deg in full_pan_tour:
    #                 full_tour_points.append((pan_deg, tilt_fixed))
    #             logging.info(f"[{self.name}] Yatay tarama rotası oluşturuldu: {len(full_tour_points)} nokta.")
            
    #         if not full_tour_points:
    #             logging.error(f"[{self.name}] Tarama rotası oluşturulamadı. Lütfen konfigürasyonu kontrol edin.")
    #             return

    #         import itertools
    #         tour_iterator = itertools.cycle(full_tour_points)

    #         # ### ANA DEĞİŞİKLİK: YENİ KONTROL MANTIĞI ###
    #         while self._run_flag:
    #             # 1. Bağlantı kontrolü. Bu, döngünün en başında kalmalı.
    #             if not self._check_onvif_connection():
    #                 time.sleep(10)
    #                 continue

    #             # 2. Tarama devam etmeli mi?
    #             if self._should_scan_continue():
    #                 # TARAMA AKTİF: Hareket et ve hazırlan.
    #                 current_pan, current_tilt = next(tour_iterator)
    #                 self._go_to_degree(current_pan, current_tilt)
    #                 self.status_message = f"Tarama: Pan={current_pan}°, Tilt={current_tilt}° pozisyonunda."
                    
    #                 post_move_delay = scan_p.get("post_move_delay_sec", 1.0)
    #                 time.sleep(post_move_delay)
                    
    #                 with self.lock:
    #                     self.last_max_temp = None
    #             else:
    #                 # TARAMA DURAKLATILDI: Hareket etme, sadece bekle.
    #                 self.status_message = "Tarama merkezi sistem tarafından duraklatıldı."
    #                 time.sleep(2) # CPU'yu yormamak için kısa bir bekleme.

    #             # ### ORTAK BÖLÜM: ANOMALİ KONTROLÜ ###
    #             # Tarama aktif de olsa, duraklatılmış da olsa bu bölüm HER ZAMAN çalışır.
    #             wait_end_time = time.time() + wait_sec
    #             while time.time() < wait_end_time and self._run_flag:
    #                 self._check_for_anomaly()
    #                 time.sleep(1)

    #     except Exception as e:
    #         logging.critical(f"[{self.name}] TARAMA THREAD'İNDE KRİTİK HATA: {e}", exc_info=True)
    #         if self._run_flag:
    #             time.sleep(30)
    #             self.run()
    #     finally:
    #         if not self._run_flag: logging.info(f"[{self.name}] Tarama döngüsü durduruldu.")
    # def run(self): son çalışan buydu
    #     try:
    #         if not self._connect_onvif():
    #             self.status_message = "ONVIF bağlantısı kurulamadı, tarama durdu."
    #             # Not: Bağlantı başlangıçta yoksa, döngü içinde yeniden denenir.
                
    #         scan_p = self.config['scan_parameters']
    #         pan_start = scan_p['pan_start_deg']
    #         pan_end = scan_p['pan_end_deg']

    #         # Durum 1: Sabit pozisyon (Tarama tamamen devre dışı)
    #         if pan_start == pan_end:
    #             tilt_fixed = scan_p.get('tilt_fixed_deg', 0)
    #             self.status_message = f"Sabit pozisyon: Pan={pan_start}°, Tilt={tilt_fixed}°"
    #             logging.info(f"[{self.name}] Tarama devre dışı, sabit pozisyonda bekleniyor.")
    #             self._go_to_degree(pan_start, tilt_fixed)
    #             while self._run_flag:
    #                 self._check_for_anomaly()
    #                 time.sleep(1)
    #             return

    #         # Durum 2: Dinamik Tarama (Rota Oluşturma - Orijinal kodunuzla aynı)
    #         pan_step = scan_p.get('step_deg', 10)
    #         if pan_start < pan_end:
    #             forward_pan_path = list(np.arange(pan_start, pan_end + 1, pan_step))
    #             backward_pan_path = list(np.arange(pan_end, pan_start - 1, -pan_step))
    #         else:
    #             path1 = list(np.arange(pan_start, 360, pan_step)); path2 = list(np.arange(0, pan_end + 1, pan_step))
    #             forward_pan_path = path1 + path2
    #             path1_rev = list(np.arange(pan_end, -1, -pan_step)); path2_rev = list(np.arange(359, pan_start - 1, -pan_step))
    #             backward_pan_path = path1_rev + path2_rev

    #         full_tour_points = []
    #         if 'pan_start_Ydeg' in scan_p and 'pan_end_Ydeg' in scan_p and 'step_Ydeg' in scan_p:
    #             tilt_start, tilt_end, tilt_step = scan_p['pan_start_Ydeg'], scan_p['pan_end_Ydeg'], scan_p.get('step_Ydeg', 10)
    #             tilt_levels = list(np.arange(tilt_start, tilt_end + 1, tilt_step))
    #             for i, tilt_deg in enumerate(tilt_levels):
    #                 current_pan_path = forward_pan_path if i % 2 == 0 else backward_pan_path
    #                 for pan_deg in current_pan_path: full_tour_points.append((pan_deg, tilt_deg))
    #         else:
    #             tilt_fixed = scan_p.get('tilt_fixed_deg', 0)
    #             for pan_deg in forward_pan_path + backward_pan_path: full_tour_points.append((pan_deg, tilt_fixed))
            
    #         if not full_tour_points:
    #             logging.error(f"[{self.name}] Tarama rotası oluşturulamadı."); return

    #         import itertools
    #         tour_iterator = itertools.cycle(full_tour_points)

    #         # === ANA KONTROL DÖNGÜSÜ (ORİJİNAL YAPI KORUNDU) ===
    #         while self._run_flag:
    #             # Orijinal kodunuzdaki gibi, döngü başında bağlantı kontrolü yapılır.
    #             if not self._check_onvif_connection():
    #                 time.sleep(10)
    #                 continue
                
    #             if self.scan_paused:
    #                 if time.time() > self.scan_pause_end_time:
    #                     logging.info(f"[{self.name}] Tarama duraklatma süresi doldu. Otomatik taramaya devam ediliyor.")
    #                     self.scan_paused = False
    #                 else:
    #                     # Duraklatma aktifken, hareket etme, sadece anomali kontrolü yap.
    #                     self._check_for_anomaly()
    #                     time.sleep(1) # CPU'yu yormamak için kısa bekleme
    #                     continue # Döngünün başına dönerek aşağıdaki hareket komutlarını atla
                
    #             # --- MOD AYRIMI NOKTASI ---
    #             if not self._should_scan_continue():
    #                 # --- MOD: MANUEL GÖZLEM (API'den 'false' geldi) ---
    #                 self.status_message = "Tarama merkezi sistem tarafından duraklatıldı. Sürekli gözlem yapılıyor..."
    #                 logging.warning(f"[{self.name}] Manuel Gözlem Modu Aktif. API'den 'devam' komutu bekleniyor.")
                    
    #                 # API'den 'true' komutu gelene kadar bu özel döngüde kal.
    #                 while self._run_flag and not self._should_scan_continue():
    #                     # Sadece anomali kontrolü yap, başka hiçbir bekleme yok.
    #                     self._check_for_anomaly()
    #                     time.sleep(1) # CPU'yu yormamak için zorunlu kısa bekleme.
                    
    #                 logging.info(f"[{self.name}] Otomatik Tarama Moduna geri dönülüyor...")
    #                 continue # Döngünün başına dönerek taramaya başla.

    #             # --- MOD: OTOMATİK TARAMA (API'den 'true' geldi veya kontrol yok) ---
    #             # BU BÖLÜM SİZİN ORİJİNAL KODUNUZUN BİREBİR AYNISIDIR.
    #             current_pan, current_tilt = next(tour_iterator)
    #             self._go_to_degree(current_pan, current_tilt)
    #             self.status_message = f"Tarama: Pan={current_pan}°, Tilt={current_tilt}°"
                
    #             # Hareket sonrası veri bütünlüğü için ZORUNLU bekleme.
    #             post_move_delay = scan_p.get("post_move_delay_sec", 1.0)
    #             time.sleep(post_move_delay)

    #             # Eski pozisyondan kalan veriyi temizle.
    #             with self.lock:
    #                 self.last_max_temp = None
                
    #             # Yeni pozisyonda gözlem yap.
    #             wait_sec = scan_p.get('wait_sec', 3)
    #             wait_end_time = time.time() + wait_sec
    #             while time.time() < wait_end_time and self._run_flag:
    #                 self._check_for_anomaly()
    #                 time.sleep(1)

    #     except Exception as e:
    #         logging.critical(f"[{self.name}] TARAMA THREAD'İNDE KRİTİK HATA: {e}", exc_info=True)
    #         if self._run_flag:
    #             time.sleep(30)
    #             self.run()
    #     finally:
    #         if not self._run_flag:
    #             logging.info(f"[{self.name}] Tarama döngüsü durduruldu.")

    def run(self):
        try:
            if not self._connect_onvif():
                self.status_message = "ONVIF bağlantısı kurulamadı, tarama durdu."
            
            # --- Rota Oluşturma (Bu bölümde değişiklik yok) ---
            scan_p = self.config['scan_parameters']
            pan_start, pan_end = scan_p['pan_start_deg'], scan_p['pan_end_deg']

            if pan_start == pan_end: # Sabit pozisyon senaryosu
                tilt_fixed = scan_p.get('tilt_fixed_deg', 0)
                self.status_message = f"Sabit pozisyon: Pan={pan_start}°, Tilt={tilt_fixed}°"
                self._go_to_degree(pan_start, tilt_fixed)
                while self._run_flag:
                    self._check_for_anomaly() # Sabitken bile sürekli analiz et
                    time.sleep(1)
                return

            pan_step = scan_p.get('step_deg', 10)
            if pan_start < pan_end:
                forward_pan_path = list(np.arange(pan_start, pan_end + 1, pan_step))
                backward_pan_path = list(np.arange(pan_end, pan_start - 1, -pan_step))
            else:
                path1 = list(np.arange(pan_start, 360, pan_step)); path2 = list(np.arange(0, pan_end + 1, pan_step))
                forward_pan_path = path1 + path2
                path1_rev = list(np.arange(pan_end, -1, -pan_step)); path2_rev = list(np.arange(359, pan_start - 1, -pan_step))
                backward_pan_path = path1_rev + path2_rev

            full_tour_points = []
            if 'pan_start_Ydeg' in scan_p and 'pan_end_Ydeg' in scan_p and 'step_Ydeg' in scan_p:
                tilt_start, tilt_end, tilt_step = scan_p['pan_start_Ydeg'], scan_p['pan_end_Ydeg'], scan_p.get('step_Ydeg', 10)
                tilt_levels = list(np.arange(tilt_start, tilt_end + 1, tilt_step))
                for i, tilt_deg in enumerate(tilt_levels):
                    current_pan_path = forward_pan_path if i % 2 == 0 else backward_pan_path
                    for pan_deg in current_pan_path: full_tour_points.append((pan_deg, tilt_deg))
            else:
                tilt_fixed = scan_p.get('tilt_fixed_deg', 0)
                for pan_deg in forward_pan_path + backward_pan_path: full_tour_points.append((pan_deg, tilt_fixed))
            
            if not full_tour_points:
                logging.error(f"[{self.name}] Tarama rotası oluşturulamadı."); return

            import itertools
            tour_iterator = itertools.cycle(full_tour_points)

            # === YENİ VE GELİŞTİRİLMİŞ ANA KONTROL DÖNGÜSÜ ===
            wait_sec = scan_p.get('wait_sec', 3)
            last_move_time = 0

            while self._run_flag:
                if not self._check_onvif_connection():
                    time.sleep(10)
                    continue

                # --- HAREKET KARAR BLOĞU ---
                # Bu döngüde hareket etmeli miyiz?
                should_move_now = False

                # 1. Akıllı Odaklanma (scan_paused) bitti mi?
                if self.scan_paused and time.time() > self.scan_pause_end_time:
                    logging.info(f"[{self.name}] Akıllı odaklanma süresi doldu. Otomatik taramaya dönülüyor.")
                    self.scan_paused = False

                # 2. API ve Akıllı Odaklanma'ya göre hareket iznini kontrol et
                api_allows_scan = self._should_scan_continue()
                if api_allows_scan and not self.scan_paused:
                    # Hem API izin veriyor hem de akıllı odaklanma yoksa, bekleme süresi doldu mu diye bak
                    if time.time() - last_move_time > wait_sec:
                        should_move_now = True
                
                # --- HAREKET UYGULAMA BLOĞU ---
                if should_move_now:
                    current_pan, current_tilt = next(tour_iterator)
                    self._go_to_degree(current_pan, current_tilt)
                    self.status_message = f"Tarama: Pan={current_pan}°, Tilt={current_tilt}°"
                    
                    post_move_delay = scan_p.get("post_move_delay_sec", 1.0)
                    time.sleep(post_move_delay)
                    
                    with self.lock: # Eski pozisyondan kalan veriyi temizle
                        self.last_max_temp = None
                    
                    last_move_time = time.time() # Son hareket zamanını güncelle
                
                # --- ORTAK ANALİZ BLOĞU ---
                # Ne olursa olsun (hareket etsin veya etmesin), bu blok HER ZAMAN çalışır.
                self._check_for_anomaly()
                time.sleep(1) # Ana döngünün CPU'yu yormaması için genel bekleme

        except Exception as e:
            logging.critical(f"[{self.name}] TARAMA THREAD'İNDE KRİTİK HATA: {e}", exc_info=True)
            if self._run_flag:
                time.sleep(30)
                self.run()
        finally:
            if not self._run_flag:
                logging.info(f"[{self.name}] Tarama döngüsü durduruldu.")
                
    def stop(self):
        self._run_flag = False

class SendEventThread(threading.Thread):

    # ─────────────── SQL Yardımcı Metotlar ──────────────

    @staticmethod
    def sql_select_pending_events():
        return """
            SELECT * FROM events
             WHERE (api_sent = 0 OR api_sent = 2)
               AND retry_count < ?
             ORDER BY priority_id ASC, id DESC
             LIMIT ?
        """

    @staticmethod
    def sql_update_api_error():
        return """
            UPDATE events
               SET retry_count = retry_count + 1,
                   last_try_ts = ?,
                   api_sent = 2
             WHERE id = ?
        """

    @staticmethod
    def sql_update_success():
        return """
            UPDATE events
               SET api_sent = 1,
                   api_event_id = ?,
                   file_sent = ?,
                   retry_count = retry_count + 1,
                   last_try_ts = ?
             WHERE id = ?
        """

    # ───────────── Thread Başlatıcı ────────────

    def __init__(self, db_path, api_base_url, api_key,
                 ram_queue: Queue,
                 poll_interval=1,
                 backlog_scan_interval=10,
                 max_retries=3):
        super().__init__(name=f"SendEvent Thread")
        self.db_path = db_path
        self.api_base_url = api_base_url
        self.api_key = api_key
        self.poll_interval = poll_interval
        self.backlog_scan_interval = backlog_scan_interval
        self.max_retries = max_retries
        self.ram_queue = ram_queue
        self._stop_event = threading.Event()
        self._last_backlog_check = time.time()

        self.session = requests.Session()
        self.session.headers.update({"API-KEY": self.api_key})

    def stop(self):
        self._stop_event.set()

    def run(self):
        while not self._stop_event.is_set():
            try:
                # 1. RAM KUYRUĞU: Anlık event_id'leri kontrol et
                try:
                    event_id = self.ram_queue.get(timeout=self.poll_interval)
                    self.process_event_by_id(event_id)
                except Empty:
                    pass

                # 2. BACKLOG: SQLite tarama zamanı geldiyse
                if time.time() - self._last_backlog_check >= self.backlog_scan_interval:
                    self._last_backlog_check = time.time()
                    with sqlite3.connect(self.db_path) as conn:
                        conn.row_factory = sqlite3.Row
                        cursor = conn.cursor()

                        cursor.execute(
                            self.sql_select_pending_events(),
                            (self.max_retries, 5)
                        )
                        events = cursor.fetchall()
                        for event in events:
                            self.process_event(event)

            except Exception as loop_error:
                print(f"[DISPATCHER ERROR] {loop_error}")

        logging.info("SendEvent API thread durduruldu.")
    def process_event_by_id(self, event_id):
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM events WHERE id = ?", (event_id,))
            row = cursor.fetchone()
            if row:
                self.process_event(row)

    def process_event(self, event):
        event_id = event["id"]
        camera_id = event["camera_id"]
        folder_path = event["folder_path"]
        usecase_id = event["usecase_id"]
        priority_id = event["priority_id"]
        event_type = event["is_event"]  # 5: Anomali, 6: Normal
        temparature= event["temperature"]
        now_iso = datetime.utcnow().isoformat(timespec='seconds')


        timestamp=event["timestamp"]
        # # C# DateTime formatına uygun dönüştürme (.NET Core MVC Web API için)
        api_create_time = datetime.strptime(timestamp, "%Y-%m-%d_%H-%M-%S").strftime("%Y-%m-%d %H:%M:%S")
        # print(f"--->>api_create_time: {api_create_time}")


        # 1. AddEvent API
        try:
            # r = self.session.get(
            #     f"{self.api_base_url}/AddEvent",
            #     params={
            #         "usecaseId": usecase_id,
            #         "priorityId": priority_id,
            #         "cameraId": camera_id,
            #         "duration": int(temparature * 1000),
            #         "idle": 0,
            #         "statusId": 1,
            #         "eventTypeId": event_type,
            #         "create": api_create_time,
            #     },
            #     timeout=(3, 10),
            #     verify=False  # Sertifika doğrulamasını devre dışı bırak    
            # )
             # ===================================================================
            # === DEĞİŞİKLİK BURADA BAŞLIYOR ===
            # ===================================================================
            # Adım 1: Gönderilecek parametreleri bir değişkende topla
            api_params = {
                "usecaseId": usecase_id,
                "priorityId": priority_id,
                "cameraId": camera_id,
                "duration": int(temparature * 1000),
                "idle": 0,
                "statusId": 1,
                "eventTypeId": event_type,
                "create": api_create_time,
            }

            # Adım 2: Bu parametreleri API'ye göndermeden önce ekrana yazdır
            print(f"--- [API'YE GÖNDERİLECEK VERİ] (Event ID: {event_id}) ---")
            pprint.pprint(api_params)
            print("-----------------------------------------------------")

            # Adım 3: API isteğini yaparken yukarıdaki değişkeni kullan
            r = self.session.get(
                f"{self.api_base_url}/AddEvent",
                params=api_params,
                timeout=(3, 10),
                verify=False  # Sertifika doğrulamasını devre dışı bırak    
            )
            # ===================================================================
            # === DEĞİŞİKLİK BURADA BİTİYOR ===
            # ===================================================================
            r.raise_for_status()
            result = r.json()
            if result.get("success"):
                api_event_id = result["data"]["innerValue"]
                print(f"[SEND] Event {event_id} başarıyla gönderildi (API ID: {api_event_id})")
            else:
                raise Exception("Başarısız yanıt: AddEvent")
        except Exception as e:
            print(f"[ERROR] Event {event_id} gönderilemedi: {e}")
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(self.sql_update_api_error(), (now_iso, event_id))
                conn.commit()
            return

        # 2. AddEventFile API
        try:
            image_url = f"{folder_path}/normal.jpg"
            r2 = self.session.get(
                f"{self.api_base_url}/AddEventFile",
                params={
                    "eventId": api_event_id,
                    "url": image_url,
                    "fileTypeId": 1
                },
                timeout=(3, 10),
                verify=False  # Sertifika doğrulamasını devre dışı bırak
            )
            r2.raise_for_status()
            result2 = r2.json()
            if not result2.get("success"):
                raise Exception("Görsel gönderimi başarısız")
            file_sent_status = 1
            print(f"[SEND] Görsel gönderildi → Event {event_id}")
        except Exception as e:
            print(f"[ERROR] Görsel gönderilemedi: {e}")
            file_sent_status = 2
        
        try:
            image_url = f"{folder_path}/thermal.jpg"
            r2 = self.session.get(
                f"{self.api_base_url}/AddEventFile",
                params={
                    "eventId": api_event_id,
                    "url": image_url,
                    "fileTypeId": 1
                },
                timeout=(3, 10),
                verify=False  # Sertifika doğrulamasını devre dışı bırak
            )
            r2.raise_for_status()
            result2 = r2.json()
            if not result2.get("success"):
                raise Exception("Termal Görsel gönderimi başarısız")
            file_sent_status = 1
            print(f"[SEND] Termal Görsel gönderildi → Event {event_id}")
        except Exception as e:
            print(f"[ERROR] Termal Görsel gönderilemedi: {e}")
            file_sent_status = 2
        
        try:
            image_url = f"{folder_path}/data.json"
            r2 = self.session.get(
                f"{self.api_base_url}/AddEventFile",
                params={
                    "eventId": api_event_id,
                    "url": image_url,
                    "fileTypeId": 1
                },
                timeout=(3, 10),
                verify=False  # Sertifika doğrulamasını devre dışı bırak
            )
            r2.raise_for_status()
            result2 = r2.json()
            if not result2.get("success"):
                raise Exception("Data gönderimi başarısız")
            file_sent_status = 1
            print(f"[SEND] Data gönderildi → Event {event_id}")
        except Exception as e:
            print(f"[ERROR] Data gönderilemedi: {e}")
            file_sent_status = 2

        # 3. Başarılı güncelleme
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                self.sql_update_success(),
                (api_event_id, file_sent_status, now_iso, event_id)
            )
            conn.commit()


def main():

    """Ana program fonksiyonu."""
    # SQLite veritabanını başlat
    setup_database()

    # Gerekli klasörleri oluştur
    os.makedirs(EVENTS_DIR, exist_ok=True)

    # 1. RAM kuyruğunu oluştur
    ram_queue = Queue()

    # Loglamayı başlat
    system_logger, anomaly_logger = setup_logging()

    def start_all_threads(config):
        monitors, threads = [], []
        for cam_config in config['cameras']:
            monitor = CameraMonitor(
                cam_config,
                anomaly_logger,
                ram_queue,
                priorities=config.get("priorities", []),
                usecases=config.get("usecases", []),
            )
            monitors.append(monitor)

            thermal_thread = ThermalDataThread(
                name=monitor.name,
                url=monitor.thermal_api_url,
                auth=monitor.auth,
                data_callback=monitor._update_thermal_data,
                status_callback=lambda status, m=monitor: setattr(m, 'thermal_thread_status', status)
            )
            threads.append(thermal_thread)
            thermal_thread.start()

            scan_thread = threading.Thread(
                target=monitor.run,
                name=f"ScanCycle-{monitor.name}"
            )
            scan_thread.stop = monitor.stop  # ← bağlama işlemi burada
            threads.append(scan_thread)
            scan_thread.start()
        # 2. Gönderim thread'ini başlat
        send_thread = SendEventThread(
            db_path=DB_PATH,
            api_base_url=BASE_URL,
            api_key=API_KEY,
            ram_queue=ram_queue,
            poll_interval=1,
            backlog_scan_interval=10,
            max_retries=3
        )
        threads.append(send_thread)
        send_thread.start()
        return monitors, threads, send_thread

    def stop_all_threads(threads):
        for thread in threads:
            if hasattr(thread, 'stop'):
                thread.stop()
        time.sleep(3)
        for thread in threads:
            thread.join(timeout=5)

    def should_reload_config():
        try:
            reload_key = "global.hidden.termal.loadconfig"
            api_url = f"{BASE_URL}/GetOneByApiKey"
            headers = {"API-KEY": API_KEY}
            payload = {"InnerValue": reload_key}
            response = requests.get(api_url, headers=headers, json=payload, verify=False, timeout=5)
            response.raise_for_status()
            data = response.json()
            # API'den gelen yanıttaki 'value' değerini kontrol et
            if data.get("success"):
                load_config_status_str = data.get("data", {}).get("value", "true").lower()
                if load_config_status_str == "true":
                    try:
                        change_api_value_url= f"{BASE_URL}/SetOneByApiKey"
                        change_url_headers = {"API-KEY": API_KEY,"Content-Type": "application/json"}
                        change_payload = {"settingname": reload_key, "value": "false"}
                        change_response = requests.get(change_api_value_url, headers=change_url_headers, json=change_payload, verify=False, timeout=5)
                        system_logger.info(f"Config reload ayarı eski haline başarılı şekilde getirildi.")
                    except Exception as e:
                        system_logger.warning(f"API'ye config ayar değişikliği gönderilirken hata: {e}")

                    # EĞER 'true' GELDİYSE, TARAMA YAP
                    # self.status_message = "Tarama merkezi sistem tarafından başlatıldı."
                    #logging.warning(f"[{self.name}] Tarama, API'den gelen 'true' durumu nedeniyle başlatıldı.")
                    return True

            # Diğer tüm durumlarda (başarılı ve 'true' ise, veya bir hata oluşursa) taramaya devam et
            return False
        except Exception as e:
            system_logger.warning(f"Reload kontrolü sırasında hata: {e}")
            return False

    config = load_hybrid_config()
    monitors, threads, send_thread = start_all_threads(config)
    system_logger.info(f"{len(config['cameras'])} adet kamera yapılandırması bulundu. Sistem başlatılıyor...")

    try:
        while True:
            system_logger.info("===== SİSTEM DURUM KONTROLÜ =====")
            for monitor in monitors:
                log_level = logging.INFO if monitor.is_healthy else logging.WARNING
                system_logger.log(log_level,
                    f"Kamera: {monitor.name} | "
                    f"Sağlık: {'İYİ' if monitor.is_healthy else 'SORUNLU'} | "
                    f"Durum: {monitor.status_message} | "
                    f"Termal Veri: {monitor.thermal_thread_status} | "
                    f"Son Maks. Sıcaklık: {f'{monitor.last_max_temp:.1f}°C' if monitor.last_max_temp is not None else 'N/A'}"
                )
            system_logger.info("=================================")

            # Her döngüde reload kontrolü (60 saniyede bir)
            if should_reload_config():
                system_logger.warning("API'den reload tetiklendi! Konfigürasyon yeniden yükleniyor...")
                stop_all_threads(threads)
                config = load_hybrid_config()
                monitors, threads, send_thread = start_all_threads(config)
                system_logger.info(f"{len(config['cameras'])} adet kamera yapılandırması ile sistem yeniden başlatıldı.")

            time.sleep(60)

    except KeyboardInterrupt:
        system_logger.info("CTRL+C algılandı. Sistem kapatılıyor...")

    finally:
        stop_all_threads(threads)
        system_logger.info("Tüm işlemler durduruldu. Çıkış yapılıyor.")

if __name__ == "__main__":
    main()