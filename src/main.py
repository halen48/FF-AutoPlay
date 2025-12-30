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
        
        # --- CONFIGURAÇÃO DE CAPTURA ---
        self.rel_right_x = 1205
        self.rel_y = -20
        self.SIZE_BIG = 267     
        self.SIZE_SMALL = 187   
        
        # --- LÓGICA CIDADE (CANVAS 1024x1024) ---
        self.TOWN_SIZE = 1024
        self.town_canvas = np.zeros((self.TOWN_SIZE, self.TOWN_SIZE), dtype=np.uint8)
        # Começa no meio do canvas
        self.town_x = self.TOWN_SIZE // 2
        self.town_y = self.TOWN_SIZE // 2

        # --- LÓGICA WORLD MAP (GLOBO/%) ---
        # Vamos assumir um tamanho arbitrário de passos para dar a volta no mundo
        # Ex: 4000 passos para dar a volta completa. O % vai garantir o loop.
        self.WORLD_MAX_STEPS = 4000 
        self.world_x = 0
        self.world_y = 0

        # --- ESTADO GERAL ---
        self.current_map_mode = "DESCONHECIDO" # MUNDI ou CIDADE
        self.last_map_frame = None
        
        # Navegação
        self.current_direction = 'down' 
        self.stuck_counter = 0
        self.failed_directions = [] 

        # Pygame
        pygame.init()
        self.screen = pygame.display.set_mode((DEBUG_WIDTH, DEBUG_HEIGHT))
        pygame.display.set_caption("CEREBRO DO BOT - Hybrid Mapping")
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

    def capture_smart_map(self, win_geo):
        # ... (Lógica de captura e detecção de cor mantida) ...
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
            img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
            
            if img.size > 0:
                avg_color = np.mean(img, axis=(0, 1))
                blue, green, red = avg_color
                is_blue_map = (blue > red) and (blue > green) and (blue > 40)
                
                if is_blue_map:
                    self.current_map_mode = "MUNDI"
                    return img
                else:
                    self.current_map_mode = "CIDADE"
                    diff = self.SIZE_BIG - self.SIZE_SMALL
                    if img.shape[0] > self.SIZE_SMALL and img.shape[1] > diff:
                        return img[0:self.SIZE_SMALL, diff:] 
                    else: return img
        except: return None
        return None

    def detect_movement(self, frame):
        """Verifica visualmente se o mapa mexeu"""
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        moved = False
        
        if self.last_map_frame is not None and self.last_map_frame.shape == gray.shape:
            score = cv2.absdiff(self.last_map_frame, gray)
            non_zero = np.count_nonzero(score > 10) 
            
            if non_zero > 50:
                moved = True
                self.stuck_counter = 0
                if len(self.failed_directions) > 0:
                    self.failed_directions = [] 
            else:
                moved = False
                self.stuck_counter += 1

        self.last_map_frame = gray
        return moved

    def update_logical_position(self, direction):
        """
        ATUALIZA O XY DO MAPA COM BASE NA DIREÇÃO E NO MODO.
        Só é chamado se detect_movement() for True.
        """
        dx, dy = 0, 0
        if direction == 'up': dy = -1
        elif direction == 'down': dy = 1
        elif direction == 'left': dx = -1
        elif direction == 'right': dx = 1

        # MODO 1: CIDADE (Matriz Finita 1024x1024)
        if self.current_map_mode == "CIDADE":
            self.town_x += dx
            self.town_y += dy
            
            # Clamp (Não deixa sair do papel)
            self.town_x = max(0, min(self.town_x, self.TOWN_SIZE - 1))
            self.town_y = max(0, min(self.town_y, self.TOWN_SIZE - 1))
            
            # Pinta o rastro (Branco)
            # Pintamos um raio de 2px para ficar visível
            cv2.circle(self.town_canvas, (self.town_x, self.town_y), 2, 255, -1)

        # MODO 2: WORLD MAP (Globo Infinito com %)
        elif self.current_map_mode == "MUNDI":
            self.world_x += dx
            self.world_y += dy
            
            # Lógica de Módulo (%) para resetar nas extremidades
            # Isso simula a volta ao mundo
            self.world_x = self.world_x % self.WORLD_MAX_STEPS
            self.world_y = self.world_y % self.WORLD_MAX_STEPS

    def pick_smart_direction(self):
        all_dirs = ['up', 'down', 'left', 'right']
        valid_options = [d for d in all_dirs if d not in self.failed_directions]
        if not valid_options:
            self.failed_directions = []
            valid_options = all_dirs
        return random.choice(valid_options)

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
        
        # 1. VISÃO ATUAL (Esquerda)
        if minimap is not None:
            rgb = cv2.cvtColor(minimap, cv2.COLOR_BGR2RGB)
            surf = pygame.surfarray.make_surface(np.transpose(rgb, (1, 0, 2)))
            self.screen.blit(surf, (20, 80))
            pygame.draw.rect(self.screen, (255, 255, 255), (20, 80, minimap.shape[1], minimap.shape[0]), 2)
            self.screen.blit(self.font.render("Câmera", True, (200, 200, 200)), (20, 60))

        # 2. MAPA LÓGICO (Direita) - Depende do Modo
        preview_size = 300
        off_x = 350
        
        if self.current_map_mode == "CIDADE":
            # Mostra o Canvas 1024x1024
            canvas_rgb = cv2.cvtColor(self.town_canvas, cv2.COLOR_GRAY2RGB)
            # Desenha player
            cv2.circle(canvas_rgb, (self.town_x, self.town_y), 5, (0, 0, 255), -1)
            
            surf_town = pygame.surfarray.make_surface(np.transpose(canvas_rgb, (1, 0, 2)))
            surf_town = pygame.transform.scale(surf_town, (preview_size, preview_size))
            
            self.screen.blit(surf_town, (off_x, 80))
            pygame.draw.rect(self.screen, (0, 255, 0), (off_x, 80, preview_size, preview_size), 1)
            header = f"MAPA CIDADE (1024px) - {self.town_x},{self.town_y}"
            
        else: # MUNDI
            # Mostra um gráfico abstrato de coordenadas globo
            # Fundo Azul para representar o mundo
            world_surf = pygame.Surface((preview_size, preview_size))
            world_surf.fill((0, 0, 50))
            
            # Calcula posição relativa no globo (0 a 1)
            rel_x = (self.world_x / self.WORLD_MAX_STEPS) * preview_size
            rel_y = (self.world_y / self.WORLD_MAX_STEPS) * preview_size
            
            pygame.draw.circle(world_surf, (0, 255, 255), (int(rel_x), int(rel_y)), 10)
            self.screen.blit(world_surf, (off_x, 80))
            pygame.draw.rect(self.screen, (0, 100, 255), (off_x, 80, preview_size, preview_size), 1)
            
            pct_x = (self.world_x / self.WORLD_MAX_STEPS) * 100
            pct_y = (self.world_y / self.WORLD_MAX_STEPS) * 100
            header = f"MUNDO (GLOBO) - {pct_x:.1f}%, {pct_y:.1f}%"

        self.screen.blit(self.font.render(header, True, (0, 255, 255)), (off_x, 60))

        # STATUS GERAL
        c = (0, 255, 0) if "ANDANDO" in status_text else (255, 50, 50)
        self.screen.blit(self.font.render(f"STATUS: {status_text}", True, c), (20, 20))
        
        pygame.display.flip()

    def run(self):
        print("🎮 BOT HYBRID - 1024px Town / % World")
        directions = ['up', 'right', 'down', 'left']

        while self.running:
            self.handle_calibration_input()
            
            win = self.get_window_geometry()
            if not win:
                self.release_all_keys()
                self.draw_dashboard(None, "PAUSADO (Sem foco)")
                time.sleep(0.5)
                continue

            minimap = self.capture_smart_map(win)
            if minimap is None: continue

            if self.detect_dialogue_bubble(win):
                self.release_all_keys()
                pyautogui.press('enter')
                self.draw_dashboard(minimap, "DIALOGO")
                continue

            # --- CICLO DE MOVIMENTO (LONG PRESS) ---
            print(f"🏃 Andando: {self.current_direction}")
            pyautogui.keyDown(self.current_direction)
            
            start_move_time = time.time()
            collision_detected = False
            
            # Segura por até 5s
            while time.time() - start_move_time < 5.0:
                current_win = self.get_window_geometry()
                if not current_win:
                    self.release_all_keys()
                    break

                current_minimap = self.capture_smart_map(current_win)
                if current_minimap is None: break

                # 1. Verifica se mexeu
                moved = self.detect_movement(current_minimap)
                
                # 2. Se mexeu, atualiza a lógica do mapa (Pinta XY ou Incrementa %)
                if moved:
                    self.update_logical_position(self.current_direction)
                
                # 3. Desenha
                self.draw_dashboard(current_minimap, f"ANDANDO: {self.current_direction.upper()}")
                self.clock.tick(30) 

                # 4. Checa colisão
                if not moved:
                    if self.stuck_counter > 5:
                        print("🚫 COLISÃO CONFIRMADA.")
                        collision_detected = True
                        break 
                
                if self.detect_dialogue_bubble(current_win):
                    self.release_all_keys()
                    pyautogui.press('enter')
                    break

            pyautogui.keyUp(self.current_direction)
            
            # --- ESTRATÉGIA PÓS-COLISÃO ---
            if collision_detected:
                if self.current_direction not in self.failed_directions:
                    self.failed_directions.append(self.current_direction)
                
                self.current_direction = self.pick_smart_direction()
                time.sleep(0.2)

        pygame.quit()

if __name__ == "__main__":
    bot = FinalFantasyBot()
    bot.run()