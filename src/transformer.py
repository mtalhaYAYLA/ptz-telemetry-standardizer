import numpy as np


class PTZTransformer:
    """
    ONVIF ham koordinatlarını (-1.0 ile 1.0) insan tarafından okunabilir
    fiziksel derecelere dönüştürür.

    Kamera montaj açısı nedeniyle pan okumada +180° düzeltmesi uygulanır;
    bu düzeltme core.py'deki move_to() yazma formülüyle simetriktir.

    Attributes:
        TILT_DEGREE_RANGE (tuple): Fiziksel tilt sınırları. Varsayılan: (-5, 90).
            -5 → kamera hafifçe aşağı, 90 → kamera tam yukarı bakış.
        ONVIF_TILT_RANGE (tuple): ONVIF tilt değer aralığı. Sabit: (-1.0, 1.0).
    """

    def __init__(self):
        self.TILT_DEGREE_RANGE = (-5, 90)
        self.ONVIF_TILT_RANGE = (-1.0, 1.0)

    def onvif_to_degree(self, x, y):
        """
        ONVIF GetStatus'tan gelen ham x/y değerlerini fiziksel derecelere çevirir.

        Pan dönüşümü:
            raw_pan = interp(x, [-1, 1], [0, 360])
            pan_deg = (raw_pan + 180) % 360   ← montaj açısı düzeltmesi

        Tilt dönüşümü (ters orantı):
            tilt_deg = interp(y, [-1.0, 1.0], (90, -5))
            y=-1.0 → 90° (yukarı),  y=1.0 → -5° (aşağı)

        Args:
            x (float): ONVIF pan koordinatı. Aralık: -1.0 ile 1.0.
            y (float): ONVIF tilt koordinatı. Aralık: -1.0 ile 1.0.

        Returns:
            tuple[float, float]: (pan_deg, tilt_deg)
                - pan_deg  : 0.0 ile 359.99 arası, 2 ondalık hassasiyetle.
                - tilt_deg : -5.0 ile 90.0 arası, 2 ondalık hassasiyetle.

        Örnek:
            >>> tr = PTZTransformer()
            >>> tr.onvif_to_degree(0.5, -0.05)
            (270.0, 45.0)
        """
        # Pan: montaj açısı için +180° düzeltmesi (move_to'nun tersi)
        pan_deg = (np.interp(x, [-1, 1], [0, 360]) + 180) % 360
        # Tilt: xp artan sırada olmalı, fp ters çevrildi (y=-1→90°, y=1→-5°)
        tilt_deg = np.interp(y, [-1.0, 1.0], self.TILT_DEGREE_RANGE[::-1])
        return round(pan_deg, 2), round(tilt_deg, 2)
