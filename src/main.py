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
DEBUG_WIDTH = 800  # Aumentei pra caber o debug da matriz
DEBUG_HEIGHT = 400
BG_COLOR = (20, 20, 20)

# --- PROPORÇÕES HARDCODED (Base 1280x760) ---
# Calculado com base nos seus pontos: (947, 77) até (1217, 347)
MINIMAP_REL_X = 0.739  # 947 / 1280
MINIMAP_REL_Y = 0.101  # 77 / 760
MINIMAP_REL_W = 0.211  # 270 / 1280
MINIMAP_REL_H = 0.355  # 270 / 760

class FinalFantasyBot:
    def __init__(self):
        self.sct = mss.mss()
        self.current_pos = None  # (x, y)
        self.target_pos = None   # (x, y) ou None
        self.matrix = None       # A Grade do Mundo
        
        # Estado de movimento
        self.last_move_time = 0
        self.move_interval = 0.1 # Velocidade dos toques

        # --- PYGAME SETUP ---
        pygame.init()
        self.screen = pygame.display.set_mode((DEBUG_WIDTH, DEBUG_HEIGHT))
        pygame.display.set_caption("CEREBRO DO BOT - Contra a Máquina")
        self.font = pygame.font.SysFont("Consolas", 14, bold=True)
        self.clock = pygame.time.Clock()
        self.running = True

    def get_window_geometry(self):
        """Busca a janela do jogo no Linux"""
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
        """Recorta o minimapa usando as proporções fixas"""
        full_img = np.array(self.sct.grab(win_geo))
        full_img = cv2.cvtColor(full_img, cv2.COLOR_BGRA2BGR)
        
        h, w, _ = full_img.shape
        x = int(w * MINIMAP_REL_X)
        y = int(h * MINIMAP_REL_Y)
        w_map = int(w * MINIMAP_REL_W)
        h_map = int(h * MINIMAP_REL_H)
        
        # Garante limites
        if y+h_map > h or x+w_map > w: return None
        
        return full_img[y:y+h_map, x:x+w_map]

    def find_player(self, img):
        """Acha a seta vermelha"""
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        # Vermelho tem 2 ranges no HSV
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
        """
        Converte a imagem visual em Matriz Lógica.
        0 = Terreno (Verde/Marrom) -> Andável
        1 = Água/Obstáculo (Azul/Preto) -> Bloqueado
        """
        # Reduzir resolução para performance (opcional, aqui mantendo 1:1)
        # Lógica simplificada: Se tem muito azul, é água.
        
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        
        # Definindo "Água/Obstáculo" como tons de azul ou preto muito escuro
        # Azul no HSV fica entre 90 e 150
        lower_blue = np.array([90, 50, 50])
        upper_blue = np.array([150, 255, 255])
        
        mask_water = cv2.inRange(hsv, lower_blue, upper_blue)
        
        # Cria matriz: 0 onde é seguro, 1 onde é água
        # Normalizando para 0 e 1
        matrix = (mask_water > 0).astype(int)
        
        return matrix, mask_water # Retorna mask pra debug visual

    def decide_move(self):
        """Cérebro: Aleatório vs Objetivo"""
        
        # Se não tem objetivo, define um aleatório às vezes?
        # Por enquanto: Se target é None -> Random Walk
        
        if self.target_pos:
            tx, ty = self.target_pos
            px, py = self.current_pos
            
            # Chegou? (Margem de erro de 5 pixels)
            if abs(tx - px) < 5 and abs(ty - py) < 5:
                print("🎯 Chegamos no objetivo!")
                self.target_pos = None
                return None
            
            # Algoritmo de perseguição simples (eixo dominante)
            dx = tx - px
            dy = ty - py
            
            if abs(dx) > abs(dy):
                return 'right' if dx > 0 else 'left'
            else:
                return 'down' if dy > 0 else 'up'
        
        else:
            # Modo Bêbado (Random Walk)
            # 5% de chance de mudar a direção para não ficar vibrando
            if random.random() < 0.1:
                return random.choice(['up', 'down', 'left', 'right'])
            return None

    def draw_dashboard(self, minimap, mask_debug, status_text):
        """Renderiza a UI do Pygame"""
        # Eventos
        for event in pygame.event.get():
            if event.type == pygame.QUIT: self.running = False
            # Clique do Mouse define objetivo!
            if event.type == pygame.MOUSEBUTTONDOWN and self.matrix is not None:
                # Converter clique da tela para coordenada da matriz
                mx, my = pygame.mouse.get_pos()
                # O minimapa está desenhado em (20, 60) com tamanho 250x250 (exemplo)
                # Ajuste conforme o blit abaixo
                rel_x = mx - 20
                rel_y = my - 60
                
                # Fator de escala (se tiver zoom)
                scale_w = minimap.shape[1] / 250
                scale_h = minimap.shape[0] / 250
                
                final_x = int(rel_x * scale_w)
                final_y = int(rel_y * scale_h)
                
                if 0 <= final_x < minimap.shape[1] and 0 <= final_y < minimap.shape[0]:
                    self.target_pos = (final_x, final_y)
                    print(f"🖱️ Novo Objetivo Clicado: {self.target_pos}")

        self.screen.fill(BG_COLOR)
        
        # 1. VISUAL (Minimapa Real)
        if minimap is not None:
            rgb = cv2.cvtColor(minimap, cv2.COLOR_BGR2RGB)
            rgb = np.transpose(rgb, (1, 0, 2))
            surf = pygame.surfarray.make_surface(rgb)
            surf = pygame.transform.scale(surf, (250, 250))
            self.screen.blit(surf, (20, 60))
            pygame.draw.rect(self.screen, (255, 255, 255), (20, 60, 250, 250), 2)
            
            lbl = self.font.render("VISAO (CAMERA)", True, (200, 200, 200))
            self.screen.blit(lbl, (20, 40))

        # 2. LÓGICA (Matriz / Máscara)
        if mask_debug is not None:
            # Máscara é P&B. Branco = Parede (1), Preto = Chão (0)
            mask_rgb = cv2.cvtColor(mask_debug, cv2.COLOR_GRAY2RGB)
            mask_rgb = np.transpose(mask_rgb, (1, 0, 2))
            surf_m = pygame.surfarray.make_surface(mask_rgb)
            surf_m = pygame.transform.scale(surf_m, (250, 250))
            self.screen.blit(surf_m, (300, 60))
            pygame.draw.rect(self.screen, (0, 255, 255), (300, 60, 250, 250), 1)
            
            lbl2 = self.font.render("MATRIZ (Branco=Parede)", True, (0, 255, 255))
            self.screen.blit(lbl2, (300, 40))

        # 3. TEXTOS
        pos_txt = f"Pos: {self.current_pos}" if self.current_pos else "Pos: ???"
        tar_txt = f"Target: {self.target_pos}" if self.target_pos else "Target: ALEATORIO (Clique para definir)"
        
        self.screen.blit(self.font.render(status_text, True, (0, 255, 0)), (20, 10))
        self.screen.blit(self.font.render(pos_txt, True, (255, 255, 0)), (20, 320))
        self.screen.blit(self.font.render(tar_txt, True, (255, 100, 255)), (20, 340))

        pygame.display.flip()

    def run(self):
        print("🎮 BOT INICIADO - Aguardando Janela 'Final Fantasy'...")
        
        while self.running:
            win = self.get_window_geometry()
            
            if not win:
                self.draw_dashboard(None, None, "AGUARDANDO FINAL FANTASY...")
                time.sleep(0.5)
                continue

            try:
                # 1. Captura
                minimap = self.capture_minimap(win)
                if minimap is None: continue

                # 2. Processamento (Visão + Matriz)
                self.current_pos = self.find_player(minimap)
                self.matrix, mask_debug = self.generate_matrix(minimap)
                
                # Desenha posição e target na imagem para debug
                if self.current_pos:
                    cv2.circle(minimap, self.current_pos, 5, (0, 255, 0), -1) # Player Verde
                if self.target_pos:
                    cv2.circle(minimap, self.target_pos, 5, (255, 0, 255), -1) # Alvo Roxo
                    cv2.line(minimap, self.current_pos, self.target_pos, (255, 255, 0), 1)

                # 3. Decisão e Ação
                if self.current_pos:
                    move = self.decide_move()
                    
                    # Controle de taxa de movimento (para não spamar teclas)
                    if move and (time.time() - self.last_move_time > self.move_interval):
                        # print(f"Andando: {move}") # Debug no terminal
                        pyautogui.keyDown(move)
                        time.sleep(0.05) # Toque rápido
                        pyautogui.keyUp(move)
                        self.last_move_time = time.time()

                # 4. Renderiza Dashboard
                self.draw_dashboard(minimap, mask_debug, "RODANDO")
                
                self.clock.tick(60) # 60 FPS na UI

            except Exception as e:
                print(f"Erro no loop: {e}")
                
        pygame.quit()

if __name__ == "__main__":
    bot = FinalFantasyBot()
    bot.run()