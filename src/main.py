import time
import cv2
import numpy as np
import mss
import pyautogui
import random
import subprocess
import re
import pygame
import hashlib

# --- CONFIGURAÇÕES ---
WINDOW_NAME_PART = "Final Fantasy"
DEBUG_WIDTH = 1000
DEBUG_HEIGHT = 700
BG_COLOR = (30, 30, 35)

# --- CONFIGURAÇÕES DE TECLAS ---
KEY_SQUARE = 'v' # Ajuste aqui qual tecla do teclado é o Quadrado
KEY_CONFIRM = 'enter'

# --- CORES E LIMIARES ---
TARGET_CITY_BGR = np.array([148, 203, 244], dtype=np.float32)
CITY_COLOR_THRESHOLD = 60.0 
MAP_CHANGE_THRESHOLD = 0.65 

class FinalFantasyBot:
    def __init__(self):
        self.sct = mss.mss()
        
        # --- ALINHAMENTO ---
        self.rel_right_x = 1205
        self.rel_y = -20
        self.SIZE_BIG = 267     
        self.SIZE_SMALL = 187   
        
        # --- MEMÓRIA ---
        self.maps_memory = {} 
        self.current_map_id = None 
        self.current_map_signature = None
        
        # --- CANVAS ---
        self.TOWN_SIZE = 1024
        self.town_canvas = np.zeros((self.TOWN_SIZE, self.TOWN_SIZE), dtype=np.uint8)
        self.town_x = self.TOWN_SIZE // 2
        self.town_y = self.TOWN_SIZE // 2

        self.WORLD_SIZE = self.SIZE_BIG
        self.world_canvas = np.zeros((self.WORLD_SIZE, self.WORLD_SIZE), dtype=np.uint8)
        self.world_x = 0
        self.world_y = 0

        # --- ESTADO ---
        self.current_map_mode = "MUNDI"
        self.last_map_frame = None
        self.current_pos = None
        self.debug_similarity = 1.0
        self.debug_color_dist = 0.0
        self.in_battle = False
        
        # Navegação
        self.current_direction = 'down' 
        self.stuck_counter = 0
        self.failed_directions = [] 
        self.last_move_time = 0
        self.map_change_stability_counter = 0

        # Pygame
        pygame.init()
        self.screen = pygame.display.set_mode((DEBUG_WIDTH, DEBUG_HEIGHT))
        pygame.display.set_caption("CEREBRO DO BOT - Auto Battle")
        self.font = pygame.font.SysFont("Consolas", 14, bold=True)
        self.clock = pygame.time.Clock()
        self.running = True

    def get_window_geometry(self):
        try:
            aid = subprocess.check_output(['xdotool', 'getactivewindow']).decode().strip()
            name = subprocess.check_output(['xdotool', 'getwindowname', aid]).decode().strip()
            if WINDOW_NAME_PART.lower() not in name.lower(): return None
            geo = subprocess.check_output(['xdotool', 'getwindowgeometry', aid]).decode()
            pos = re.search(r'Position: (\d+),(\d+)', geo)
            geom = re.search(r'Geometry: (\d+)x(\d+)', geo)
            if pos and geom:
                return {
                    "top": int(pos.group(2)), "left": int(pos.group(1)), 
                    "width": int(geom.group(1)), "height": int(geom.group(2))
                }
        except: pass
        return None

    def release_all_keys(self):
        for k in ['up', 'down', 'left', 'right', 'enter', KEY_SQUARE]:
            pyautogui.keyUp(k)

    def check_battle_state(self, win_geo):
        """
        Verifica se entrou em batalha olhando a região azul do menu.
        Coords: 75,565 até 266,696 (Relativo à janela)
        """
        rel_x1, rel_y1 = 75, 565
        rel_x2, rel_y2 = 266, 696
        w = rel_x2 - rel_x1
        h = rel_y2 - rel_y1
        
        region = {
            "top": int(win_geo["top"] + rel_y1), 
            "left": int(win_geo["left"] + rel_x1), 
            "width": int(w), "height": int(h)
        }
        
        try:
            sct_img = self.sct.grab(region)
            img = np.array(sct_img)
            img_bgr = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
            
            # Média de cor
            if img_bgr.size > 0:
                avg_bgr = np.mean(img_bgr, axis=(0, 1))
                blue, green, red = avg_bgr
                
                # Critério de Menu de Batalha (Azul Escuro Clássico FF)
                # Geralmente B é alto, R é baixo.
                is_battle_menu = (blue > 100) and (blue > red + 40) and (blue > green + 10)
                
                return is_battle_menu
        except: pass
        return False

    def handle_battle(self, win_geo):
        """
        Rotina de Batalha:
        1. Aperta Quadrado (Auto Battle / Skill) UMA VEZ.
        2. Spama Enter até o menu azul sumir.
        """
        print("⚔️ BATALHA INICIADA!")
        self.in_battle = True
        self.release_all_keys()
        
        # 1. Aperta Quadrado (Uma vez)
        pyautogui.press(KEY_SQUARE)
        time.sleep(0.5) 
        
        # 2. Loop de Spam Enter
        while True:
            # Verifica se ainda tem foco
            win = self.get_window_geometry()
            if not win: break
            
            # Verifica se a batalha acabou (Menu azul sumiu)
            if not self.check_battle_state(win):
                print("🏁 Batalha Finalizada.")
                break
                
            pyautogui.press(KEY_CONFIRM)
            
            # Feedback Visual
            self.draw_dashboard(None, "EM COMBATE (Spam Enter)")
            self.clock.tick(10) # 10 clicks por segundo
            
        self.in_battle = False
        # Pequeno delay pós-batalha pra recuperar o fôlego
        time.sleep(1.0)

    # ... (Resto das funções auxiliares mantidas: signatures, detect, etc) ...
    def get_visual_signature(self, img):
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        hist = cv2.calcHist([hsv], [0, 1], None, [50, 60], [0, 180, 0, 256])
        cv2.normalize(hist, hist, 0, 1, cv2.NORM_MINMAX)
        return hist

    def compare_signatures(self, hist1, hist2):
        if hist1 is None or hist2 is None: return 0.0
        return cv2.compareHist(hist1, hist2, cv2.HISTCMP_CORREL)

    def classify_map_type(self, img):
        if img is None or img.size == 0: return "CIDADE"
        avg_bgr = np.mean(img, axis=(0, 1)).astype(np.float32)
        blue, green, red = avg_bgr
        dist_city = np.linalg.norm(avg_bgr - TARGET_CITY_BGR)
        self.debug_color_dist = dist_city
        if dist_city < CITY_COLOR_THRESHOLD: return "CIDADE"
        if (blue > red + 40) and (blue > green + 20) and (blue > 60): return "MUNDI"
        return "CIDADE"

    def check_smart_map_change(self, current_img):
        if self.current_map_signature is None:
            self.current_map_signature = self.get_visual_signature(current_img)
            self.current_map_id = hashlib.md5(current_img.tobytes()).hexdigest()[:8]
            self.current_map_mode = self.classify_map_type(current_img)
            return False

        new_sig = self.get_visual_signature(current_img)
        similarity = self.compare_signatures(self.current_map_signature, new_sig)
        self.debug_similarity = similarity

        if similarity < MAP_CHANGE_THRESHOLD:
            self.map_change_stability_counter += 1
        else:
            self.map_change_stability_counter = 0
            cv2.accumulateWeighted(new_sig, self.current_map_signature, 0.1)

        if self.map_change_stability_counter > 5:
            print(f"🌍 MUDANÇA VISUAL! Sim: {similarity:.2f}")
            if self.current_map_id and self.current_map_mode == "CIDADE":
                self.maps_memory[self.current_map_id] = self.town_canvas.copy()
            
            new_mode = self.classify_map_type(current_img)
            new_id = hashlib.md5(current_img.tobytes()).hexdigest()[:8]
            self.town_canvas = np.zeros((self.TOWN_SIZE, self.TOWN_SIZE), dtype=np.uint8)
            self.town_x = self.TOWN_SIZE // 2
            self.town_y = self.TOWN_SIZE // 2
            self.current_map_mode = new_mode
            self.current_map_id = new_id
            self.current_map_signature = new_sig
            self.map_change_stability_counter = 0
            time.sleep(1.0)
            return True
        return False

    def capture_smart_map(self, win_geo):
        start_x_relative = self.rel_right_x - self.SIZE_BIG
        abs_top = win_geo["top"] + self.rel_y
        abs_left = win_geo["left"] + start_x_relative
        monitor_idx = 1 if len(self.sct.monitors) > 1 else 0
        screen_w = self.sct.monitors[monitor_idx]["width"]
        screen_h = self.sct.monitors[monitor_idx]["height"]
        if abs_top < 0: abs_top = 0
        if abs_left < 0: abs_left = 0
        width = self.SIZE_BIG; height = self.SIZE_BIG 
        if abs_left + width > screen_w: width = screen_w - abs_left
        if abs_top + height > screen_h: height = screen_h - abs_top
        if width <= 0 or height <= 0: return None
        region = {"top": int(abs_top), "left": int(abs_left), "width": int(width), "height": int(height)}
        try:
            sct_img = self.sct.grab(region)
            img = np.array(sct_img)
            img_bgr = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
            if img_bgr.size > 0:
                if self.current_map_mode == "CIDADE":
                    diff = self.SIZE_BIG - self.SIZE_SMALL
                    if img_bgr.shape[0] > self.SIZE_SMALL:
                        return img_bgr[0:self.SIZE_SMALL, diff:] 
                return img_bgr
        except: return None
        return None

    def detect_movement(self, frame):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        moved = False
        if self.last_map_frame is not None and self.last_map_frame.shape == gray.shape:
            score = cv2.absdiff(self.last_map_frame, gray)
            if np.count_nonzero(score > 10) > 50:
                moved = True
                self.stuck_counter = 0
                if self.failed_directions: self.failed_directions = [] 
            else:
                moved = False
                self.stuck_counter += 1
        self.last_map_frame = gray
        return moved

    def update_position_logic(self, moved, direction):
        if self.current_map_mode == "MUNDI":
            if self.current_pos:
                self.world_x, self.world_y = self.current_pos
                self.world_x = max(0, min(self.world_x, self.WORLD_SIZE - 1))
                self.world_y = max(0, min(self.world_y, self.WORLD_SIZE - 1))
                cv2.circle(self.world_canvas, (self.world_x, self.world_y), 2, 255, -1)
        elif self.current_map_mode == "CIDADE" and moved:
            dx, dy = 0, 0
            if direction == 'up': dy = -1
            elif direction == 'down': dy = 1
            elif direction == 'left': dx = -1
            elif direction == 'right': dx = 1
            self.town_x += dx
            self.town_y += dy
            self.town_x = max(0, min(self.town_x, self.TOWN_SIZE - 1))
            self.town_y = max(0, min(self.town_y, self.TOWN_SIZE - 1))
            cv2.circle(self.town_canvas, (self.town_x, self.town_y), 2, 255, -1)

    def pick_smart_direction(self):
        all_dirs = ['up', 'down', 'left', 'right']
        valid = [d for d in all_dirs if d not in self.failed_directions]
        if not valid:
            self.failed_directions = []
            valid = all_dirs
        return random.choice(valid)

    def detect_dialogue_bubble(self, win_geo):
        cx = win_geo["left"] + (win_geo["width"] // 2) - 200
        cy = win_geo["top"] + (win_geo["height"] // 2) - 250
        if cx < 0: cx = 0; 
        if cy < 0: cy = 0
        region = {"top": int(cy), "left": int(cx), "width": 400, "height": 450}
        try:
            img = np.array(self.sct.grab(region))
            img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
            mask = cv2.inRange(img, np.array([240, 240, 240]), np.array([255, 255, 255]))
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for c in contours:
                if cv2.contourArea(c) > 1000:
                    x, y, w, h = cv2.boundingRect(c)
                    if 1.0 < w/float(h) < 6.0: return True
        except: pass
        return False

    def find_player(self, img):
        if img is None: return None
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, np.array([0, 150, 100]), np.array([10, 255, 255])) + \
               cv2.inRange(hsv, np.array([170, 150, 100]), np.array([180, 255, 255]))
        contours, _ = cv2.findContours(mask, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
        if contours:
            c = max(contours, key=cv2.contourArea)
            M = cv2.moments(c)
            if M["m00"] != 0:
                return (int(M["m10"]/M["m00"]), int(M["m01"]/M["m00"]))
        return None

    def handle_calibration_input(self):
        keys = pygame.key.get_pressed()
        speed = 5 if (keys[pygame.K_LSHIFT]) else 1
        if keys[pygame.K_LEFT]:  self.rel_right_x -= speed
        if keys[pygame.K_RIGHT]: self.rel_right_x += speed
        if keys[pygame.K_UP]:    self.rel_y -= speed
        if keys[pygame.K_DOWN]:  self.rel_y += speed

    def draw_dashboard(self, minimap, status_text):
        for event in pygame.event.get():
            if event.type == pygame.QUIT: self.running = False
        
        self.screen.fill(BG_COLOR)
        
        if minimap is not None:
            rgb = cv2.cvtColor(minimap, cv2.COLOR_BGR2RGB)
            surf = pygame.surfarray.make_surface(np.transpose(rgb, (1, 0, 2)))
            self.screen.blit(surf, (20, 80))
            pygame.draw.rect(self.screen, (255, 255, 255), (20, 80, minimap.shape[1], minimap.shape[0]), 2)
            self.screen.blit(self.font.render(f"Visão ({minimap.shape[1]}px)", True, (200, 200, 200)), (20, 60))

        off_x = 350
        preview_size = 300
        
        if self.current_map_mode == "CIDADE":
            canvas_rgb = cv2.cvtColor(self.town_canvas, cv2.COLOR_GRAY2RGB)
            cv2.circle(canvas_rgb, (self.town_x, self.town_y), 5, (0, 0, 255), -1)
            surf_map = pygame.surfarray.make_surface(np.transpose(canvas_rgb, (1, 0, 2)))
            surf_map = pygame.transform.scale(surf_map, (preview_size, preview_size))
            self.screen.blit(surf_map, (off_x, 80))
            pygame.draw.rect(self.screen, (0, 255, 0), (off_x, 80, preview_size, preview_size), 1)
            header = f"CIDADE ID: {self.current_map_id}"
        else: 
            canvas_rgb = cv2.cvtColor(self.world_canvas, cv2.COLOR_GRAY2RGB)
            cv2.circle(canvas_rgb, (self.world_x, self.world_y), 5, (0, 0, 255), -1)
            surf_map = pygame.surfarray.make_surface(np.transpose(canvas_rgb, (1, 0, 2)))
            self.screen.blit(surf_map, (off_x, 80))
            pygame.draw.rect(self.screen, (0, 100, 255), (off_x, 80, 267, 267), 1)
            header = "MAPA MUNDI"

        self.screen.blit(self.font.render(header, True, (0, 255, 255)), (off_x, 60))

        c = (255, 50, 50) if "COMBATE" in status_text or "DIALOGO" in status_text else (0, 255, 0)
        self.screen.blit(self.font.render(f"STATUS: {status_text}", True, c), (20, 20))
        
        sim_pct = int(self.debug_similarity * 100)
        sim_color = (0, 255, 0) if self.debug_similarity > MAP_CHANGE_THRESHOLD else (255, 0, 0)
        self.screen.blit(self.font.render(f"Sim Vis: {sim_pct}%", True, sim_color), (20, 400))
        dist_color = (0, 255, 0) if self.debug_color_dist < CITY_COLOR_THRESHOLD else (255, 0, 0)
        self.screen.blit(self.font.render(f"Cor Bege Dist: {self.debug_color_dist:.1f}", True, dist_color), (20, 430))

        pygame.display.flip()

    def run(self):
        print("🎮 BOT RODANDO - Modo Batalha Ativo")
        while self.running:
            self.handle_calibration_input()
            win = self.get_window_geometry()
            if not win:
                self.release_all_keys()
                self.draw_dashboard(None, "PAUSADO")
                time.sleep(0.5)
                continue

            # CHECK DE BATALHA (PRIORIDADE ALTA)
            if self.check_battle_state(win):
                self.handle_battle(win)
                continue

            raw_minimap = self.capture_smart_map(win)
            if raw_minimap is None or np.mean(raw_minimap) < 10: 
                continue

            if self.check_smart_map_change(raw_minimap):
                self.release_all_keys()
                continue

            self.current_pos = self.find_player(raw_minimap)
            moved = self.detect_movement(raw_minimap)
            
            if self.detect_dialogue_bubble(win):
                self.release_all_keys()
                pyautogui.press('enter')
                self.draw_dashboard(raw_minimap, "DIALOGO")
                continue

            # MOVIMENTO
            pyautogui.keyDown(self.current_direction)
            start_move_time = time.time()
            collision_detected = False
            
            while time.time() - start_move_time < 5.0:
                current_win = self.get_window_geometry()
                if not current_win:
                    self.release_all_keys()
                    break
                
                # Check de Batalha no meio do andar
                if self.check_battle_state(current_win):
                    self.release_all_keys()
                    # Sai do loop de movimento, o próximo loop principal vai tratar a batalha
                    break 

                if self.check_smart_map_change(current_win):
                    self.release_all_keys()
                    break 

                current_minimap = self.capture_smart_map(current_win)
                if current_minimap is None: 
                    self.release_all_keys()
                    break

                self.current_pos = self.find_player(current_minimap)
                frame_moved = self.detect_movement(current_minimap)
                self.update_position_logic(frame_moved, self.current_direction)
                
                self.draw_dashboard(current_minimap, f"ANDANDO: {self.current_direction.upper()}")
                self.clock.tick(30) 

                if not frame_moved:
                    if self.stuck_counter > 5:
                        collision_detected = True
                        break 
                
                if self.detect_dialogue_bubble(current_win):
                    self.release_all_keys()
                    pyautogui.press('enter')
                    break

            pyautogui.keyUp(self.current_direction)
            
            if collision_detected:
                if self.current_direction not in self.failed_directions:
                    self.failed_directions.append(self.current_direction)
                self.current_direction = self.pick_smart_direction()
                time.sleep(0.2)

        pygame.quit()

if __name__ == "__main__":
    bot = FinalFantasyBot()
    bot.run()