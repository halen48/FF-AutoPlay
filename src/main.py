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
DEBUG_WIDTH = 800
DEBUG_HEIGHT = 450
BG_COLOR = (20, 20, 20)
WINDOW_NAME_PART = "Final Fantasy"

class FinalFantasyBot:
    def __init__(self):
        self.sct = mss.mss()
        self.current_pos = None
        self.target_pos = None
        self.matrix = None
        
        # --- CONFIGURAÇÃO INICIAL (Ajuste Fino) ---
        # "X tem que diminuir": Se 1215 for muito para a direita, 
        # use as setas para reduzir esse valor na hora.
        self.map_offset_x = 1215 
        self.map_offset_y = -20  
        
        # Tamanhos Padrão
        self.SIZE_BIG = 267   # Mapa Mundi (Azul)
        self.SIZE_SMALL = 187 # Cidade (Bege)
        self.current_size = self.SIZE_BIG # Começa assumindo grande
        
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

    def capture_and_resize(self, win_geo):
        """
        Versão BLINDADA: Impede que o MSS tente capturar pixels fora do monitor
        (o que causa o erro XGetImage failed).
        """
        # 1. Calcula as coordenadas ideais
        top = win_geo["top"] + self.map_offset_y
        left = win_geo["left"] + self.map_offset_x
        width = self.SIZE_BIG
        height = self.SIZE_BIG
        
        # 2. Obtém limites do monitor principal (Monitor 1)
        # sct.monitors[0] é "todos juntos", monitors[1] é o principal.
        # Vamos assumir o monitor 1 para segurança.
        screen_w = self.sct.monitors[1]["width"]
        screen_h = self.sct.monitors[1]["height"]

        # 3. CLAMPING (Segurança)
        # Se for negativo, vira 0.
        if top < 0: top = 0
        if left < 0: left = 0
        
        # Se estourar a largura/altura da tela, ajusta o tamanho
        if left + width > screen_w:
            width = screen_w - left
        if top + height > screen_h:
            height = screen_h - top
            
        # Se o ajuste deixou a janela com tamanho inválido (<=0), aborta
        if width <= 0 or height <= 0:
            return None

        # 4. Monta o dicionário seguro
        monitor = {
            "top": int(top),
            "left": int(left),
            "width": int(width),
            "height": int(height)
        }

        try:
            sct_img = self.sct.grab(monitor)
            img = np.array(sct_img)
            img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
            
            # --- DETECÇÃO DE TAMANHO (Mantida) ---
            h_img, w_img, _ = img.shape
            
            # Se a imagem for muito pequena (cortada pela borda), nem tenta analisar
            if h_img < 50 or w_img < 50: return img

            # Pega amostra do centro (cuidado com limites de novo)
            cy, cx = h_img // 2, w_img // 2
            center_sample = img[max(0, cy-20):min(h_img, cy+20), max(0, cx-20):min(w_img, cx+20)]
            
            if center_sample.size > 0:
                avg_color = np.mean(center_sample, axis=(0, 1))
                blue, green, red = avg_color
                
                # Lógica Azul vs Bege
                is_blue_map = (blue > red) and (blue > 50)
                
                if is_blue_map:
                    self.current_size = self.SIZE_BIG
                    return img
                else:
                    self.current_size = self.SIZE_SMALL
                    # Recorte de segurança
                    crop_h = min(self.SIZE_SMALL, h_img)
                    crop_w = min(self.SIZE_SMALL, w_img)
                    return img[0:crop_h, 0:crop_w]
            
            return img

        except mss.exception.ScreenShotError:
            print("⚠️ ERRO MSS: Tentou capturar fora da tela! Ajuste a janela do jogo.")
            return None
        
    def find_player(self, img):
        if img is None or img.size == 0: return None
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        # Range vermelho da seta
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
        if img is None or img.size == 0: return None, None
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        
        # Se estamos no mapa PEQUENO (Bege), a parede geralmente é escura ou contorno
        # Se estamos no mapa GRANDE (Azul), a parede é o mar (azul)
        
        if self.current_size == self.SIZE_BIG:
            # Lógica Mar (Azul é parede)
            lower = np.array([90, 50, 50])
            upper = np.array([150, 255, 255])
        else:
            # Lógica Cidade (Bege) - Aqui é mais chato, vamos assumir
            # que o que for MUITO escuro é obstáculo ou o que for verde é chão?
            # Por enquanto usando a mesma lógica pra testar visualmente
            lower = np.array([0, 0, 0]) # Placeholder
            upper = np.array([180, 255, 30]) # Coisas escuras
            
        mask = cv2.inRange(hsv, lower, upper)
        matrix = (mask > 0).astype(int)
        return matrix, mask

    def handle_input(self):
        keys = pygame.key.get_pressed()
        speed = 5 if (keys[pygame.K_LSHIFT]) else 1

        # Calibração Manual do Offset X/Y
        if keys[pygame.K_LEFT]:  self.map_offset_x -= speed
        if keys[pygame.K_RIGHT]: self.map_offset_x += speed
        if keys[pygame.K_UP]:    self.map_offset_y -= speed
        if keys[pygame.K_DOWN]:  self.map_offset_y += speed

    def draw_dashboard(self, minimap, mask_debug):
        for event in pygame.event.get():
            if event.type == pygame.QUIT: self.running = False

        self.screen.fill(BG_COLOR)
        
        # --- Visão Câmera ---
        if minimap is not None and minimap.size > 0:
            rgb = cv2.cvtColor(minimap, cv2.COLOR_BGR2RGB)
            rgb = np.transpose(rgb, (1, 0, 2))
            surf = pygame.surfarray.make_surface(rgb)
            surf = pygame.transform.scale(surf, (250, 250))
            self.screen.blit(surf, (20, 60))
            pygame.draw.rect(self.screen, (255, 255, 255), (20, 60, 250, 250), 2)
            
            # Info do tamanho detectado
            size_txt = "GRANDE (267)" if self.current_size == self.SIZE_BIG else "PEQUENO (187)"
            color_sz = (0, 255, 255) if self.current_size == self.SIZE_BIG else (255, 200, 150)
            self.screen.blit(self.font.render(f"DETECTADO: {size_txt}", True, color_sz), (20, 320))

        # --- Matriz Lógica ---
        if mask_debug is not None and mask_debug.size > 0:
            mask_rgb = cv2.cvtColor(mask_debug, cv2.COLOR_GRAY2RGB)
            mask_rgb = np.transpose(mask_rgb, (1, 0, 2))
            surf_m = pygame.surfarray.make_surface(mask_rgb)
            surf_m = pygame.transform.scale(surf_m, (250, 250))
            self.screen.blit(surf_m, (300, 60))
            pygame.draw.rect(self.screen, (0, 255, 255), (300, 60, 250, 250), 1)

        # Infos
        info = f"OFFSET: X={self.map_offset_x} Y={self.map_offset_y}"
        self.screen.blit(self.font.render(info, True, (255, 255, 0)), (20, 360))
        
        pos_txt = f"Player: {self.current_pos}"
        self.screen.blit(self.font.render(pos_txt, True, (0, 255, 0)), (20, 390))

        pygame.display.flip()

    def run(self):
        print("🎮 BOT INICIADO - Auto-Detecção de Tamanho Ativa!")
        
        while self.running:
            self.handle_input()
            win = self.get_window_geometry()
            
            if not win:
                # Standby...
                time.sleep(0.5)
                continue

            try:
                # 1. Captura Inteligente (Decide tamanho sozinho)
                minimap = self.capture_and_resize(win)
                
                # 2. Processa
                self.current_pos = self.find_player(minimap)
                self.matrix, mask_debug = self.generate_matrix(minimap)
                
                if self.current_pos:
                    cv2.circle(minimap, self.current_pos, 5, (0, 255, 0), -1)

                # 3. Desenha
                self.draw_dashboard(minimap, mask_debug)
                self.clock.tick(30)

            except Exception as e:
                print(f"Erro: {e}")
                
        pygame.quit()

if __name__ == "__main__":
    bot = FinalFantasyBot()
    bot.run()