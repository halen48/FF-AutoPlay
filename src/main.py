import time
import cv2
import numpy as np
import mss
import pyautogui
import random
import subprocess
import re
import pygame

# --- CONFIGURAÇÕES GERAIS ---
WINDOW_NAME_PART = "Final Fantasy"
DEBUG_WIDTH = 800
DEBUG_HEIGHT = 450 # Aumentei um pouco
BG_COLOR = (20, 20, 20)

class FinalFantasyBot:
    def __init__(self):
        self.sct = mss.mss()
        self.current_pos = None
        self.target_pos = None
        self.matrix = None
        
        # --- COORDENADAS DINÂMICAS (Para ajuste ao vivo) ---
        # Começando com o seu chute inicial
        self.map_x = 949
        self.map_y = 36
        self.map_w = 267
        self.map_h = 267
        
        self.last_move_time = 0
        self.move_interval = 0.1

        # --- PYGAME SETUP ---
        pygame.init()
        self.screen = pygame.display.set_mode((DEBUG_WIDTH, DEBUG_HEIGHT))
        pygame.display.set_caption("CEREBRO DO BOT - Calibracao Manual")
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

    def capture_minimap(self, win_geo):
        # Captura a janela inteira primeiro
        full_img = np.array(self.sct.grab(win_geo))
        full_img = cv2.cvtColor(full_img, cv2.COLOR_BGRA2BGR)
        
        h, w, _ = full_img.shape
        
        # Usa as variáveis da classe (que vamos mudar com teclado)
        x, y = self.map_x, self.map_y
        w_rect, h_rect = self.map_w, self.map_h
        
        # Validação simples para não crashar se sair da tela
        if x < 0: self.map_x = 0
        if y < 0: self.map_y = 0
        if x + w_rect > w: w_rect = w - x
        if y + h_rect > h: h_rect = h - y
        
        crop = full_img[y:y+h_rect, x:x+w_rect]
        return crop

    def find_player(self, img):
        if img.size == 0: return None
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
        if img.size == 0: return None, None
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        lower_blue = np.array([90, 50, 50])
        upper_blue = np.array([150, 255, 255])
        mask_water = cv2.inRange(hsv, lower_blue, upper_blue)
        matrix = (mask_water > 0).astype(int)
        return matrix, mask_water

    def handle_calibration_input(self):
        """Mágica da Calibração: Teclado controla a área de captura"""
        keys = pygame.key.get_pressed()
        changed = False
        
        # SETAS = Movem a Posição (X, Y)
        if keys[pygame.K_LEFT]:  self.map_x -= 1; changed = True
        if keys[pygame.K_RIGHT]: self.map_x += 1; changed = True
        if keys[pygame.K_UP]:    self.map_y -= 1; changed = True
        if keys[pygame.K_DOWN]:  self.map_y += 1; changed = True
        
        # WASD = Redimensionam (Largura, Altura)
        if keys[pygame.K_d]: self.map_w += 1; changed = True
        if keys[pygame.K_a]: self.map_w -= 1; changed = True
        if keys[pygame.K_s]: self.map_h += 1; changed = True
        if keys[pygame.K_w]: self.map_h -= 1; changed = True
        
        if changed:
            print(f"🔧 AJUSTE: X={self.map_x}, Y={self.map_y}, W={self.map_w}, H={self.map_h}")

    def draw_dashboard(self, minimap, mask_debug, status_text):
        for event in pygame.event.get():
            if event.type == pygame.QUIT: self.running = False
            # Clique define objetivo
            if event.type == pygame.MOUSEBUTTONDOWN and self.matrix is not None:
                mx, my = pygame.mouse.get_pos()
                rel_x, rel_y = mx - 20, my - 60
                scale_w = minimap.shape[1] / 250
                scale_h = minimap.shape[0] / 250
                final_x = int(rel_x * scale_w)
                final_y = int(rel_y * scale_h)
                if 0 <= final_x < minimap.shape[1] and 0 <= final_y < minimap.shape[0]:
                    self.target_pos = (final_x, final_y)

        self.screen.fill(BG_COLOR)
        
        # Desenha a Visão da Câmera
        if minimap is not None and minimap.size > 0:
            rgb = cv2.cvtColor(minimap, cv2.COLOR_BGR2RGB)
            rgb = np.transpose(rgb, (1, 0, 2))
            surf = pygame.surfarray.make_surface(rgb)
            surf = pygame.transform.scale(surf, (250, 250))
            self.screen.blit(surf, (20, 60))
            pygame.draw.rect(self.screen, (255, 255, 255), (20, 60, 250, 250), 2)
            self.screen.blit(self.font.render("VISAO (Use Setas/WASD para ajustar)", True, (200, 200, 200)), (20, 40))

        # Desenha a Matriz (Debug)
        if mask_debug is not None and mask_debug.size > 0:
            mask_rgb = cv2.cvtColor(mask_debug, cv2.COLOR_GRAY2RGB)
            mask_rgb = np.transpose(mask_rgb, (1, 0, 2))
            surf_m = pygame.surfarray.make_surface(mask_rgb)
            surf_m = pygame.transform.scale(surf_m, (250, 250))
            self.screen.blit(surf_m, (300, 60))
            pygame.draw.rect(self.screen, (0, 255, 255), (300, 60, 250, 250), 1)
            self.screen.blit(self.font.render("MATRIZ LOGICA", True, (0, 255, 255)), (300, 40))

        # Status Texto
        self.screen.blit(self.font.render(f"AJUSTE MANUAL: X={self.map_x} Y={self.map_y} W={self.map_w} H={self.map_h}", True, (255, 255, 0)), (20, 360))
        
        pos_txt = f"Player: {self.current_pos}"
        tar_txt = f"Target: {self.target_pos}"
        self.screen.blit(self.font.render(pos_txt, True, (0, 255, 0)), (20, 390))
        self.screen.blit(self.font.render(tar_txt, True, (255, 0, 255)), (20, 410))

        pygame.display.flip()

    def decide_move(self):
        # Lógica mantida...
        if self.target_pos:
            tx, ty = self.target_pos
            px, py = self.current_pos
            if abs(tx - px) < 5 and abs(ty - py) < 5:
                self.target_pos = None
                return None
            dx = tx - px
            dy = ty - py
            if abs(dx) > abs(dy): return 'right' if dx > 0 else 'left'
            else: return 'down' if dy > 0 else 'up'
        else:
            if random.random() < 0.05:
                return random.choice(['up', 'down', 'left', 'right'])
            return None

    def run(self):
        print("🎮 BOT INICIADO - Calibre usando SETAS e WASD!")
        
        while self.running:
            self.handle_calibration_input() # <--- Verifica teclas
            
            win = self.get_window_geometry()
            if not win:
                self.draw_dashboard(None, None, "AGUARDANDO FINAL FANTASY...")
                time.sleep(0.5)
                continue

            try:
                minimap = self.capture_minimap(win)
                if minimap is None or minimap.size == 0: continue

                self.current_pos = self.find_player(minimap)
                self.matrix, mask_debug = self.generate_matrix(minimap)
                
                # Visual Debug
                if self.current_pos:
                    cv2.circle(minimap, self.current_pos, 5, (0, 255, 0), -1)
                if self.target_pos:
                    cv2.circle(minimap, self.target_pos, 5, (255, 0, 255), -1)
                    if self.current_pos:
                         cv2.line(minimap, self.current_pos, self.target_pos, (255, 255, 0), 1)

                if self.current_pos:
                    move = self.decide_move()
                    if move and (time.time() - self.last_move_time > self.move_interval):
                        # Descomente para andar de verdade
                        # pyautogui.keyDown(move); time.sleep(0.05); pyautogui.keyUp(move)
                        self.last_move_time = time.time()

                self.draw_dashboard(minimap, mask_debug, "RODANDO")
                self.clock.tick(30)

            except Exception as e:
                print(f"Erro: {e}")
                
        pygame.quit()

if __name__ == "__main__":
    bot = FinalFantasyBot()
    bot.run()