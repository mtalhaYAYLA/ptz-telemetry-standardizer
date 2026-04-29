import numpy as np
import itertools


class PTZScanner:
    """
    config.json'daki scan_parameters bloğunu okuyarak kameranın izleyeceği
    Pan/Tilt tur noktalarını üretir.

    Desteklenen tarama modları:
        - Sabit Nokta : pan_start == pan_end → tek nokta döner.
        - X Tarama   : Yatay eksende ileri-geri (sabit tilt ile).
        - 2D S-Tarama: Her tilt seviyesinde yön değiştirerek tam alan taraması.
        - 360° Geçiş : pan_start > pan_end ise 360/0 sınırını akıllıca aşar.
    """

    @staticmethod
    def generate_tour_points(scan_p):
        """
        Tarama parametrelerinden sıralı (pan, tilt) tur noktaları listesi üretir.

        Algoritma:
            1. pan_start == pan_end ise sabit nokta döner.
            2. pan_start < pan_end  → düz X taraması (ileri + geri).
            3. pan_start > pan_end  → 360°/0° sınırını geçen X taraması.
            4. pan_start_Ydeg ve pan_end_Ydeg varsa → 2D S-tarama oluşturulur.
               Her tilt seviyesinde yön çevrilir (çift indeks → ileri, tek → geri).

        Args:
            scan_p (dict): config.json'daki scan_parameters sözlüğü. Beklenen anahtarlar:
                - pan_start_deg   (int)   : Pan başlangıç açısı (0–359).
                - pan_end_deg     (int)   : Pan bitiş açısı (0–359).
                - step_deg        (float) : Pan adım büyüklüğü. Varsayılan: 10.
                - pan_start_Ydeg  (float) : Tilt başlangıç açısı. (S-tarama için)
                - pan_end_Ydeg    (float) : Tilt bitiş açısı. (S-tarama için)
                - step_Ydeg       (float) : Tilt adım büyüklüğü. Varsayılan: 10.
                - tilt_fixed_deg  (float) : Sabit tilt (X-tarama için). Varsayılan: 0.

        Returns:
            list[tuple[float, float]]: [(pan_deg, tilt_deg), ...] formatında tur noktaları.
                Liste worker tarafından itertools.cycle ile sonsuz döngüye alınır.

        Örnek (X-tarama, pan 0→30, step 10, sabit tilt 20):
            [(0, 20), (10, 20), (20, 20), (30, 20),
             (30, 20), (20, 20), (10, 20), (0, 20)]

        Örnek (S-tarama, pan 0→20, tilt 5→15, step 10):
            [(0,5),(10,5),(20,5),  ← ileri, tilt=5
             (20,15),(10,15),(0,15)]  ← geri, tilt=15
        """
        pan_start = scan_p['pan_start_deg']
        pan_end = scan_p['pan_end_deg']
        pan_step = scan_p.get('step_deg', 10)

        # 1. Sabit Pozisyon (pan_start == pan_end)
        if pan_start == pan_end:
            tilt_fixed = scan_p.get('tilt_fixed_deg', 0)
            return [(pan_start, tilt_fixed)]

        # 2. X Ekseni rotalarını oluştur (360/0 geçişi dahil)
        if pan_start < pan_end:
            forward_pan = list(np.arange(pan_start, pan_end + 1, pan_step))
            backward_pan = list(np.arange(pan_end, pan_start - 1, -pan_step))
        else:
            # 360°/0° sınırını geçen tarama
            path1 = list(np.arange(pan_start, 360, pan_step))
            path2 = list(np.arange(0, pan_end + 1, pan_step))
            forward_pan = path1 + path2
            path1_rev = list(np.arange(pan_end, -1, -pan_step))
            path2_rev = list(np.arange(359, pan_start - 1, -pan_step))
            backward_pan = path1_rev + path2_rev

        full_tour_points = []

        # 3. S-Tarama (Y ekseni varsa) veya düz X-tarama
        if 'pan_start_Ydeg' in scan_p and 'pan_end_Ydeg' in scan_p:
            tilt_start = scan_p['pan_start_Ydeg']
            tilt_end = scan_p['pan_end_Ydeg']
            tilt_step = scan_p.get('step_Ydeg', 10)
            tilt_levels = list(np.arange(tilt_start, tilt_end + 1, tilt_step))

            # S-Tarama: çift indeks ileri, tek indeks geri
            for i, tilt_deg in enumerate(tilt_levels):
                current_pan_path = forward_pan if i % 2 == 0 else backward_pan
                for pan_deg in current_pan_path:
                    full_tour_points.append((pan_deg, tilt_deg))
        else:
            # Sadece X Taraması (sabit tilt)
            tilt_fixed = scan_p.get('tilt_fixed_deg', 0)
            for pan_deg in (forward_pan + backward_pan):
                full_tour_points.append((pan_deg, tilt_fixed))

        return full_tour_points
