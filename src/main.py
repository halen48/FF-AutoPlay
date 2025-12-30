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
DEBUG_WIDTH = 600
DEBUG_HEIGHT = 600 # Aumentei pra caber tudo
BG_COLOR = (30, 30, 35)

class FinalFantasyBot:
    def __init__(self):
        self.sct = mss.mss()
        self.current_pos = None
        self.target_pos = None
        self.matrix = None
        
        # --- CONFIGURAÇÃO DE ALINHAMENTO ---
        # Referência: CANTO DIREITO DO MAPA
        self.rel_right_x = 1205  # <--- Atualizado conforme pedido
        self.rel_y = -20         # Ajuste vertical
        
        # Tamanhos (Quadrados)
        self.SIZE_BIG = 267     
        self.SIZE_SMALL = 187   
        
        self.current_map_mode = "MUNDI" # Começa assumindo grande

        self.last_move_time = 0
        self.move_interval = 0.1

        # Pygame
        pygame.init()
        self.screen = pygame.display.set_mode((DEBUG_WIDTH, DEBUG_HEIGHT))
        pygame.display.set_caption("CEREBRO DO BOT - FF1")
        self.font = pygame.font.SysFont("Consolas", 16, bold=True) # Fonte maior
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
        """
        Captura a área do mapa e decide o tamanho.
        O mapa é SEMPRE um quadrado.
        """
        # 1. Tenta capturar o tamanho GRANDE primeiro (267x267)
        # O X de inicio é: (Onde termina - 267)
        start_x_relative = self.rel_right_x - self.SIZE_BIG
        
        abs_top = win_geo["top"] + self.rel_y
        abs_left = win_geo["left"] + start_x_relative
        
        # --- CLAMPING (Segurança) ---
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
            
            # --- 2. LÓGICA DE COR & REDIMENSIONAMENTO ---
            # Verifica se é AZUL (Mundi)
            if img.size > 0:
                avg_color = np.mean(img, axis=(0, 1))
                blue, green, red = avg_color
                
                # É azul predominante?
                is_blue_map = (blue > red) and (blue > green) and (blue > 40)
                
                if is_blue_map:
                    self.current_map_mode = "MUNDI (267px)"
                    return img # Retorna quadrado 267x267
                else:
                    self.current_map_mode = "CIDADE (187px)"
                    
                    # --- RECORTE PARA QUADRADO MENOR ---
                    # Se o mapa diminuiu, ele cola na direita (1205).
                    # Então pegamos os últimos 187 pixels da largura e os primeiros 187 da altura (Top-Right Alignment)
                    
                    diff = self.SIZE_BIG - self.SIZE_SMALL # 80 pixels
                    
                    # Checagem de segurança pra não cortar array vazio
                    if img.shape[0] > self.SIZE_SMALL and img.shape[1] > diff:
                        # Recorta: [0 até 187 (Altura), 80 até 267 (Largura)]
                        # Isso garante que pegamos o canto SUPERIOR DIREITO da captura original
                        return img[0:self.SIZE_SMALL, diff:] 
                    else:
                        return img

        except Exception as e:
            return None
        return None

    def detect_dialogue_bubble(self, win_geo):
        # Detecta diálogo no centro
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

    def generate_matrix(self, img):
        if img is None: return None, None
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        # Se for cidade (não azul), a parede não é necessariamente azul.
        # Mas vamos manter a lógica base por enquanto.
        mask = cv2.inRange(hsv, np.array([90, 50, 50]), np.array([150, 255, 255]))
        return (mask > 0).astype(int), mask

    def handle_calibration_input(self):
        keys = pygame.key.get_pressed()
        speed = 5 if (keys[pygame.K_LSHIFT]) else 1
        
        # Ajusta o PONTO FINAL (1205)
        if keys[pygame.K_LEFT]:  self.rel_right_x -= speed
        if keys[pygame.K_RIGHT]: self.rel_right_x += speed
        
        # Ajusta Y
        if keys[pygame.K_UP]:    self.rel_y -= speed
        if keys[pygame.K_DOWN]:  self.rel_y += speed

    def draw_dashboard(self, minimap, mask_debug, status_text, is_dialogue=False):
        for event in pygame.event.get():
            if event.type == pygame.QUIT: self.running = False
            if event.type == pygame.MOUSEBUTTONDOWN and self.matrix is not None:
                mx, my = pygame.mouse.get_pos()
                # Clique relativo à posição da imagem (que começa em 20, 80)
                # Como a imagem muda de tamanho, o clique tem que acompanhar
                current_w = minimap.shape[1] if minimap is not None else self.SIZE_BIG
                
                rx, ry = mx - 20, my - 80
                if 0 <= rx < current_w and 0 <= ry < current_w:
                    self.target_pos = (rx, ry)

        self.screen.fill(BG_COLOR)
        
        # --- DESENHO DO MINIMAPA (REDIMENSIONA VISUALMENTE) ---
        current_h = 0
        if minimap is not None:
            # Converte BGR -> RGB e Transpõe
            rgb = cv2.cvtColor(minimap, cv2.COLOR_BGR2RGB)
            rgb = np.transpose(rgb, (1, 0, 2))
            surf = pygame.surfarray.make_surface(rgb)
            
            # ATENÇÃO: Aqui desenhamos 1:1. 
            # Se for 267, desenha 267. Se for 187, desenha 187.
            self.screen.blit(surf, (20, 80))
            
            # Desenha borda no tamanho exato da imagem atual
            current_w = minimap.shape[1]
            current_h = minimap.shape[0]
            pygame.draw.rect(self.screen, (255, 255, 255), (20, 80, current_w, current_h), 2)
            
            # Texto indicando o tamanho visual
            dim_text = f"Tam: {current_w}x{current_h}"
            self.screen.blit(self.font.render(dim_text, True, (200, 200, 200)), (20, 60))

        # --- DESENHO DA MATRIZ (Lado a Lado) ---
        if mask_debug is not None:
            mask_rgb = cv2.cvtColor(mask_debug, cv2.COLOR_GRAY2RGB)
            mask_rgb = np.transpose(mask_rgb, (1, 0, 2))
            surf_m = pygame.surfarray.make_surface(mask_rgb)
            
            # Desenha a matriz ao lado do mapa
            offset_x = 20 + (minimap.shape[1] if minimap is not None else 270) + 20
            self.screen.blit(surf_m, (offset_x, 80))
            pygame.draw.rect(self.screen, (0, 255, 255), (offset_x, 80, mask_debug.shape[1], mask_debug.shape[0]), 1)
            self.screen.blit(self.font.render("Visão Lógica", True, (0, 255, 255)), (offset_x, 60))

        # --- COORDENADAS E STATUS (Bem visível) ---
        c = (255, 50, 50) if is_dialogue else (0, 255, 0)
        self.screen.blit(self.font.render(f"STATUS: {status_text}", True, c), (20, 20))
        
        # COORDENADAS CRÍTICAS
        coord_color = (255, 255, 0)
        coords_str = f"X_FIM (Setas): {self.rel_right_x} | Y_TOPO: {self.rel_y}"
        self.screen.blit(self.font.render(coords_str, True, coord_color), (20, 400))
        
        mode_color = (100, 200, 255) if "MUNDI" in self.current_map_mode else (255, 180, 100)
        self.screen.blit(self.font.render(f"MODO: {self.current_map_mode}", True, mode_color), (20, 430))

        self.screen.blit(self.font.render(f"Target: {self.target_pos}", True, (255, 0, 255)), (20, 460))
        pygame.display.flip()

    def get_move(self):
        if not self.target_pos or not self.current_pos: return None
        dx, dy = self.target_pos[0] - self.current_pos[0], self.target_pos[1] - self.current_pos[1]
        if abs(dx) < 5 and abs(dy) < 5: return None
        return ('right' if dx > 0 else 'left') if abs(dx) > abs(dy) else ('down' if dy > 0 else 'up')

    def run(self):
        print("🎮 BOT RODANDO - Coordenadas visíveis no painel!")
        while self.running:
            self.handle_calibration_input()
            win = self.get_window_geometry()
            
            if not win:
                self.draw_dashboard(None, None, "AGUARDANDO JOGO...", False)
                time.sleep(0.5)
                continue

            minimap = self.capture_smart_map(win)
            if minimap is None: continue

            self.current_pos = self.find_player(minimap)
            self.matrix, mask_debug = self.generate_matrix(minimap)
            
            if self.current_pos: cv2.circle(minimap, self.current_pos, 5, (0, 255, 0), -1)
            if self.target_pos: cv2.circle(minimap, self.target_pos, 5, (255, 0, 255), -1)

            is_chat = self.detect_dialogue_bubble(win)
            if is_chat:
                pyautogui.press('enter')
            else:
                move = self.get_move()
                if move and (time.time() - self.last_move_time > self.move_interval):
                    pyautogui.keyDown(move); time.sleep(0.05); pyautogui.keyUp(move)
                    self.last_move_time = time.time()

            self.draw_dashboard(minimap, mask_debug, "DIALOGO" if is_chat else "RODANDO", is_chat)
            self.clock.tick(30)
        pygame.quit()

if __name__ == "__main__":
    bot = FinalFantasyBot()
    bot.run()