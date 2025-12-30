import time
import cv2
import numpy as np
import mss
import pyautogui
import random
import subprocess
import re
import pygame

# --- CONFIGURAÇÕES ---
WINDOW_NAME_PART = "Final Fantasy"
DEBUG_WIDTH = 1000  
DEBUG_HEIGHT = 600
BG_COLOR = (30, 30, 35)

class FinalFantasyBot:
    def __init__(self):
        self.sct = mss.mss()
        self.current_pos = None
        self.target_pos = None
        
        # --- CONFIGURAÇÃO DE CAPTURA ---
        self.rel_right_x = 1205
        self.rel_y = -20
        self.SIZE_BIG = 267     
        self.SIZE_SMALL = 187   
        
        # --- MAPA GLOBAL DINÂMICO (SLAM) ---
        # Começamos com um canvas 800x800
        self.global_map = np.zeros((800, 800, 3), dtype=np.uint8)
        self.global_visited = np.zeros((800, 800), dtype=np.uint8)
        
        # Posição da "Câmera" no Mapa Global (Começa no meio)
        self.cam_global_x = 400
        self.cam_global_y = 400
        
        self.last_frame_gray = None
        self.current_map_mode = "MUNDI"
        self.last_move_time = 0
        self.move_interval = 0.1

        # Pygame
        pygame.init()
        self.screen = pygame.display.set_mode((DEBUG_WIDTH, DEBUG_HEIGHT))
        pygame.display.set_caption("CEREBRO DO BOT - SLAM Mapping Fixed")
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

    def capture_smart_map(self, win_geo):
        start_x_relative = self.rel_right_x - self.SIZE_BIG
        abs_top = win_geo["top"] + self.rel_y
        abs_left = win_geo["left"] + start_x_relative
        
        monitor_idx = 1 if len(self.sct.monitors) > 1 else 0
        screen_w = self.sct.monitors[monitor_idx]["width"]
        screen_h = self.sct.monitors[monitor_idx]["height"]

        if abs_top < 0: abs_top = 0
        if abs_left < 0: abs_left = 0
        
        width = self.SIZE_BIG
        height = self.SIZE_BIG 
        
        if abs_left + width > screen_w: width = screen_w - abs_left
        if abs_top + height > screen_h: height = screen_h - abs_top

        if width <= 0 or height <= 0: return None

        region = {"top": int(abs_top), "left": int(abs_left), "width": int(width), "height": int(height)}
        
        try:
            sct_img = self.sct.grab(region)
            img = np.array(sct_img)
            img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
            
            if img.size > 0:
                avg_color = np.mean(img, axis=(0, 1))
                blue, green, red = avg_color
                is_blue_map = (blue > red) and (blue > green) and (blue > 40)
                
                if is_blue_map:
                    self.current_map_mode = "MUNDI (267px)"
                    # Se for mapa mundi, retornamos ele todo. 
                    # Resetar SLAM se mudar de modo? Por enquanto não.
                    return img
                else:
                    self.current_map_mode = "CIDADE (187px)"
                    diff = self.SIZE_BIG - self.SIZE_SMALL
                    if img.shape[0] > self.SIZE_SMALL and img.shape[1] > diff:
                        return img[0:self.SIZE_SMALL, diff:] 
                    else:
                        return img
        except Exception as e: return None
        return None

    def update_slam_map(self, frame):
        """Lógica de Costura de Mapa (Stitching)"""
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape

        # Inicialização
        if self.last_frame_gray is None:
            self.last_frame_gray = gray
            # Cola no centro
            self.global_map[self.cam_global_y:self.cam_global_y+h, self.cam_global_x:self.cam_global_x+w] = frame
            return

        # Se o tamanho do recorte mudou (entrou na cidade), reseta a referência para evitar crash
        if self.last_frame_gray.shape != gray.shape:
             self.last_frame_gray = gray
             return

        # 1. CALCULAR DESLOCAMENTO
        margin = 40
        # Pega um pedaço do frame anterior
        patch = self.last_frame_gray[margin:h-margin, margin:w-margin]
        
        try:
            # Procura esse pedaço no frame novo
            res = cv2.matchTemplate(gray, patch, cv2.TM_CCOEFF_NORMED)
            _, _, _, max_loc = cv2.minMaxLoc(res)
            
            # Calcula o delta
            dx = max_loc[0] - margin
            dy = max_loc[1] - margin
            
            # Atualiza posição GLOBAL da câmera (Invertido: Se a imagem foi pra esq, camera foi pra dir)
            self.cam_global_x -= dx
            self.cam_global_y -= dy

            # 2. EXPANSÃO AUTOMÁTICA (Pad)
            pad = 100
            gh, gw, _ = self.global_map.shape
            
            pad_left = 0
            pad_top = 0
            
            # Verifica estouro de bordas
            if self.cam_global_x < pad:
                pad_left = 200
                self.cam_global_x += pad_left
            if self.cam_global_y < pad:
                pad_top = 200
                self.cam_global_y += pad_top
            
            pad_right = 0
            pad_bottom = 0
            if self.cam_global_x + w > gw - pad: pad_right = 200
            if self.cam_global_y + h > gh - pad: pad_bottom = 200
                
            if pad_left or pad_right or pad_top or pad_bottom:
                self.global_map = np.pad(self.global_map, ((pad_top, pad_bottom), (pad_left, pad_right), (0,0)), mode='constant')
                self.global_visited = np.pad(self.global_visited, ((pad_top, pad_bottom), (pad_left, pad_right)), mode='constant')
                print(f"🗺️ MAPA EXPANDIDO! {self.global_map.shape}")

            # 3. COLAGEM (Stitching)
            y1, y2 = self.cam_global_y, self.cam_global_y + h
            x1, x2 = self.cam_global_x, self.cam_global_x + w
            
            # Garante que não vai estourar índice (Safety Check)
            if y2 <= self.global_map.shape[0] and x2 <= self.global_map.shape[1]:
                self.global_map[y1:y2, x1:x2] = frame
                
                # Rastro (Marca o centro da câmera como visitado)
                cx = self.cam_global_x + w//2
                cy = self.cam_global_y + h//2
                cv2.circle(self.global_visited, (cx, cy), 4, 255, -1)
            
            self.last_frame_gray = gray
            
        except Exception as e:
            print(f"Erro SLAM: {e}")

    def detect_dialogue_bubble(self, win_geo):
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

    def draw_dashboard(self, minimap, status_text, is_dialogue=False):
        for event in pygame.event.get():
            if event.type == pygame.QUIT: self.running = False
            
        self.screen.fill(BG_COLOR)
        
        # 1. VISÃO ATUAL (Esquerda)
        if minimap is not None:
            rgb = cv2.cvtColor(minimap, cv2.COLOR_BGR2RGB)
            surf = pygame.surfarray.make_surface(np.transpose(rgb, (1, 0, 2)))
            self.screen.blit(surf, (20, 80))
            pygame.draw.rect(self.screen, (255, 255, 255), (20, 80, minimap.shape[1], minimap.shape[0]), 2)
            self.screen.blit(self.font.render("Visão Atual", True, (200, 200, 200)), (20, 60))

        # 2. MAPA GLOBAL COSTURADO (Direita)
        gh, gw, _ = self.global_map.shape
        if gh > 0 and gw > 0:
            scale = min(500/gw, 400/gh)
            new_w, new_h = int(gw*scale), int(gh*scale)
            
            if new_w > 0 and new_h > 0:
                small_global = cv2.resize(self.global_map, (new_w, new_h))
                small_visited = cv2.resize(self.global_visited, (new_w, new_h))

                # --- FIX DO CRASH AQUI ---
                # Em vez de addWeighted, usamos lógica NumPy pura com Broadcasting
                # Acha pixels visitados
                visited_indices = small_visited > 0
                
                # Se tiver algum lugar visitado, pinta de verde semi-transparente
                if np.any(visited_indices):
                    # Pega os pixels originais
                    pixels = small_global[visited_indices]
                    # Define Verde BGR (OpenCV usa BGR)
                    green = np.array([0, 255, 0], dtype=np.float32)
                    # Mistura: 50% Cor Original + 50% Verde
                    blended = (pixels * 0.5 + green * 0.5).astype(np.uint8)
                    # Atribui de volta
                    small_global[visited_indices] = blended

                # Desenha o quadrado da câmera
                if minimap is not None:
                    cam_x_s = int(self.cam_global_x * scale)
                    cam_y_s = int(self.cam_global_y * scale)
                    cam_w_s = int(minimap.shape[1] * scale)
                    cam_h_s = int(minimap.shape[0] * scale)
                    cv2.rectangle(small_global, (cam_x_s, cam_y_s), (cam_x_s+cam_w_s, cam_y_s+cam_h_s), (0, 255, 255), 2)

                rgb_global = cv2.cvtColor(small_global, cv2.COLOR_BGR2RGB)
                surf_g = pygame.surfarray.make_surface(np.transpose(rgb_global, (1, 0, 2)))
                
                self.screen.blit(surf_g, (300, 80))
                pygame.draw.rect(self.screen, (100, 100, 255), (300, 80, new_w, new_h), 1)
                self.screen.blit(self.font.render(f"MAPA GLOBAL ({gw}x{gh})", True, (100, 100, 255)), (300, 60))

        c = (255, 50, 50) if is_dialogue else (0, 255, 0)
        self.screen.blit(self.font.render(f"STATUS: {status_text}", True, c), (20, 20))
        self.screen.blit(self.font.render(f"OFFSET X: {self.rel_right_x}", True, (255, 255, 0)), (20, 400))
        
        pygame.display.flip()

    def get_move(self):
        if random.random() < 0.05:
             return random.choice(['up', 'down', 'left', 'right'])
        return None

    def run(self):
        print("🎮 BOT RODANDO - Crash corrigido! Costurando mapa...")
        while self.running:
            self.handle_calibration_input()
            win = self.get_window_geometry()
            
            if not win:
                self.draw_dashboard(None, "AGUARDANDO...", False)
                time.sleep(0.5)
                continue

            minimap = self.capture_smart_map(win)
            if minimap is None: continue

            # SLAM
            self.update_slam_map(minimap)
            self.current_pos = self.find_player(minimap)

            is_chat = self.detect_dialogue_bubble(win)
            if is_chat:
                pyautogui.press('enter')
            else:
                move = self.get_move()
                if move and (time.time() - self.last_move_time > self.move_interval):
                    pyautogui.keyDown(move); time.sleep(0.05); pyautogui.keyUp(move)
                    self.last_move_time = time.time()

            self.draw_dashboard(minimap, "EXPLORANDO", is_chat)
            self.clock.tick(30)
        pygame.quit()

if __name__ == "__main__":
    bot = FinalFantasyBot()
    bot.run()