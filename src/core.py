# (Bağlantı Katmanı) sys.setrecursionlimit(5000) ayarını unutmadık, zeep için kritik.

import sys
from onvif import ONVIFCamera
import numpy as np

sys.setrecursionlimit(5000)

class CameraClient:
    def __init__(self, name, ip, user, password):
        self.name = name
        self.ip = ip
        self.user = user
        self.password = password
        self.cam = None
        self.ptz = None
        self.profile_token = None

    def connect(self):
        try:
            self.cam = ONVIFCamera(self.ip, 80, self.user, self.password)
            self.ptz = self.cam.create_ptz_service()
            self.profile_token = self.cam.create_media_service().GetProfiles()[0].token
            return True
        except Exception as e:
            print(f"[{self.name}] Bağlantı hatası: {e}")
            return False

    def move_to(self, pan_deg, tilt_deg):
        """Orijinal _convert_degrees_to_ptz ve AbsoluteMove mantığı"""
        # Koordinat dönüştürme
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
        try:
            return self.ptz.GetStatus({'ProfileToken': self.profile_token}).Position.PanTilt
        except:
            return None