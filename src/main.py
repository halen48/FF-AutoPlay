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
DEBUG_WIDTH = 800
DEBUG_HEIGHT = 500 # Aumentei um pouco pra caber status de dialogo
BG_COLOR = (20, 20, 20)

class FinalFantasyBot:
    def __init__(self):
        self.sct = mss.mss()
        self.current_pos = None
        self.target_pos = None
        self.matrix = None
        
        # --- CONFIGURAÇÃO MANUAL (Como no seu código) ---
        self.map_offset_x = 1215 
        self.map_offset_y = -20   
        self.map_w = 267
        self.map_h = 267
        
        self.last_move_time = 0
        self.move_interval = 0.1

        # Pygame
        pygame.init()
        self.screen = pygame.display.set_mode((DEBUG_WIDTH, DEBUG_HEIGHT))
        pygame.display.set_caption("CEREBRO DO BOT - Versão Pro")
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

    def capture_safe(self, top, left, width, height):
        """
        FUNÇÃO BLINDADA: Impede o crash 'XGetImage failed' ajustando
        automaticamente se as coordenadas saírem da tela.
        """
        # Pega tamanho do monitor principal
        monitor_idx = 1 # Geralmente 1 é o principal no MSS
        if len(self.sct.monitors) < 2: monitor_idx = 0
        screen_w = self.sct.monitors[monitor_idx]["width"]
        screen_h = self.sct.monitors[monitor_idx]["height"]

        # 1. Impede coordenadas negativas (O fix do seu erro!)
        if top < 0: top = 0
        if left < 0: left = 0

        # 2. Impede estourar a largura/altura
        if left + width > screen_w: width = screen_w - left
        if top + height > screen_h: height = screen_h - top

        # 3. Validação final
        if width <= 0 or height <= 0: return None

        region = {"top": int(top), "left": int(left), "width": int(width), "height": int(height)}
        
        try:
            img = np.array(self.sct.grab(region))
            return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
        except Exception as e:
            print(f"⚠️ Erro de captura: {e}")
            return None

    def detect_dialogue_bubble(self, win_geo):
        """
        Verifica se tem uma bolha de fala '...' no centro da tela.
        Estratégia: Procura por uma região BRANCA brilhante perto do centro.
        """
        # Captura o centro da janela do jogo (onde o boneco fica)
        # Uma área de 300x200 deve bastar
        center_x = win_geo["left"] + win_geo["width"] // 2 - 150
        center_y = win_geo["top"] + win_geo["height"] // 2 - 150
        
        img = self.capture_safe(center_y, center_x, 300, 300)
        if img is None: return False

        # Filtra cor BRANCA PURA (A bolha de fala)
        # O branco da UI costuma ser (255, 255, 255) ou muito próximo
        lower_white = np.array([240, 240, 240])
        upper_white = np.array([255, 255, 255])
        mask = cv2.inRange(img, lower_white, upper_white)

        # Acha contornos brancos
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        for c in contours:
            area = cv2.contourArea(c)
            # A bolha de fala é um retângulo razoável (ex: > 1000 pixels quadrados)
            # e geralmente é mais larga que alta
            if area > 1000:
                x, y, w, h = cv2.boundingRect(c)
                ratio = w / float(h)
                # Verifica se parece um balão (retangular/elíptico)
                if 1.0 < ratio < 4.0:
                    return True # ACHAMOS O DIÁLOGO!
        return False

    def find_player(self, img):
        if img is None: return None
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        mask1 = cv2.inRange(hsv, np.array([0, 150, 100]), np.array([10, 255, 255]))
        mask2 = cv2.inRange(hsv, np.array([170, 150, 100]), np.array([180, 255, 255]))
        mask = mask1 + mask2
        contours, _ = cv2.findContours(mask, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
        if contours:
            c = max(contours, key=cv2.contourArea)
            M = cv2.moments(c)
            if M["m00"] != 0:
                return (int(M["m10"] / M["m00"]), int(M["m01"] / M["m00"]))
        return None

    def generate_matrix(self, img):
        if img is None: return None, None
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        lower_blue = np.array([90, 50, 50])
        upper_blue = np.array([150, 255, 255])
        mask = cv2.inRange(hsv, lower_blue, upper_blue)
        matrix = (mask > 0).astype(int)
        return matrix, mask

    def handle_calibration_input(self):
        keys = pygame.key.get_pressed()
        speed = 5 if (keys[pygame.K_LSHIFT]) else 1
        if keys[pygame.K_UP]:    self.map_offset_y -= speed
        if keys[pygame.K_DOWN]:  self.map_offset_y += speed
        if keys[pygame.K_LEFT]:  self.map_offset_x -= speed
        if keys[pygame.K_RIGHT]: self.map_offset_x += speed
        # Tamanho
        if keys[pygame.K_d]: self.map_w += speed
        if keys[pygame.K_a]: self.map_w -= speed
        if keys[pygame.K_s]: self.map_h += speed
        if keys[pygame.K_w]: self.map_h -= speed

    def draw_dashboard(self, minimap, mask_debug, status_text, is_dialogue=False):
        for event in pygame.event.get():
            if event.type == pygame.QUIT: self.running = False
            if event.type == pygame.MOUSEBUTTONDOWN and self.matrix is not None:
                mx, my = pygame.mouse.get_pos()
                rel_x, rel_y = mx - 20, my - 60
                if 0 <= rel_x < 250 and 0 <= rel_y < 250:
                    scale_w = self.map_w / 250
                    scale_h = self.map_h / 250
                    self.target_pos = (int(rel_x * scale_w), int(rel_y * scale_h))

        self.screen.fill(BG_COLOR)
        
        # Visão Câmera
        if minimap is not None:
            rgb = cv2.cvtColor(minimap, cv2.COLOR_BGR2RGB)
            rgb = np.transpose(rgb, (1, 0, 2))
            surf = pygame.surfarray.make_surface(rgb)
            surf = pygame.transform.scale(surf, (250, 250))
            self.screen.blit(surf, (20, 60))
            pygame.draw.rect(self.screen, (255, 255, 255), (20, 60, 250, 250), 2)

        # Matriz
        if mask_debug is not None:
            mask_rgb = cv2.cvtColor(mask_debug, cv2.COLOR_GRAY2RGB)
            mask_rgb = np.transpose(mask_rgb, (1, 0, 2))
            surf_m = pygame.surfarray.make_surface(mask_rgb)
            surf_m = pygame.transform.scale(surf_m, (250, 250))
            self.screen.blit(surf_m, (300, 60))
            pygame.draw.rect(self.screen, (0, 255, 255), (300, 60, 250, 250), 1)

        # Status
        color_st = (0, 255, 0)
        if is_dialogue:
            status_text = "DIALOGO DETECTADO! (SPAMMANDO ENTER)"
            color_st = (255, 0, 0) # Vermelho alerta

        self.screen.blit(self.font.render(f"STATUS: {status_text}", True, color_st), (20, 20))
        self.screen.blit(self.font.render(f"OFFSET: X={self.map_offset_x} Y={self.map_offset_y}", True, (255, 255, 0)), (20, 360))
        self.screen.blit(self.font.render(f"Target: {self.target_pos}", True, (255, 0, 255)), (20, 390))
        
        pygame.display.flip()

    def get_direction_to_target(self):
        if not self.target_pos or not self.current_pos: return None
        tx, ty = self.target_pos
        px, py = self.current_pos
        dx = tx - px
        dy = ty - py
        if abs(dx) > abs(dy): return 'right' if dx > 0 else 'left'
        else: return 'down' if dy > 0 else 'up'

    def run(self):
        print("🎮 BOT RODANDO COM PROTEÇÃO DE CRASH + DETECTOR DE DIÁLOGO")
        
        while self.running:
            self.handle_calibration_input()
            win = self.get_window_geometry()
            
            if not win:
                self.draw_dashboard(None, None, "AGUARDANDO FINAL FANTASY...")
                time.sleep(0.5)
                continue

            # 1. CAPTURA SEGURA (Sem crash!)
            # Usa a capture_safe no lugar da lógica antiga
            # Note o cálculo: Left é (win_left + offset), Top é (win_top + offset)
            minimap = self.capture_safe(
                win["top"] + self.map_offset_y, 
                win["left"] + self.map_offset_x, # Correção aqui (Soma ou Subtrai dependendo do seu offset manual)
                self.map_w, 
                self.map_h
            )
            
            # Se a captura falhar, tenta de novo
            if minimap is None: continue

            # 2. PROCESSAMENTO
            self.current_pos = self.find_player(minimap)
            self.matrix, mask_debug = self.generate_matrix(minimap)
            
            # Debug visual
            if self.current_pos: cv2.circle(minimap, self.current_pos, 5, (0, 255, 0), -1)
            if self.target_pos:  cv2.circle(minimap, self.target_pos, 5, (255, 0, 255), -1)

            # 3. LÓGICA DE MOVIMENTO + DIÁLOGO
            is_dialogue = self.detect_dialogue_bubble(win)
            
            if is_dialogue:
                # MODO "SKIPPER": Segura direção + Enter
                move = self.get_direction_to_target()
                if move:
                    pyautogui.keyDown(move) # Segura a direção
                    pyautogui.press('enter') # Spama enter
                    time.sleep(0.05) # Pequeno delay
                    # Nota: Não dou keyUp aqui para ele "ficar segurando" como você pediu
                else:
                    pyautogui.press('enter') # Se não tem alvo, só pula texto
            
            else:
                # MODO NORMAL (Anda toque a toque)
                if self.current_pos and self.target_pos:
                    # Solta teclas que possam ter ficado presas no modo dialogo
                    # (Opcional, mas seguro)
                    for key in ['up', 'down', 'left', 'right']: pyautogui.keyUp(key)
                    
                    move = self.get_direction_to_target()
                    if move and (time.time() - self.last_move_time > self.move_interval):
                        pyautogui.keyDown(move)
                        time.sleep(0.05)
                        pyautogui.keyUp(move)
                        self.last_move_time = time.time()

            self.draw_dashboard(minimap, mask_debug, "RODANDO", is_dialogue)
            self.clock.tick(30)

        pygame.quit()

if __name__ == "__main__":
    bot = FinalFantasyBot()
    bot.run()