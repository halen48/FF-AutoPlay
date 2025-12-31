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
TILE_UNKNOWN = 0  # Preto
TILE_BLOCKED = 1  # Vermelho
TILE_WALKABLE = 2 # Verde
TILE_DOOR = 3     # Azul (Novo!)

class FinalFantasyBot:
    def __init__(self):
        self.sct = mss.mss()
        
        # ALINHAMENTO
        self.rel_right_x = 1205
        self.rel_y = -20
        self.SIZE_BIG = 267     
        self.SIZE_SMALL = 187   
        
        # MEMÓRIA { 'hash': {'grid': np.array, 'doors': list} }
        self.maps_memory = {} 
        self.current_map_id = None 
        self.current_map_signature = None
        
        # CANVAS TÁTICO
        self.TOWN_SIZE = 1024
        self.town_grid = np.zeros((self.TOWN_SIZE, self.TOWN_SIZE), dtype=np.uint8)
        self.town_x = self.TOWN_SIZE // 2
        self.town_y = self.TOWN_SIZE // 2
        self.town_grid[self.town_y, self.town_x] = TILE_WALKABLE
        self.town_doors = [] # Lista de portas deste mapa

        self.WORLD_SIZE = self.SIZE_BIG
        self.world_grid = np.zeros((self.WORLD_SIZE, self.WORLD_SIZE), dtype=np.uint8)
        self.world_x = 0
        self.world_y = 0

        # ESTADO
        self.current_map_mode = "MUNDI"
        self.active_target = None  
        self.path_fail_count = 0   
        self.current_pos = None 
        
        self.debug_similarity = 1.0
        self.debug_color_dist = 0.0
        self.in_battle = False
        
        # Navegação
        self.stuck_counter = 0
        self.map_change_stability_counter = 0
        self.failed_directions = []

        # Pygame
        pygame.init()
        self.screen = pygame.display.set_mode((DEBUG_WIDTH, DEBUG_HEIGHT))
        pygame.display.set_caption("CEREBRO DO BOT - Weighted Exploration")
        self.font = pygame.font.SysFont("Consolas", 14, bold=True)
        self.clock = pygame.time.Clock()
        self.running = True

    # ... (get_window_geometry e release_all_keys mantidos) ...
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
            # Porta tem prioridade máxima, depois Walkable/Blocked
            if current_val != TILE_DOOR:
                # Se for desconhecido, aceita qualquer update
                # Se for Walkable, só aceita Blocked se for correção
                if current_val == TILE_UNKNOWN or (current_val == TILE_WALKABLE and state == TILE_BLOCKED):
                    grid[y, x] = state
            
            # Se a atualização for explicitamente uma porta, sobrescreve
            if state == TILE_DOOR:
                grid[y, x] = TILE_DOOR

    def is_tile_blocked(self, x, y):
        grid, _, _ = self.get_current_grid_and_pos()
        h, w = grid.shape
        if 0 <= x < w and 0 <= y < h:
            val = grid[y, x]
            return val == TILE_BLOCKED
        return True 

    # --- NOVA INTELIGÊNCIA DE TARGET (PONDERADA + FLOODFILL) ---

    def clean_unreachable_areas(self):
        """
        Executa um Flood Fill a partir da posição do jogador.
        Qualquer tile 'DESCONHECIDO' que não for atingido pela água 
        é declarado inalcançável (dentro de paredes) e marcado como BLOQUEADO.
        """
        grid, px, py = self.get_current_grid_and_pos()
        h, w = grid.shape
        
        # Cria máscara para floodFill (precisa ser uint8 e 2 pixels maior)
        # 0 = Obstáculo para o floodfill, 1 = Caminho livre
        # Consideramos Unknown(0) e Walkable(2) e Door(3) como passáveis para o teste
        mask = np.zeros((h + 2, w + 2), np.uint8)
        
        # Tudo que NÃO é Bloqueado(1) vira 1 na máscara (caminhável)
        # Bloqueado vira 0 (parede)
        fill_mask = (grid != TILE_BLOCKED).astype(np.uint8)
        
        # Copia para dentro da máscara com borda (OpenCV floodfill requirement)
        mask[1:-1, 1:-1] = fill_mask
        
        # Executa FloodFill a partir do jogador (valor de preenchimento arbitrário, ex: 100)
        # Estamos usando a versão que altera a imagem, mas queremos apenas saber o que foi tocado.
        # Vamos criar uma imagem temporária floodable.
        flood_img = fill_mask.copy()
        
        # Ponto semente: Jogador
        cv2.floodFill(flood_img, None, (px, py), 255)
        
        # Agora:
        # Tudo que era DESCONHECIDO (0) no grid original...
        # E que NÃO virou 255 na flood_img...
        # Significa que está isolado! Marcamos como BLOQUEADO.
        
        unreachable_mask = (grid == TILE_UNKNOWN) & (flood_img != 255)
        count_cleaned = np.count_nonzero(unreachable_mask)
        
        if count_cleaned > 0:
            grid[unreachable_mask] = TILE_BLOCKED
            print(f"🧹 FAXINA: Marquei {count_cleaned} tiles inalcançáveis como parede.")

    def select_new_target(self):
        grid, _, _ = self.get_current_grid_and_pos()
        
        # 1. Limpa sujeira (áreas fechadas)
        if self.current_map_mode == "CIDADE":
            self.clean_unreachable_areas()

        # 2. Cria mapa de candidatos (Apenas tiles DESCONHECIDOS)
        unknown_mask = (grid == TILE_UNKNOWN).astype(np.uint8)
        
        # Se não houver mais nada desconhecido, escolhe qualquer ponto livre longe
        if np.count_nonzero(unknown_mask) == 0:
            print("🌟 MAPA 100% EXPLORADO! Andando aleatório...")
            unknown_mask = (grid == TILE_WALKABLE).astype(np.uint8)

        # 3. DISTANCE TRANSFORM (O Peso da Exploração)
        # Calcula a distância de cada pixel '1' (desconhecido) até o pixel '0' (conhecido) mais próximo.
        # Resultado: Pixels no "meio" do desconhecido têm valores altos.
        dist_map = cv2.distanceTransform(unknown_mask, cv2.DIST_L2, 5)
        
        # Normaliza para probabilidades
        total_weight = np.sum(dist_map)
        
        if total_weight > 0:
            probs = dist_map.flatten() / total_weight
            # Escolhe um índice baseado no peso (Weighted Random)
            flat_idx = np.random.choice(probs.size, p=probs)
            ty, tx = np.unravel_index(flat_idx, dist_map.shape)
        else:
            # Fallback seguro
            tx = random.randint(10, grid.shape[1]-10)
            ty = random.randint(10, grid.shape[0]-10)

        self.active_target = (tx, ty)
        self.path_fail_count = 0
        self.failed_directions = [] 
        self.distance_history = []
        print(f"🎯 NOVO ALVO PONDERADO: {self.active_target}")

    def get_next_routing_step(self):
        if not self.active_target:
            self.select_new_target()
            return None

        grid, curr_x, curr_y = self.get_current_grid_and_pos()
        target_x, target_y = self.active_target

        dx = target_x - curr_x
        dy = target_y - curr_y
        dist_now = math.sqrt(dx**2 + dy**2)
        
        if dist_now < 10:
            print("✅ CHEGOU!")
            self.select_new_target()
            return None

        # Check de Progresso (Anti-Oscilação)
        self.distance_history.append(dist_now)
        if len(self.distance_history) > 5: self.distance_history.pop(0)
        if len(self.distance_history) >= 3:
            if (self.distance_history[0] - self.distance_history[-1]) < 3:
                print("⚠️ ESTAGNADO. Trocando.")
                self.select_new_target()
                return None

        moves = [('right', 1, 0), ('left', -1, 0), ('down', 0, 1), ('up', 0, -1)]
        
        valid_moves = []
        for mv, mx, my in moves:
            nx, ny = curr_x + mx, curr_y + my
            if not self.is_tile_blocked(nx, ny) and mv not in self.failed_directions:
                valid_moves.append((mv, nx, ny))
        
        if not valid_moves:
            self.select_new_target()
            return None

        best_move = None
        min_dist = float('inf')

        for mv, nx, ny in valid_moves:
            d = math.sqrt((target_x - nx)**2 + (target_y - ny)**2)
            if d < min_dist:
                min_dist = d
                best_move = mv
        
        return best_move

    # --- SUPORTE VISUAL & ETC ---
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

    # ... (get_visual_signature, compare_signatures, classify_map_type, capture_smart_map, detect_movement, detect_dialogue_bubble mantidos) ...
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
            
            # 1. SALVA PORTA E GRID ANTIGO
            if self.current_map_id and self.current_map_mode == "CIDADE":
                # Salva a posição atual como PORTA antes de sair
                self.update_tile_state(self.town_x, self.town_y, TILE_DOOR)
                if (self.town_x, self.town_y) not in self.town_doors:
                    self.town_doors.append((self.town_x, self.town_y))
                
                self.maps_memory[self.current_map_id] = {
                    'grid': self.town_grid.copy(),
                    'doors': list(self.town_doors)
                }
                print(f"💾 Mapa salvo com {len(self.town_doors)} portas.")
            
            # 2. CONFIGURA NOVO MAPA
            new_mode = self.classify_map_type(current_img)
            new_id = hashlib.md5(current_img.tobytes()).hexdigest()[:8]
            
            # Carrega ou Cria
            if new_id in self.maps_memory:
                print("📂 Mapa conhecido carregado.")
                data = self.maps_memory[new_id]
                self.town_grid = data['grid'].copy()
                self.town_doors = list(data['doors'])
            else:
                print("✨ Novo mapa criado.")
                self.town_grid = np.zeros((self.TOWN_SIZE, self.TOWN_SIZE), dtype=np.uint8)
                self.town_doors = []
            
            # Em mapa novo/carregado, reseta posição para o meio para não bugar
            # (Idealmente teríamos que saber qual porta entramos para setar o XY correto,
            # mas isso exigiria rastrear a relação entre portas dos mapas. Fica pro futuro.)
            self.town_x = self.TOWN_SIZE // 2
            self.town_y = self.TOWN_SIZE // 2
            self.town_grid[self.town_y, self.town_x] = TILE_WALKABLE
            
            self.current_map_mode = new_mode
            self.current_map_id = new_id
            self.current_map_signature = new_sig
            self.map_change_stability_counter = 0
            self.active_target = None 
            self.failed_directions = [] 
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

    def detect_movement(self, frame):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        if self.last_map_frame is not None and self.last_map_frame.shape == gray.shape: pass
        self.last_map_frame = gray
        return False # Stub para compatibilidade

    def update_position_logic(self, moved, direction):
        pass # Stub, lógica movida para o run()

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
        
        # Cores Táticas
        vis_map[grid_to_draw == TILE_BLOCKED] = [255, 0, 0] 
        vis_map[grid_to_draw == TILE_WALKABLE] = [0, 100, 0] # Verde Escuro
        vis_map[grid_to_draw == TILE_DOOR] = [255, 255, 0]   # Ciano/Amarelo (BGR) -> Portas
        
        cv2.circle(vis_map, (px, py), 3, (0, 255, 255), -1)
        if self.active_target:
            cv2.line(vis_map, (px, py), self.active_target, (0, 0, 255), 1) # Linha vermelha alvo
            cv2.circle(vis_map, self.active_target, 4, (0, 0, 255), -1)

        vis_rgb = np.transpose(vis_map, (1, 0, 2))
        surf_map = pygame.surfarray.make_surface(vis_rgb)
        surf_map = pygame.transform.scale(surf_map, (preview_size, preview_size))
        self.screen.blit(surf_map, (off_x, 80))
        pygame.draw.rect(self.screen, (0, 100, 255), (off_x, 80, preview_size, preview_size), 1)
        
        header = f"TÁTICO ({'CIDADE' if self.current_map_mode == 'CIDADE' else 'MUNDI'})"
        self.screen.blit(self.font.render(header, True, (0, 255, 255)), (off_x, 60))

        c = (255, 50, 50) if "COMBATE" in status_text else (0, 255, 0)
        self.screen.blit(self.font.render(f"STATUS: {status_text}", True, c), (20, 20))
        tgt_text = f"ALVO: {self.active_target}" if self.active_target else "ALVO: NENHUM"
        self.screen.blit(self.font.render(tgt_text, True, (255, 255, 0)), (20, 480))

        pygame.display.flip()

    def run(self):
        print("🎮 BOT RODANDO - Ponderado + Portas")
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