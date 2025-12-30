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
DEBUG_HEIGHT = 650
BG_COLOR = (30, 30, 35)

# COR ALVO DA CIDADE (RGB: 244, 203, 148) -> BGR: (148, 203, 244)
TARGET_CITY_BGR = np.array([148, 203, 244], dtype=np.float32)
# Quão perto a cor precisa estar? (Quanto menor, mais rigoroso)
CITY_COLOR_THRESHOLD = 45.0 

class FinalFantasyBot:
    def __init__(self):
        self.sct = mss.mss()
        
        # --- OFFSET E TAMANHO ---
        self.rel_right_x = 1205
        self.rel_y = -20
        self.SIZE_BIG = 267     
        self.SIZE_SMALL = 187   
        
        # --- MEMÓRIA DE MAPAS ---
        self.maps_memory = {} 
        self.current_map_hash = None 
        
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
        self.debug_binary_banner = None
        self.debug_color_dist = 0.0 # Para mostrar na tela
        
        # Navegação
        self.current_direction = 'down' 
        self.stuck_counter = 0
        self.failed_directions = [] 
        self.last_move_time = 0

        # Pygame
        pygame.init()
        self.screen = pygame.display.set_mode((DEBUG_WIDTH, DEBUG_HEIGHT))
        pygame.display.set_caption("CEREBRO DO BOT - Color Validation")
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
        for k in ['up', 'down', 'left', 'right', 'enter']:
            pyautogui.keyUp(k)

    def check_map_transition(self, win_geo):
        # ... (Lógica do balão mantida idêntica) ...
        rel_x1, rel_y1 = 264, 75
        rel_x2, rel_y2 = 1009, 128
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
            gray = cv2.cvtColor(img, cv2.COLOR_BGRA2GRAY)
            _, binary = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY)
            self.debug_binary_banner = binary 
            
            if cv2.countNonZero(binary) < 100:
                if self.current_map_mode == "CIDADE":
                    if self.current_map_hash:
                         self.maps_memory[self.current_map_hash] = self.town_canvas.copy()
                    self.current_map_mode = "MUNDI"
                    self.current_map_hash = None
                return False

            img_hash = hashlib.md5(binary.tobytes()).hexdigest()
            if img_hash != self.current_map_hash:
                print(f"🌍 MAPA DETECTADO! Hash: {img_hash[:8]}")
                if self.current_map_hash is not None and self.current_map_mode == "CIDADE":
                    self.maps_memory[self.current_map_hash] = self.town_canvas.copy()

                if img_hash in self.maps_memory:
                    print("📂 Carregando mapa salvo...")
                    self.town_canvas = self.maps_memory[img_hash].copy()
                else:
                    print("✨ Novo mapa registrado!")
                    self.town_canvas = np.zeros((self.TOWN_SIZE, self.TOWN_SIZE), dtype=np.uint8)
                    self.town_x = self.TOWN_SIZE // 2
                    self.town_y = self.TOWN_SIZE // 2
                
                self.current_map_hash = img_hash
                self.current_map_mode = "CIDADE"
                self.failed_directions = []
                self.stuck_counter = 0
                time.sleep(1.0)
                return True
        except: pass
        return False

    def capture_smart_map(self, win_geo):
        # Lógica de captura padrão
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
                # Calcula média BGR da imagem atual
                avg_bgr = np.mean(img_bgr, axis=(0, 1)).astype(np.float32)
                blue, green, red = avg_bgr

                # --- LÓGICA DE DECISÃO REFINADA ---
                
                # 1. Verifica MUNDI (Azul predominante) - Prioridade alta
                is_blue_map = (blue > red) and (blue > green) and (blue > 40)
                if is_blue_map:
                    # Se o balão já sumiu e o mapa é azul, garante que estamos em modo MUNDI
                    if self.current_map_mode == "CIDADE":
                         if self.current_map_hash:
                             self.maps_memory[self.current_map_hash] = self.town_canvas.copy()
                         self.current_map_mode = "MUNDI"
                         self.current_map_hash = None
                    return img_bgr

                # 2. Verifica CIDADE (Pela distância da cor alvo)
                # Calcula distância Euclidiana entre a média atual e o alvo BGR
                dist = np.linalg.norm(avg_bgr - TARGET_CITY_BGR)
                self.debug_color_dist = dist # Para mostrar no dashboard

                # Se a distância for pequena, é a cor de cidade que queremos
                if dist < CITY_COLOR_THRESHOLD:
                    # Mantém modo cidade (definido pelo balão anteriormente)
                    self.current_map_mode = "CIDADE"
                    diff = self.SIZE_BIG - self.SIZE_SMALL
                    if img_bgr.shape[0] > self.SIZE_SMALL and img_bgr.shape[1] > diff:
                        return img_bgr[0:self.SIZE_SMALL, diff:] 
                    else: return img_bgr
                
                # 3. Se não for Azul e não for Bege Cidade (Ex: Tela Preta, Menu)
                # Retorna None para não processar movimento nem sujar o mapa
                return None

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
        # ... (Mantido) ...
        cx = win_geo["left"] + (win_geo["width"] // 2) - 200
        cy = win_geo["top"] + (win_geo["height"] // 2) - 250
        if cx < 0: cx = 0
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
        
        # 1. VISÃO CÂMERA
        if minimap is not None:
            rgb = cv2.cvtColor(minimap, cv2.COLOR_BGR2RGB)
            surf = pygame.surfarray.make_surface(np.transpose(rgb, (1, 0, 2)))
            self.screen.blit(surf, (20, 80))
            pygame.draw.rect(self.screen, (255, 255, 255), (20, 80, minimap.shape[1], minimap.shape[0]), 2)
            self.screen.blit(self.font.render(f"Câmera", True, (200, 200, 200)), (20, 60))

        # 2. DEBUG DO BANNER
        if self.debug_binary_banner is not None:
            b_rgb = cv2.cvtColor(self.debug_binary_banner, cv2.COLOR_GRAY2RGB)
            b_rgb = np.transpose(b_rgb, (1, 0, 2)) 
            surf_banner = pygame.surfarray.make_surface(b_rgb)
            surf_banner = pygame.transform.scale(surf_banner, (300, 50)) 
            self.screen.blit(surf_banner, (20, 450))
            pygame.draw.rect(self.screen, (255, 255, 0), (20, 450, 300, 50), 1)
            self.screen.blit(self.font.render("Visão Binária (Texto)", True, (255, 255, 0)), (20, 430))

        # 3. MAPA LÓGICO
        off_x = 350
        preview_size = 300
        
        if self.current_map_mode == "CIDADE":
            canvas_rgb = cv2.cvtColor(self.town_canvas, cv2.COLOR_GRAY2RGB)
            cv2.circle(canvas_rgb, (self.town_x, self.town_y), 5, (0, 0, 255), -1)
            surf_map = pygame.surfarray.make_surface(np.transpose(canvas_rgb, (1, 0, 2)))
            surf_map = pygame.transform.scale(surf_map, (preview_size, preview_size))
            self.screen.blit(surf_map, (off_x, 80))
            pygame.draw.rect(self.screen, (0, 255, 0), (off_x, 80, preview_size, preview_size), 1)
            h_str = self.current_map_hash[:6] if self.current_map_hash else "???"
            header = f"CIDADE ID: {h_str}"
        else: 
            canvas_rgb = cv2.cvtColor(self.world_canvas, cv2.COLOR_GRAY2RGB)
            cv2.circle(canvas_rgb, (self.world_x, self.world_y), 5, (0, 0, 255), -1)
            surf_map = pygame.surfarray.make_surface(np.transpose(canvas_rgb, (1, 0, 2)))
            self.screen.blit(surf_map, (off_x, 80))
            pygame.draw.rect(self.screen, (0, 100, 255), (off_x, 80, 267, 267), 1)
            header = "MAPA MUNDI (Global)"

        self.screen.blit(self.font.render(header, True, (0, 255, 255)), (off_x, 60))

        # Status e DEBUG DE COR
        c = (0, 255, 0) if "ANDANDO" in status_text else (255, 50, 50)
        self.screen.blit(self.font.render(f"STATUS: {status_text}", True, c), (20, 20))
        self.screen.blit(self.font.render(f"MAPAS NA MEMÓRIA: {len(self.maps_memory)}", True, (255, 100, 255)), (20, 400))
        
        # Mostra a distância da cor atual para a cor alvo
        dist_color = (0, 255, 0) if self.debug_color_dist < CITY_COLOR_THRESHOLD else (255, 0, 0)
        self.screen.blit(self.font.render(f"Cor Dist: {self.debug_color_dist:.1f} (Limiar: {CITY_COLOR_THRESHOLD})", True, dist_color), (20, 520))

        pygame.display.flip()

    def run(self):
        print("🎮 BOT RODANDO - Validação de Cor Ativa")
        while self.running:
            self.handle_calibration_input()
            win = self.get_window_geometry()
            if not win:
                self.release_all_keys()
                self.draw_dashboard(None, "PAUSADO")
                time.sleep(0.5)
                continue

            if self.check_map_transition(win):
                self.release_all_keys()
                continue

            minimap = self.capture_smart_map(win)
            # Se minimap for None (tela preta/menu), o bot fica em standby
            if minimap is None: 
                self.draw_dashboard(None, "AGUARDANDO MAPA VÁLIDO...")
                continue

            self.current_pos = self.find_player(minimap)
            moved = self.detect_movement(minimap)
            
            if self.detect_dialogue_bubble(win):
                self.release_all_keys()
                pyautogui.press('enter')
                self.draw_dashboard(minimap, "DIALOGO")
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

                if self.check_map_transition(current_win):
                    self.release_all_keys()
                    break 

                current_minimap = self.capture_smart_map(current_win)
                # Se o mapa ficar inválido no meio do movimento (ex: menu), para.
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