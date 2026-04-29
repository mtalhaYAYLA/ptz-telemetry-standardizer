# (Bağlantı Katmanı) sys.setrecursionlimit(5000) ayarını unutmadık, zeep için kritik.

import sys
from onvif import ONVIFCamera
import numpy as np

sys.setrecursionlimit(5000)


class CameraClient:
    """
    ONVIF protokolü üzerinden termal PTZ kamerasına bağlantı ve kontrol sağlar.

    Attributes:
        name (str): Kamera için tanımlayıcı isim (log ve ekran için).
        ip (str): Kameranın ağ IP adresi.
        user (str): ONVIF kimlik doğrulama kullanıcı adı.
        password (str): ONVIF kimlik doğrulama şifresi.
        cam (ONVIFCamera): Zeep tabanlı ONVIF kamera nesnesi.
        ptz (zeep.proxy): PTZ servis proxy'si.
        profile_token (str): Aktif medya profil token'ı (AbsoluteMove için gerekli).
    """

    def __init__(self, name, ip, user, password):
        """
        Args:
            name (str): Kameranın görünen adı. Örn: "Kamera-1_Depo"
            ip (str): Kameranın IP adresi. Örn: "192.168.1.100"
            user (str): ONVIF kullanıcı adı.
            password (str): ONVIF şifresi.
        """
        self.name = name
        self.ip = ip
        self.user = user
        self.password = password
        self.cam = None
        self.ptz = None
        self.profile_token = None

    def connect(self):
        """
        Kameraya ONVIF bağlantısı kurar; PTZ servisini ve medya profilini hazırlar.

        Returns:
            bool: Bağlantı başarılıysa True, hata oluşursa False.

        Raises:
            Yok — tüm istisnalar yakalanır, hata mesajı terminale basılır.
        """
        try:
            self.cam = ONVIFCamera(self.ip, 80, self.user, self.password)
            self.ptz = self.cam.create_ptz_service()
            self.profile_token = self.cam.create_media_service().GetProfiles()[0].token
            return True
        except Exception as e:
            print(f"[{self.name}] Bağlantı hatası: {e}")
            return False

    def move_to(self, pan_deg, tilt_deg):
        """
        Kamerayı belirtilen Pan/Tilt derecelerine AbsoluteMove komutuyla gönderir.

        Koordinat dönüşümü (V1 formülü):
            - Pan : corrected = (pan_deg - 180 + 360) % 360
                    ONVIF x  = interp(corrected, [0, 360], [-1, 1])
            - Tilt: ONVIF y  = interp(tilt_deg, (-5, 90), (1.0, -1.0))
                    (y=1.0 → -5° aşağı, y=-1.0 → 90° yukarı)

        Args:
            pan_deg (float): Hedef pan açısı, 0–359 derece arası.
            tilt_deg (float): Hedef tilt açısı, -5 ile 90 derece arası.

        Returns:
            None

        Raises:
            Yok — ONVIF hataları yakalanır, terminale basılır.
        """
        corrected_pan = (pan_deg - 180 + 360) % 360
        x = np.interp(corrected_pan, [0, 360], [-1, 1])
        y = np.interp(tilt_deg, (-5, 90), (1.0, -1.0))

        try:
            req = self.ptz.create_type('AbsoluteMove')
            req.ProfileToken = self.profile_token
            req.Position = {'PanTilt': {'x': x, 'y': y}, 'Zoom': {'x': 0.0}}
            req.Speed = {'PanTilt': {'x': 1.0, 'y': 1.0}}
            self.ptz.AbsoluteMove(req)
        except Exception as e:
            print(f"[{self.name}] Hareket hatası: {e}")

    def get_status(self):
        """
        Kameradan anlık PTZ pozisyonunu ONVIF GetStatus çağrısıyla okur.

        Returns:
            object | None: ONVIF PanTilt nesnesi (.x ve .y float alanlarıyla),
                           bağlantı/okuma hatası olursa None.

        Örnek dönüş:
            raw.x = 0.5   # ONVIF pan koordinatı (-1.0 ile 1.0 arası)
            raw.y = -0.05  # ONVIF tilt koordinatı (-1.0 ile 1.0 arası)
        """
        try:
            return self.ptz.GetStatus({'ProfileToken': self.profile_token}).Position.PanTilt
        except Exception as e:
            print(f"[{self.name}] Konum okuma hatası: {e}")
            return None
