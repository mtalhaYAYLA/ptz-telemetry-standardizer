import numpy as np
# Senin kodundaki np.interp mantığını "okuma" yönüne çevirerek buraya aldım.
class PTZTransformer:
    def __init__(self):
        # Senin kodundaki standart değerler
        self.TILT_DEGREE_RANGE = (-5, 90)
        self.ONVIF_TILT_RANGE = (-1.0, 1.0)

    def onvif_to_degree(self, x, y):
        """Kameradan gelen -1/1 değerini dereceye çevirir."""
        # Pan Dönüşümü (360 derece döngüsü dahil)
        pan_deg = np.interp(x, [-1, 1], [0, 360])
        # Senin kodundaki Tilt mantığı (Ters orantı var)
        tilt_deg = np.interp(y, [1.0, -1.0], self.TILT_DEGREE_RANGE)
        return round(pan_deg, 2), round(tilt_deg, 2)