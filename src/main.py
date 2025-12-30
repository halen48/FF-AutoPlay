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
DEBUG_WIDTH = 900
DEBUG_HEIGHT = 600
BG_COLOR = (30, 30, 35)

class FinalFantasyBot:
    def __init__(self):
        self.sct = mss.mss()
        self.current_pos = None
        
        # --- OFFSET E TAMANHO ---
        self.rel_right_x = 1205
        self.rel_y = -20
        self.SIZE_BIG = 267     
        self.SIZE_SMALL = 187   
        
        # --- SLAM / MEMÓRIA ---
        self.MAP_SIZE = 512
        self.visited_canvas = np.zeros((self.MAP_SIZE, self.MAP_SIZE), dtype=np.uint8)
        self.cam_global_x = self.MAP_SIZE // 2
        self.cam_global_y = self.MAP_SIZE // 2

        # --- VARIÁVEIS DE NAVEGAÇÃO ---
        self.current_direction = 'down' 
        self.last_map_frame = None
        self.stuck_counter = 0 # Contador de frames parados

        # Pygame
        pygame.init()
        self.screen = pygame.display.set_mode((DEBUG_WIDTH, DEBUG_HEIGHT))
        pygame.display.set_caption("CEREBRO DO BOT - Long Press Mode")
        self.font = pygame.font.SysFont("Consolas", 14, bold=True)
        self.clock = pygame.time.Clock()
        self.running = True

    def get_window_geometry(self):
        try:
            aid = subprocess.check_output(['xdotool', 'getactivewindow']).decode().strip()
            name = subprocess.check_output(['xdotool', 'getwindowname', aid]).decode().strip()
            # SE A JANELA ATIVA NÃO FOR O JOGO, RETORNA NONE IMEDIATAMENTE
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
        """Solta tudo imediatamente por segurança"""
        for k in ['up', 'down', 'left', 'right', 'enter']:
            pyautogui.keyUp(k)

    def capture_smart_map(self, win_geo):
        # ... (Lógica de captura mantida idêntica à anterior) ...
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
                if is_blue_map: return img
                else:
                    diff = self.SIZE_BIG - self.SIZE_SMALL
                    if img.shape[0] > self.SIZE_SMALL and img.shape[1] > diff:
                        return img[0:self.SIZE_SMALL, diff:] 
                    else: return img
        except: return None
        return None

    def detect_movement_and_update_slam(self, frame):
        """ Retorna TRUE se houve movimento visual no mapa """
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        moved = False
        
        if self.last_map_frame is not None and self.last_map_frame.shape == gray.shape:
            # Diferença absoluta
            score = cv2.absdiff(self.last_map_frame, gray)
            non_zero = np.count_nonzero(score > 10) 
            
            # Se mudou mais de 50 pixels, consideramos movimento
            if non_zero > 50:
                moved = True
                self.stuck_counter = 0
            else:
                moved = False
                self.stuck_counter += 1

            if moved:
                h, w = gray.shape
                margin = 30
                patch = self.last_map_frame[margin:h-margin, margin:w-margin]
                res = cv2.matchTemplate(gray, patch, cv2.TM_CCOEFF_NORMED)
                _, _, _, max_loc = cv2.minMaxLoc(res)
                
                dx = max_loc[0] - margin
                dy = max_loc[1] - margin
                
                self.cam_global_x -= dx
                self.cam_global_y -= dy
                self.cam_global_x = max(0, min(self.cam_global_x, self.MAP_SIZE-1))
                self.cam_global_y = max(0, min(self.cam_global_y, self.MAP_SIZE-1))

        self.last_map_frame = gray
        
        # Pinta rastro
        if self.cam_global_x < self.MAP_SIZE and self.cam_global_y < self.MAP_SIZE:
            cv2.circle(self.visited_canvas, (int(self.cam_global_x), int(self.cam_global_y)), 3, 255, -1)

        return moved

    def detect_dialogue_bubble(self, win_geo):
        # ... (Mantida lógica de detecção de bolha branca) ...
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
        
        if minimap is not None:
            rgb = cv2.cvtColor(minimap, cv2.COLOR_BGR2RGB)
            surf = pygame.surfarray.make_surface(np.transpose(rgb, (1, 0, 2)))
            self.screen.blit(surf, (20, 80))
            pygame.draw.rect(self.screen, (255, 255, 255), (20, 80, minimap.shape[1], minimap.shape[0]), 2)

        # Mapa Global (Direita)
        preview_size = 300
        canvas_rgb = cv2.cvtColor(self.visited_canvas, cv2.COLOR_GRAY2RGB)
        cv2.circle(canvas_rgb, (int(self.cam_global_x), int(self.cam_global_y)), 5, (255, 0, 0), -1)
        
        surf_global = pygame.surfarray.make_surface(np.transpose(canvas_rgb, (1, 0, 2)))
        surf_global = pygame.transform.scale(surf_global, (preview_size, preview_size))
        
        off_x = 350
        self.screen.blit(surf_global, (off_x, 80))
        pygame.draw.rect(self.screen, (0, 255, 0), (off_x, 80, preview_size, preview_size), 1)

        c = (0, 255, 0) if "ANDANDO" in status_text else (255, 50, 50)
        self.screen.blit(self.font.render(f"STATUS: {status_text}", True, c), (20, 20))
        self.screen.blit(self.font.render(f"OFFSET X: {self.rel_right_x}", True, (255, 255, 0)), (20, 400))
        
        pygame.display.flip()

    def run(self):
        print("🎮 BOT EXPLORADOR - LONG PRESS ATIVO")
        directions = ['up', 'right', 'down', 'left']

        while self.running:
            self.handle_calibration_input()
            
            # 1. VERIFICAÇÃO CRÍTICA DE FOCO (SEGURANÇA)
            win = self.get_window_geometry()
            if not win:
                self.release_all_keys()
                self.draw_dashboard(None, "PAUSADO (Sem foco)")
                time.sleep(0.5)
                continue # Pula o resto do loop se não tiver foco

            # Captura inicial
            minimap = self.capture_smart_map(win)
            if minimap is None: continue

            # Se detectar diálogo, trata rápido
            if self.detect_dialogue_bubble(win):
                self.release_all_keys()
                pyautogui.press('enter')
                self.draw_dashboard(minimap, "DIALOGO")
                continue

            # --- LÓGICA DE MOVIMENTO CONTÍNUO ---
            direction = self.current_direction
            print(f"🏃 Iniciando movimento: {direction}")
            
            # Segura o botão
            pyautogui.keyDown(direction)
            
            start_move_time = time.time()
            collision_detected = False
            
            # LOOP DE MOVIMENTO (Dura até 5s ou até bater)
            while time.time() - start_move_time < 5.0:
                # A cada frame do loop, TEM que checar se ainda tem foco
                # Se o usuário der Alt+Tab NO MEIO do movimento, solta tudo.
                current_win = self.get_window_geometry()
                if not current_win:
                    self.release_all_keys()
                    break

                # Captura novo frame para ver se andou
                current_minimap = self.capture_smart_map(current_win)
                if current_minimap is None: break

                # Atualiza SLAM e verifica se travou
                moved = self.detect_movement_and_update_slam(current_minimap)
                
                # Atualiza UI
                self.draw_dashboard(current_minimap, f"ANDANDO: {direction.upper()}")
                self.clock.tick(30) 

                # LÓGICA DE COLISÃO
                if not moved:
                    # Se não moveu, incrementa contador interno de stuck (já feito no detect)
                    if self.stuck_counter > 5: # ~0.5s parado
                        print("🚫 COLISÃO! Parando movimento.")
                        collision_detected = True
                        break # Sai do loop de 5s
                
                # Verifica diálogo no meio do andar
                if self.detect_dialogue_bubble(current_win):
                    self.release_all_keys()
                    pyautogui.press('enter')
                    break

            # Fim do ciclo de movimento: SOLTA A TECLA
            pyautogui.keyUp(direction)
            
            # Se houve colisão, muda a estratégia
            if collision_detected:
                # Escolhe nova direção (90 ou 270 graus)
                curr_idx = directions.index(self.current_direction)
                turn = random.choice([1, -1])
                new_idx = (curr_idx + turn) % 4
                self.current_direction = directions[new_idx]
                print(f"↪️ Virando para: {self.current_direction}")
                
                # Pequeno recuo ou pausa pra não bugar input
                time.sleep(0.2)

        pygame.quit()

if __name__ == "__main__":
    bot = FinalFantasyBot()
    bot.run()