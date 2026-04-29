import numpy as np
import itertools

class PTZScanner:
    """
    Orijinal kodundaki tarama rotası oluşturma mantığını (X, Y ve S-Tarama) 
    yöneten sınıftır.
    """
    @staticmethod
    def generate_tour_points(scan_p):
        pan_start = scan_p['pan_start_deg']
        pan_end = scan_p['pan_end_deg']
        pan_step = scan_p.get('step_deg', 10)
        
        # 1. Sabit Pozisyon Kontrolü (X_start == X_end)
        if pan_start == pan_end:
            tilt_fixed = scan_p.get('tilt_fixed_deg', 0)
            return [(pan_start, tilt_fixed)]

        # 2. X Ekseni (Pan) Rotalarını Oluştur (0/360 geçişi dahil)
        if pan_start < pan_end:
            forward_pan = list(np.arange(pan_start, pan_end + 1, pan_step))
            backward_pan = list(np.arange(pan_end, pan_start - 1, -pan_step))
        else:
            # 360/0 geçişli tarama (Standalone X Scan logic)
            path1 = list(np.arange(pan_start, 360, pan_step))
            path2 = list(np.arange(0, pan_end + 1, pan_step))
            forward_pan = path1 + path2
            path1_rev = list(np.arange(pan_end, -1, -pan_step))
            path2_rev = list(np.arange(359, pan_start - 1, -pan_step))
            backward_pan = path1_rev + path2_rev

        full_tour_points = []
        
        # 3. Y Ekseni (Tilt) ve 2D S-Tarama Kontrolü
        if 'pan_start_Ydeg' in scan_p and 'pan_end_Ydeg' in scan_p:
            tilt_start = scan_p['pan_start_Ydeg']
            tilt_end = scan_p['pan_end_Ydeg']
            tilt_step = scan_p.get('step_Ydeg', 10)
            tilt_levels = list(np.arange(tilt_start, tilt_end + 1, tilt_step))
            
            # S-Tarama Formasyonu (Her seviyede yön değiştirme)
            for i, tilt_deg in enumerate(tilt_levels):
                current_pan_path = forward_pan if i % 2 == 0 else backward_pan
                for pan_deg in current_pan_path:
                    full_tour_points.append((pan_deg, tilt_deg))
        else:
            # Sadece X Taraması (Sabit Tilt ile)
            tilt_fixed = scan_p.get('tilt_fixed_deg', 0)
            for pan_deg in (forward_pan + backward_pan):
                full_tour_points.append((pan_deg, tilt_fixed))
                
        return full_tour_points