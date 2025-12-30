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
DEBUG_HEIGHT = 500
BG_COLOR = (20, 20, 20)

class FinalFantasyBot:
    def __init__(self):
        self.sct = mss.mss()
        self.current_pos = None
        self.target_pos = None
        self.matrix = None
        
        # --- CONFIGURAÇÃO DE ALINHAMENTO ---
        # AGORA USAMOS O CANTO DIREITO COMO REFERÊNCIA
        self.rel_right_x = 1215 # Onde o mapa TERMINA (pixel relativo à esquerda da janela)
        self.rel_y = -20        # Ajuste vertical (Barra de título)
        
        # Tamanhos
        self.SIZE_BIG = 267     # Mapa Mundi
        self.SIZE_SMALL = 187   # Cidade
        
        self.current_map_mode = "DESCONHECIDO" # Para mostrar na tela

        self.last_move_time = 0
        self.move_interval = 0.1

        # Pygame
        pygame.init()
        self.screen = pygame.display.set_mode((DEBUG_WIDTH, DEBUG_HEIGHT))
        pygame.display.set_caption("CEREBRO DO BOT - Auto Size")
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
        """
        Captura sempre 267px alinhados à direita.
        Analisa a cor. Se não for azul, corta para 187px.
        """
        # 1. Definir a área de captura MAXIMA (Mundi)
        # O X inicial é: (Onde o mapa termina) - (Largura Máxima)
        rel_start_x = self.rel_right_x - self.SIZE_BIG
        
        abs_top = win_geo["top"] + self.rel_y
        abs_left = win_geo["left"] + rel_start_x
        
        # --- BLINDAGEM (Evita Crash fora da tela) ---
        monitor_idx = 1 if len(self.sct.monitors) > 1 else 0
        screen_w = self.sct.monitors[monitor_idx]["width"]
        screen_h = self.sct.monitors[monitor_idx]["height"]

        if abs_top < 0: abs_top = 0
        if abs_left < 0: abs_left = 0
        
        width = self.SIZE_BIG
        height = self.SIZE_BIG # Altura é sempre 267 segundo a lógica quadrada?
        
        if abs_left + width > screen_w: width = screen_w - abs_left
        if abs_top + height > screen_h: height = screen_h - abs_top

        if width <= 0 or height <= 0: return None

        region = {"top": int(abs_top), "left": int(abs_left), "width": int(width), "height": int(height)}
        
        try:
            # Captura a imagem bruta
            sct_img = self.sct.grab(region)
            img = np.array(sct_img)
            img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
            
            # --- 2. DETECÇÃO DE COR (AZUL OU NÃO?) ---
            # Calcula a média de cor da imagem inteira capturada
            if img.size > 0:
                avg_color = np.mean(img, axis=(0, 1))
                blue, green, red = avg_color
                
                # Critério: O Azul é predominante?
                # Adicionei um blue > 40 pra garantir que não é tudo preto
                is_blue_dominant = (blue > red) and (blue > green) and (blue > 40)
                
                if is_blue_dominant:
                    self.current_map_mode = f"MUNDI ({self.SIZE_BIG})"
                    return img # Retorna os 267px completos
                else:
                    self.current_map_mode = f"CIDADE ({self.SIZE_SMALL})"
                    
                    # --- 3. RECORTE (CROP) ---
                    # Se não é azul, o mapa real são só os 187px da DIREITA.
                    # Math: Imagem tem largura 267. Queremos os ultimos 187.
                    # Start index = 267 - 187 = 80
                    
                    crop_start = self.SIZE_BIG - self.SIZE_SMALL
                    
                    # Verificação de segurança caso a captura tenha sido cortada pela borda da tela
                    if img.shape[1] > crop_start:
                        # Corta mantendo a altura total, mas pegando só a direita
                        return img[:, crop_start:] 
                    else:
                        return img

        except Exception as e:
            print(f"Erro captura: {e}")
            return None
        return None

    def detect_dialogue_bubble(self, win_geo):
        cx = win_geo["left"] + (win_geo["width"] // 2) - 200
        cy = win_geo["top"] + (win_geo["height"] // 2) - 250
        if cx < 0: cx = 0
        if cy < 0: cy = 0
        
        region = {"top": int(cy), "left": int(cx), "width": 400, "height": 450}
        try:
            img = np.array(self.sct.grab(region))
            img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
            lower_white = np.array([240, 240, 240])
            upper_white = np.array([255, 255, 255])
            mask = cv2.inRange(img, lower_white, upper_white)
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
        
        # Ajusta a máscara dependendo do mapa?
        # Geralmente no mapa mundi (azul), parede = agua (azul)
        # Na cidade, precisamos ver o que é parede. Por enquanto mantendo detecção de azul/preto
        mask = cv2.inRange(hsv, np.array([90, 50, 50]), np.array([150, 255, 255]))
        return (mask > 0).astype(int), mask

    def handle_calibration_input(self):
        keys = pygame.key.get_pressed()
        speed = 5 if (keys[pygame.K_LSHIFT]) else 1
        
        # Ajusta ONDE O MAPA TERMINA (1215)
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
                rx, ry = mx - 20, my - 60
                
                # Ajusta escala do clique baseado no tamanho atual do mapa (267 ou 187)
                current_w = minimap.shape[1] if minimap is not None else 267
                current_h = minimap.shape[0] if minimap is not None else 267
                
                if 0 <= rx < 250 and 0 <= ry < 250:
                    self.target_pos = (int(rx*(current_w/250)), int(ry*(current_h/250)))

        self.screen.fill(BG_COLOR)
        
        if minimap is not None:
            # Mostra o minimapa (já recortado se for cidade)
            surf = pygame.surfarray.make_surface(np.transpose(cv2.cvtColor(minimap, cv2.COLOR_BGR2RGB), (1, 0, 2)))
            self.screen.blit(pygame.transform.scale(surf, (250, 250)), (20, 60))
            pygame.draw.rect(self.screen, (255, 255, 255), (20, 60, 250, 250), 2)

        if mask_debug is not None:
            surf_m = pygame.surfarray.make_surface(np.transpose(cv2.cvtColor(mask_debug, cv2.COLOR_GRAY2RGB), (1, 0, 2)))
            self.screen.blit(pygame.transform.scale(surf_m, (250, 250)), (300, 60))
            pygame.draw.rect(self.screen, (0, 255, 255), (300, 60, 250, 250), 1)

        c = (255, 50, 50) if is_dialogue else (0, 255, 0)
        self.screen.blit(self.font.render(f"STATUS: {status_text}", True, c), (20, 20))
        
        # Info do Modo (Mundi/Cidade)
        map_color = (100, 200, 255) if "MUNDI" in self.current_map_mode else (255, 200, 100)
        self.screen.blit(self.font.render(f"MAPA: {self.current_map_mode}", True, map_color), (300, 20))

        self.screen.blit(self.font.render(f"FIM MAPA (X): {self.rel_right_x} | Y: {self.rel_y}", True, (255, 255, 0)), (20, 360))
        self.screen.blit(self.font.render(f"Target: {self.target_pos}", True, (255, 0, 255)), (20, 390))
        pygame.display.flip()

    def get_move(self):
        if not self.target_pos or not self.current_pos: return None
        dx, dy = self.target_pos[0] - self.current_pos[0], self.target_pos[1] - self.current_pos[1]
        if abs(dx) < 5 and abs(dy) < 5: return None
        return ('right' if dx > 0 else 'left') if abs(dx) > abs(dy) else ('down' if dy > 0 else 'up')

    def run(self):
        print("🎮 BOT RODANDO - Ajuste o FIM DO MAPA (X) com setas!")
        while self.running:
            self.handle_calibration_input()
            win = self.get_window_geometry()
            
            if not win:
                self.draw_dashboard(None, None, "AGUARDANDO JOGO...")
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