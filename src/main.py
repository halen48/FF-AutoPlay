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
import math

# --- CONFIGURAÇÕES ---
WINDOW_NAME_PART = "Final Fantasy"
DEBUG_WIDTH = 1000
DEBUG_HEIGHT = 700
BG_COLOR = (30, 30, 35)

# TECLAS
KEY_SQUARE = 'v' 
KEY_CONFIRM = 'enter'

# CORES E LIMIARES
TARGET_CITY_BGR = np.array([148, 203, 244], dtype=np.float32)
CITY_COLOR_THRESHOLD = 150.0  # <--- Atualizado conforme pedido
MAP_CHANGE_THRESHOLD = 0.65 

# ESTADOS DO TILE
TILE_UNKNOWN = 0
TILE_BLOCKED = 1
TILE_WALKABLE = 2

class FinalFantasyBot:
    def __init__(self):
        self.sct = mss.mss()
        
        # ALINHAMENTO
        self.rel_right_x = 1205
        self.rel_y = -20
        self.SIZE_BIG = 267     
        self.SIZE_SMALL = 187   
        
        # MEMÓRIA
        self.maps_memory = {} 
        self.current_map_id = None 
        self.current_map_signature = None
        
        # CANVAS TÁTICO
        self.TOWN_SIZE = 1024
        self.town_grid = np.zeros((self.TOWN_SIZE, self.TOWN_SIZE), dtype=np.uint8)
        self.town_x = self.TOWN_SIZE // 2
        self.town_y = self.TOWN_SIZE // 2
        self.town_grid[self.town_y, self.town_x] = TILE_WALKABLE

        self.WORLD_SIZE = self.SIZE_BIG
        self.world_grid = np.zeros((self.WORLD_SIZE, self.WORLD_SIZE), dtype=np.uint8)
        self.world_x = 0
        self.world_y = 0

        # ESTADO
        self.current_map_mode = "MUNDI"
        self.last_map_frame = None
        self.current_pos = None 
        
        self.debug_similarity = 1.0
        self.debug_color_dist = 0.0
        self.in_battle = False
        
        # NAVEGAÇÃO & PROGRESSO (NOVO)
        self.active_target = None  
        self.distance_history = [] # Guarda as distâncias dos últimos passos
        self.failed_directions = []
        
        self.stuck_counter = 0
        self.map_change_stability_counter = 0

        # Pygame
        pygame.init()
        self.screen = pygame.display.set_mode((DEBUG_WIDTH, DEBUG_HEIGHT))
        pygame.display.set_caption("CEREBRO DO BOT - Anti-Oscillation")
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

    # --- MEMÓRIA E ESTADOS ---
    def get_current_grid_and_pos(self):
        if self.current_map_mode == "CIDADE":
            return self.town_grid, self.town_x, self.town_y
        else:
            return self.world_grid, self.world_x, self.world_y

    def update_tile_state(self, x, y, state):
        grid, _, _ = self.get_current_grid_and_pos()
        h, w = grid.shape
        if 0 <= x < w and 0 <= y < h:
            current_val = grid[y, x]
            if current_val == TILE_UNKNOWN:
                grid[y, x] = state
            elif current_val == TILE_WALKABLE and state == TILE_BLOCKED:
                grid[y, x] = state

    def is_tile_blocked(self, x, y):
        grid, _, _ = self.get_current_grid_and_pos()
        h, w = grid.shape
        if 0 <= x < w and 0 <= y < h:
            return grid[y, x] == TILE_BLOCKED
        return True 

    # --- ROTEAMENTO INTELIGENTE (COM CHECK DE PROGRESSO) ---

    def select_new_target(self):
        if self.current_map_mode == "CIDADE":
            tx = random.randint(50, self.TOWN_SIZE - 50)
            ty = random.randint(50, self.TOWN_SIZE - 50)
        else:
            tx = random.randint(10, self.WORLD_SIZE - 10)
            ty = random.randint(10, self.WORLD_SIZE - 10)
            
        self.active_target = (tx, ty)
        self.failed_directions = [] 
        self.distance_history = [] # Reseta histórico de progresso
        print(f"🎯 NOVO ALVO: {self.active_target}")

    def check_progress(self, current_dist):
        """
        Verifica se estamos avançando ou oscilando.
        Retorna False se estivermos presos num loop.
        """
        self.distance_history.append(current_dist)
        
        # Mantém apenas os últimos 5 registros (histórico curto)
        if len(self.distance_history) > 5:
            self.distance_history.pop(0)
            
        # Só analisa se tivermos pelo menos 3 passos registrados
        if len(self.distance_history) >= 3:
            # Compara a distância de 3 passos atrás com a atual
            # distance_history[0] = Distância há 3 passos (ex: 100px)
            # distance_history[-1] = Distância agora (ex: 99px)
            # Progresso = 1px. Isso é muito pouco.
            
            progress = self.distance_history[0] - self.distance_history[-1]
            
            # Se o progresso for menor que 5 pixels em 3 movimentos -> ESTAGNADO
            if progress < 5:
                print(f"⚠️ ESTAGNADO! Progresso: {progress:.1f}px. Trocando alvo.")
                return False
                
        return True

    def get_next_routing_step(self):
        if not self.active_target:
            self.select_new_target()
            return None

        grid, curr_x, curr_y = self.get_current_grid_and_pos()
        target_x, target_y = self.active_target

        dx = target_x - curr_x
        dy = target_y - curr_y
        dist_now = math.sqrt(dx**2 + dy**2)
        
        # 1. Checa Chegada
        if dist_now < 10:
            print("✅ CHEGOU NO DESTINO!")
            self.select_new_target()
            return None

        # 2. Checa Progresso (Anti-Oscilação)
        if not self.check_progress(dist_now):
            self.select_new_target()
            return None

        # 3. Escolha de Movimento
        moves = [
            ('right', curr_x + 1, curr_y),
            ('left',  curr_x - 1, curr_y),
            ('down',  curr_x,     curr_y + 1),
            ('up',    curr_x,     curr_y - 1)
        ]
        
        # Filtra bloqueados
        valid_moves = []
        for mv, nx, ny in moves:
            if not self.is_tile_blocked(nx, ny) and mv not in self.failed_directions:
                valid_moves.append((mv, nx, ny))
        
        if not valid_moves:
            print("⚠️ BECO SEM SAÍDA. Resetando Alvo.")
            self.select_new_target()
            return None

        # Escolhe o que mais aproxima
        best_move = None
        min_dist = float('inf')

        for mv, nx, ny in valid_moves:
            d = math.sqrt((target_x - nx)**2 + (target_y - ny)**2)
            if d < min_dist:
                min_dist = d
                best_move = mv
        
        return best_move

    # --- VISÃO E COMPARAÇÃO ---
    def check_visual_movement(self, img_before, img_after):
        if img_before is None or img_after is None: return False
        if img_before.shape != img_after.shape: return False
        gray1 = cv2.cvtColor(img_before, cv2.COLOR_BGR2GRAY)
        gray2 = cv2.cvtColor(img_after, cv2.COLOR_BGR2GRAY)
        diff = cv2.absdiff(gray1, gray2)
        return np.count_nonzero(diff > 10) > 50

    # ... (Resto das funções mantidas: find_player, battle, signatures) ...
    def find_player(self, img):
        if img is None: return None
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, np.array([0, 120, 70]), np.array([10, 255, 255])) + \
               cv2.inRange(hsv, np.array([170, 120, 70]), np.array([180, 255, 255]))
        contours, _ = cv2.findContours(mask, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
        if contours:
            c = max(contours, key=cv2.contourArea)
            M = cv2.moments(c)
            if M["m00"] != 0: return (int(M["m10"]/M["m00"]), int(M["m01"]/M["m00"]))
        return (img.shape[1]//2, img.shape[0]//2)

    def check_battle_state(self, win_geo):
        rel_x1, rel_y1 = 75, 565
        rel_x2, rel_y2 = 266, 696
        w, h = rel_x2 - rel_x1, rel_y2 - rel_y1
        region = {"top": int(win_geo["top"] + rel_y1), "left": int(win_geo["left"] + rel_x1), "width": int(w), "height": int(h)}
        try:
            sct_img = self.sct.grab(region)
            img = np.array(sct_img)
            img_bgr = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
            if img_bgr.size > 0:
                avg_bgr = np.mean(img_bgr, axis=(0, 1))
                blue, green, red = avg_bgr
                return (blue > 100) and (blue > red + 40) and (blue > green + 10)
        except: pass
        return False

    def handle_battle(self, win_geo):
        print("⚔️ BATALHA!")
        self.in_battle = True
        self.release_all_keys()
        pyautogui.press(KEY_SQUARE)
        time.sleep(0.5) 
        while True:
            win = self.get_window_geometry()
            if not win: break
            if not self.check_battle_state(win): break
            pyautogui.press(KEY_CONFIRM)
            self.draw_dashboard(None, "EM COMBATE")
            self.clock.tick(10)
        self.in_battle = False
        time.sleep(1.0)

    def get_visual_signature(self, img):
        if img is None: return None
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
        if current_img is None: return False
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
                self.maps_memory[self.current_map_id] = self.town_grid.copy()
            
            new_mode = self.classify_map_type(current_img)
            new_id = hashlib.md5(current_img.tobytes()).hexdigest()[:8]
            self.town_grid = np.zeros((self.TOWN_SIZE, self.TOWN_SIZE), dtype=np.uint8)
            self.town_x = self.TOWN_SIZE // 2
            self.town_y = self.TOWN_SIZE // 2
            self.town_grid[self.town_y, self.town_x] = TILE_WALKABLE
            
            self.current_map_mode = new_mode
            self.current_map_id = new_id
            self.current_map_signature = new_sig
            self.map_change_stability_counter = 0
            self.select_new_target() # Reseta alvo no mapa novo
            time.sleep(1.0)
            return True
        return False

    def capture_smart_map(self, win_geo):
        start_x_relative = self.rel_right_x - self.SIZE_BIG
        abs_top = win_geo["top"] + self.rel_y
        abs_left = win_geo["left"] + start_x_relative
        width = self.SIZE_BIG; height = self.SIZE_BIG 
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

    def handle_calibration_input(self):
        keys = pygame.key.get_pressed()
        speed = 5 if (keys[pygame.K_LSHIFT]) else 1
        if keys[pygame.K_LEFT]:  self.rel_right_x -= speed
        if keys[pygame.K_RIGHT]: self.rel_right_x += speed
        if keys[pygame.K_UP]:    self.rel_y -= speed
        if keys[pygame.K_DOWN]:  self.rel_y += speed

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

    def draw_dashboard(self, minimap, status_text):
        for event in pygame.event.get():
            if event.type == pygame.QUIT: self.running = False
        self.screen.fill(BG_COLOR)
        if minimap is not None:
            rgb = cv2.cvtColor(minimap, cv2.COLOR_BGR2RGB)
            surf = pygame.surfarray.make_surface(np.transpose(rgb, (1, 0, 2)))
            self.screen.blit(surf, (20, 80))
            pygame.draw.rect(self.screen, (255, 255, 255), (20, 80, minimap.shape[1], minimap.shape[0]), 2)

        off_x = 350
        preview_size = 300
        grid_to_draw, px, py = self.get_current_grid_and_pos()
        vis_map = np.zeros((grid_to_draw.shape[0], grid_to_draw.shape[1], 3), dtype=np.uint8)
        vis_map[grid_to_draw == TILE_BLOCKED] = [255, 0, 0] 
        vis_map[grid_to_draw == TILE_WALKABLE] = [0, 150, 0]
        cv2.circle(vis_map, (px, py), 3, (0, 255, 255), -1)
        if self.active_target:
            cv2.line(vis_map, (px, py), self.active_target, (255, 255, 0), 1)
            cv2.circle(vis_map, self.active_target, 4, (255, 255, 0), -1)

        vis_rgb = np.transpose(vis_map, (1, 0, 2))
        surf_map = pygame.surfarray.make_surface(vis_rgb)
        surf_map = pygame.transform.scale(surf_map, (preview_size, preview_size))
        self.screen.blit(surf_map, (off_x, 80))
        pygame.draw.rect(self.screen, (0, 100, 255), (off_x, 80, preview_size, preview_size), 1)
        
        c = (255, 50, 50) if "COMBATE" in status_text else (0, 255, 0)
        self.screen.blit(self.font.render(f"STATUS: {status_text}", True, c), (20, 20))
        if self.active_target:
            tgt_text = f"ALVO: {self.active_target}"
        else: tgt_text = "ALVO: NENHUM"
        self.screen.blit(self.font.render(tgt_text, True, (255, 255, 0)), (20, 480))
        pygame.display.flip()

    def run(self):
        print("🎮 BOT RODANDO - Anti-Oscilação Ativo")
        while self.running:
            self.handle_calibration_input()
            win = self.get_window_geometry()
            if not win:
                self.release_all_keys()
                self.draw_dashboard(None, "PAUSADO")
                time.sleep(0.5)
                continue

            if self.check_battle_state(win):
                self.handle_battle(win)
                continue

            minimap_start = self.capture_smart_map(win)
            if minimap_start is None: continue

            if self.check_smart_map_change(minimap_start):
                self.release_all_keys()
                continue

            self.current_pos = self.find_player(minimap_start)
            next_move = self.get_next_routing_step()
            
            if next_move:
                curr_x, curr_y = self.get_current_grid_and_pos()[1:]
                intent_x, intent_y = curr_x, curr_y
                if next_move == 'up': intent_y -= 1
                elif next_move == 'down': intent_y += 1
                elif next_move == 'left': intent_x -= 1
                elif next_move == 'right': intent_x += 1
                
                pyautogui.keyDown(next_move)
                time.sleep(0.2) 
                
                win_after = self.get_window_geometry()
                if win_after:
                    minimap_end = self.capture_smart_map(win_after)
                    visual_diff = self.check_visual_movement(minimap_start, minimap_end)
                    
                    if visual_diff:
                        if self.current_map_mode == "CIDADE":
                            self.town_x, self.town_y = intent_x, intent_y
                        else:
                            self.world_x, self.world_y = intent_x, intent_y
                        self.update_tile_state(intent_x, intent_y, TILE_WALKABLE)
                        self.stuck_counter = 0
                        if self.failed_directions: self.failed_directions = []
                    else:
                        self.update_tile_state(intent_x, intent_y, TILE_BLOCKED)
                        self.stuck_counter += 1
                        if next_move not in self.failed_directions:
                            self.failed_directions.append(next_move)

                pyautogui.keyUp(next_move)
                self.draw_dashboard(minimap_end, f"INDO: {next_move}")
            else:
                self.draw_dashboard(minimap_start, "CHEGOU/PARADO")

            self.clock.tick(30)
            
            if self.detect_dialogue_bubble(win):
                self.release_all_keys()
                pyautogui.press('enter')

        pygame.quit()

if __name__ == "__main__":
    bot = FinalFantasyBot()
    bot.run()