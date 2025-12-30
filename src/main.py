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

# CORES
TARGET_CITY_BGR = np.array([148, 203, 244], dtype=np.float32)
CITY_COLOR_THRESHOLD = 60.0 
MAP_CHANGE_THRESHOLD = 0.65 

# ESTADOS DO TILE
TILE_UNKNOWN = 0
TILE_BLOCKED = 1  # Vermelho
TILE_WALKABLE = 2 # Branco
TILE_DOOR = 3     # Azul

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
        
        # RADIAL SCAN (10 Graus)
        self.current_scan_angle = 0   
        self.is_scan_complete = False 
        self.SCAN_STEP = 10           # <--- 10 GRAUS
        
        # CANVAS TÁTICO
        self.TOWN_SIZE = 1024
        self.town_grid = np.zeros((self.TOWN_SIZE, self.TOWN_SIZE), dtype=np.uint8)
        self.town_x = self.TOWN_SIZE // 2
        self.town_y = self.TOWN_SIZE // 2
        self.town_grid[self.town_y, self.town_x] = TILE_WALKABLE
        self.town_doors = []

        self.WORLD_SIZE = self.SIZE_BIG
        self.world_grid = np.zeros((self.WORLD_SIZE, self.WORLD_SIZE), dtype=np.uint8)
        self.world_x = 0
        self.world_y = 0

        # ESTADO
        self.current_map_mode = "MUNDI"
        self.active_target = None  
        self.last_map_frame = None
        self.current_pos = None 
        self.in_battle = False
        
        # VARIÁVEIS DE DESVIO (25%)
        self.collision_base_dist = None # Distância quando bateu
        self.is_deviating = False       # Se está tentando contornar
        
        # Navegação
        self.stuck_counter = 0
        self.map_change_stability_counter = 0
        self.failed_directions = []

        # Pygame
        pygame.init()
        self.screen = pygame.display.set_mode((DEBUG_WIDTH, DEBUG_HEIGHT))
        pygame.display.set_caption("CEREBRO DO BOT - 25% Deviation Limit")
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
                return {"top": int(pos.group(2)), "left": int(pos.group(1)), "width": int(geom.group(1)), "height": int(geom.group(2))}
        except: pass
        return None

    def release_all_keys(self):
        for k in ['up', 'down', 'left', 'right', 'enter', KEY_SQUARE]:
            pyautogui.keyUp(k)

    # --- MEMÓRIA ---
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
            if current_val != TILE_DOOR:
                if current_val == TILE_UNKNOWN or (current_val == TILE_WALKABLE and state == TILE_BLOCKED):
                    grid[y, x] = state
            if state == TILE_DOOR:
                grid[y, x] = TILE_DOOR

    def is_tile_blocked(self, x, y):
        grid, _, _ = self.get_current_grid_and_pos()
        h, w = grid.shape
        if 0 <= x < w and 0 <= y < h:
            return grid[y, x] == TILE_BLOCKED
        return True 

    # --- LÓGICA DE TARGET ---

    def clean_unreachable_areas(self):
        grid, px, py = self.get_current_grid_and_pos()
        h, w = grid.shape
        mask = np.zeros((h + 2, w + 2), np.uint8)
        fill_mask = (grid != TILE_BLOCKED).astype(np.uint8)
        mask[1:-1, 1:-1] = fill_mask
        flood_img = fill_mask.copy()
        cv2.floodFill(flood_img, None, (px, py), 255)
        unreachable_mask = (grid == TILE_UNKNOWN) & (flood_img != 255)
        if np.count_nonzero(unreachable_mask) > 0:
            grid[unreachable_mask] = TILE_BLOCKED

    def select_new_target(self):
        grid, px, py = self.get_current_grid_and_pos()
        
        # Reset de variáveis de desvio
        self.collision_base_dist = None
        self.is_deviating = False
        self.failed_directions = [] 
        
        # ESTRATÉGIA 1: RADIAL (10 Graus)
        if not self.is_scan_complete and self.current_map_mode == "CIDADE":
            print(f"📡 RADIAL SCAN: {self.current_scan_angle}°")
            rad = math.radians(self.current_scan_angle - 90)
            scan_radius = 600
            tx = int(px + scan_radius * math.cos(rad))
            ty = int(py + scan_radius * math.sin(rad))
            tx = max(10, min(tx, self.TOWN_SIZE - 10))
            ty = max(10, min(ty, self.TOWN_SIZE - 10))
            self.active_target = (tx, ty)
            
            self.current_scan_angle += self.SCAN_STEP
            if self.current_scan_angle >= 360:
                print("✅ SCAN COMPLETO! Mudando para Ponderado.")
                self.is_scan_complete = True
                self.current_scan_angle = 0
            
        # ESTRATÉGIA 2: PONDERADA
        else:
            if self.current_map_mode == "CIDADE": self.clean_unreachable_areas()
            unknown_mask = (grid == TILE_UNKNOWN).astype(np.uint8)
            if np.count_nonzero(unknown_mask) == 0:
                unknown_mask = (grid == TILE_WALKABLE).astype(np.uint8)

            dist_map = cv2.distanceTransform(unknown_mask, cv2.DIST_L2, 5)
            total_weight = np.sum(dist_map)
            
            if total_weight > 0:
                probs = dist_map.flatten() / total_weight
                flat_idx = np.random.choice(probs.size, p=probs)
                ty, tx = np.unravel_index(flat_idx, dist_map.shape)
            else:
                tx = random.randint(10, grid.shape[1]-10)
                ty = random.randint(10, grid.shape[0]-10)

            self.active_target = (tx, ty)
            print(f"🎲 ALVO PONDERADO: {self.active_target}")

    def get_current_dist_to_target(self):
        if not self.active_target: return 0
        curr_x, curr_y = self.get_current_grid_and_pos()[1:]
        tgt_x, tgt_y = self.active_target
        return math.sqrt((tgt_x - curr_x)**2 + (tgt_y - curr_y)**2)

    def check_deviation_threshold(self):
        """
        Verifica se nos afastamos mais de 25% do ponto onde a colisão começou.
        """
        if not self.is_deviating or self.collision_base_dist is None:
            return True # Tudo ok

        current_dist = self.get_current_dist_to_target()
        
        # Limite: 125% da distância original
        limit_dist = self.collision_base_dist * 1.25
        
        if current_dist > limit_dist:
            print(f"⚠️ DESVIO EXCESSIVO! (Dist: {current_dist:.1f} > Limite: {limit_dist:.1f}). Desistindo.")
            return False # Falhou, deve trocar alvo
            
        return True # Ainda dentro do limite

    def get_next_routing_step(self):
        if not self.active_target:
            self.select_new_target()
            return None

        # 1. Verifica Limite de 25%
        if not self.check_deviation_threshold():
            self.select_new_target()
            return None

        grid, curr_x, curr_y = self.get_current_grid_and_pos()
        target_x, target_y = self.active_target
        dist_now = self.get_current_dist_to_target()
        
        # Chegada (Tolerância maior no scan pra não ficar preso na borda)
        threshold = 20 if not self.is_scan_complete else 10
        if dist_now < threshold:
            print("✅ CHEGOU NO ALVO!")
            self.select_new_target()
            return None

        moves = [('right', 1, 0), ('left', -1, 0), ('down', 0, 1), ('up', 0, -1)]
        
        valid_moves = []
        for mv, mx, my in moves:
            nx, ny = curr_x + mx, curr_y + my
            if not self.is_tile_blocked(nx, ny) and mv not in self.failed_directions:
                valid_moves.append((mv, nx, ny))
        
        if not valid_moves:
            print("⚠️ BECO SEM SAÍDA (Lista Negra cheia). Trocando alvo.")
            self.select_new_target()
            return None

        # Escolhe o que mais aproxima (Greedy)
        best_move = None
        min_dist = float('inf')

        for mv, nx, ny in valid_moves:
            d = math.sqrt((target_x - nx)**2 + (target_y - ny)**2)
            if d < min_dist:
                min_dist = d
                best_move = mv
        
        return best_move

    # --- VISÃO E SUPORTE ---
    def check_visual_movement(self, img_before, img_after):
        if img_before is None or img_after is None: return False
        if img_before.shape != img_after.shape: return False
        gray1 = cv2.cvtColor(img_before, cv2.COLOR_BGR2GRAY)
        gray2 = cv2.cvtColor(img_after, cv2.COLOR_BGR2GRAY)
        diff = cv2.absdiff(gray1, gray2)
        return np.count_nonzero(diff > 10) > 50

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

    # ... (signatures, classify, check_change, capture_smart_map mantidos) ...
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
                self.update_tile_state(self.town_x, self.town_y, TILE_DOOR)
                if (self.town_x, self.town_y) not in self.town_doors:
                    self.town_doors.append((self.town_x, self.town_y))
                self.maps_memory[self.current_map_id] = {
                    'grid': self.town_grid.copy(),
                    'doors': list(self.town_doors),
                    'scan_angle': self.current_scan_angle,
                    'scan_complete': self.is_scan_complete
                }
            
            new_mode = self.classify_map_type(current_img)
            new_id = hashlib.md5(current_img.tobytes()).hexdigest()[:8]
            
            if new_id in self.maps_memory:
                data = self.maps_memory[new_id]
                self.town_grid = data['grid'].copy()
                self.town_doors = list(data['doors'])
                self.current_scan_angle = data.get('scan_angle', 0)
                self.is_scan_complete = data.get('scan_complete', False)
            else:
                self.town_grid = np.zeros((self.TOWN_SIZE, self.TOWN_SIZE), dtype=np.uint8)
                self.town_doors = []
                self.current_scan_angle = 0
                self.is_scan_complete = False
            
            self.town_x = self.TOWN_SIZE // 2
            self.town_y = self.TOWN_SIZE // 2
            self.town_grid[self.town_y, self.town_x] = TILE_WALKABLE
            
            self.current_map_mode = new_mode
            self.current_map_id = new_id
            self.current_map_signature = new_sig
            self.map_change_stability_counter = 0
            self.active_target = None 
            self.failed_directions = [] 
            self.collision_base_dist = None
            self.is_deviating = False
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
        
        # --- CORES DO DASHBOARD ---
        vis_map[grid_to_draw == TILE_BLOCKED] = [0, 0, 255]   # Red (RGB) -> No OpenCV era BGR
        vis_map[grid_to_draw == TILE_WALKABLE] = [255, 255, 255] # White
        vis_map[grid_to_draw == TILE_DOOR] = [0, 255, 255]    # Cyan
        
        # Player: Verde
        cv2.circle(vis_map, (px, py), 3, (0, 255, 0), -1) 
        
        if self.active_target:
            cv2.line(vis_map, (px, py), self.active_target, (255, 255, 0), 1) 
            cv2.circle(vis_map, self.active_target, 4, (255, 255, 0), -1)

        vis_rgb = np.transpose(vis_map, (1, 0, 2))
        surf_map = pygame.surfarray.make_surface(vis_rgb)
        surf_map = pygame.transform.scale(surf_map, (preview_size, preview_size))
        self.screen.blit(surf_map, (off_x, 80))
        pygame.draw.rect(self.screen, (0, 100, 255), (off_x, 80, preview_size, preview_size), 1)
        
        scan_txt = f"{self.current_scan_angle}°" if not self.is_scan_complete else "RANDOM"
        header = f"TÁTICO ({'CIDADE' if self.current_map_mode == 'CIDADE' else 'MUNDI'}) - {scan_txt}"
        self.screen.blit(self.font.render(header, True, (0, 255, 255)), (off_x, 60))

        c = (255, 50, 50) if "COMBATE" in status_text else (0, 255, 0)
        self.screen.blit(self.font.render(f"STATUS: {status_text}", True, c), (20, 20))
        
        # Info de Desvio
        dev_text = ""
        if self.is_deviating and self.collision_base_dist:
            curr = self.get_current_dist_to_target()
            pct = (curr / self.collision_base_dist) * 100
            dev_text = f" | DEVIO: {pct:.0f}%"
            
        tgt_text = f"ALVO: {self.active_target}{dev_text}" if self.active_target else "ALVO: NENHUM"
        self.screen.blit(self.font.render(tgt_text, True, (255, 255, 0)), (20, 480))

        pygame.display.flip()

    def run(self):
        print("🎮 BOT RODANDO - 25% Deviation Rule")
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
                        # ANDOU
                        if self.current_map_mode == "CIDADE":
                            self.town_x, self.town_y = intent_x, intent_y
                        else:
                            self.world_x, self.world_y = intent_x, intent_y
                        self.update_tile_state(intent_x, intent_y, TILE_WALKABLE)
                        
                        # Se andou, reseta flags de colisão e desvio
                        self.stuck_counter = 0
                        if self.failed_directions: self.failed_directions = []
                        # Nota: Não resetamos is_deviating aqui para permitir que ele continue
                        # o contorno até sair da zona de perigo ou alinhar com o alvo.
                        # Mas se o ângulo melhorar, o get_next_routing_step já cuida disso.
                        
                    else:
                        # BATEU (PAREDE)
                        self.update_tile_state(intent_x, intent_y, TILE_BLOCKED)
                        self.stuck_counter += 1
                        if next_move not in self.failed_directions:
                            self.failed_directions.append(next_move)
                        
                        # --- INÍCIO DO ESTADO DE DESVIO ---
                        if not self.is_deviating and self.active_target:
                            self.is_deviating = True
                            self.collision_base_dist = self.get_current_dist_to_target()
                            print(f"⚠️ COLISÃO! Iniciando Desvio (Base Dist: {self.collision_base_dist:.1f})")

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