"""
EvoFox One S Gamepad Visualizer (ESP32-C3 Wireless Bridge)
Real-time 60 FPS graphical visualizer powered by Pygame & PySerial.
Decodes 10-byte HID reports from EvoFox One S in Android mode.
"""

import sys
import time
import math
import threading
import pygame
import serial

PORT = "COM8"
BAUD = 115200

# Color Palette (Dark Cyberpunk / Xbox Theme)
BG_COLOR       = (18, 20, 29)
CARD_BG        = (26, 29, 42)
CARD_BORDER    = (45, 50, 70)
SHELL_COLOR    = (32, 35, 52)
SHELL_OUTLINE  = (55, 62, 90)
ACCENT_CYAN    = (0, 229, 255)
ACCENT_GREEN   = (0, 230, 118)
ACCENT_RED     = (255, 23, 68)
ACCENT_BLUE    = (41, 121, 255)
ACCENT_YELLOW  = (255, 214, 0)
ACCENT_PURPLE  = (224, 64, 251)
TEXT_WHITE     = (240, 243, 250)
TEXT_MUTED     = (130, 140, 165)
INACTIVE_BTN   = (40, 44, 65)

class GamepadState:
    def __init__(self):
        self.lock = threading.Lock()
        # Buttons
        self.a = False
        self.b = False
        self.x = False
        self.y = False
        self.lb = False
        self.rb = False
        self.lt_dig = False
        self.rt_dig = False
        self.select = False
        self.start = False
        self.home = False
        self.ls = False
        self.rs = False
        # D-Pad
        self.up = False
        self.down = False
        self.left = False
        self.right = False
        # Analog Sticks (0..255, 128 neutral)
        self.lx = 128
        self.ly = 128
        self.rx = 128
        self.ry = 128
        # Analog Triggers (0..255)
        self.lt_ana = 0
        self.rt_ana = 0
        # Meta
        self.connected = False
        self.last_packet_time = 0
        self.packet_count = 0
        self.raw_hex = "80 80 80 80 FF 00 00 00 00 1E"

state = GamepadState()

def serial_reader_thread():
    """Background thread reading from ESP32-C3 on COM8 without blocking UI."""
    while True:
        s = None
        try:
            s = serial.Serial()
            s.port = PORT
            s.baudrate = BAUD
            s.timeout = 0.2
            s.dtr = True
            s.rts = False
            s.open()
            print(f"[SERIAL] Connected to {PORT} at {BAUD} baud.", flush=True)
            with state.lock:
                state.connected = True

            while True:
                line = s.readline()
                if not line:
                    continue
                text = line.decode('utf-8', errors='replace').strip()
                if not text:
                    continue

                # Check for 10-byte report: [REPORT 10-byte]: 80 80 80 80 FF 00 00 00 00 1E
                if "[REPORT 10-byte]:" in text or "[NOTIF h=27 len=10]:" in text:
                    parts = text.split("]:")
                        if len(parts) >= 2:
                            hex_str = parts[1].strip()
                            bytes_str = hex_str.split()
                            if len(bytes_str) == 10:
                                try:
                                    b = [int(x, 16) for x in bytes_str]
                                    with state.lock:
                                        state.raw_hex = hex_str
                                        state.last_packet_time = time.time()
                                        state.packet_count += 1
                                        state.connected = True

                                        # Sticks
                                        state.lx = b[0]
                                        state.ly = b[1]
                                        state.rx = b[2]
                                        state.ry = b[3]

                                        # D-Pad (Hat switch: 0=UP, 1=UR, 2=R, 3=DR, 4=D, 5=DL, 6=L, 7=UL, 0xFF=Release)
                                        hat = b[4]
                                        state.up    = (hat == 0 or hat == 1 or hat == 7)
                                        state.right = (hat == 1 or hat == 2 or hat == 3)
                                        state.down  = (hat == 3 or hat == 4 or hat == 5)
                                        state.left  = (hat == 5 or hat == 6 or hat == 7)

                                        # Buttons Byte 5 (A, B, X, Y, LB, RB)
                                        state.a  = bool(b[5] & 0x01)
                                        state.b  = bool(b[5] & 0x02)
                                        state.x  = bool(b[5] & 0x08)
                                        state.y  = bool(b[5] & 0x10)
                                        state.lb = bool(b[5] & 0x40)
                                        state.rb = bool(b[5] & 0x80)

                                        # Buttons Byte 6 (Triggers, Menu, Clicks)
                                        state.lt_dig = bool(b[6] & 0x01)
                                        state.rt_dig = bool(b[6] & 0x02)
                                        state.select = bool(b[6] & 0x04)
                                        state.start  = bool(b[6] & 0x08)
                                        state.home   = bool(b[6] & 0x10)
                                        state.ls     = bool(b[6] & 0x20)
                                        state.rs     = bool(b[6] & 0x40)

                                        # Analog triggers
                                        state.rt_ana = b[7]
                                        state.lt_ana = b[8]
                                except Exception as parse_err:
                                    pass

        except serial.SerialException as e:
            with state.lock:
                state.connected = False
            if s:
                try:
                    s.close()
                except Exception:
                    pass
            time.sleep(1.0)
        except Exception as e:
            if s:
                try:
                    s.close()
                except Exception:
                    pass
            time.sleep(1.0)

def draw_pill(surface, color, rect, radius=12, border_color=None, border_width=2):
    """Draw a smooth rounded rectangle / pill."""
    pygame.draw.rect(surface, color, rect, border_radius=radius)
    if border_color and border_width > 0:
        pygame.draw.rect(surface, border_color, rect, width=border_width, border_radius=radius)

def draw_circle_btn(surface, pos, radius, active, active_color, inactive_color, label, font, border_color=None):
    """Draw a circular button with glow effect when active."""
    x, y = pos
    if active:
        # Glow outer halo
        glow_surf = pygame.Surface((radius * 4, radius * 4), pygame.SRCALPHA)
        glow_c = (active_color[0], active_color[1], active_color[2], 60)
        pygame.draw.circle(glow_surf, glow_c, (radius * 2, radius * 2), int(radius * 1.5))
        surface.blit(glow_surf, (x - radius * 2, y - radius * 2))
        # Button fill
        pygame.draw.circle(surface, active_color, pos, radius)
        pygame.draw.circle(surface, (255, 255, 255), pos, radius, width=2)
        txt = font.render(label, True, (0, 0, 0) if active_color == ACCENT_YELLOW else (255, 255, 255))
    else:
        pygame.draw.circle(surface, inactive_color, pos, radius)
        if border_color:
            pygame.draw.circle(surface, border_color, pos, radius, width=2)
        txt = font.render(label, True, TEXT_MUTED)

    rect = txt.get_rect(center=pos)
    surface.blit(txt, rect)

def main():
    pygame.init()
    pygame.display.set_caption("EvoFox One S - ESP32-C3 Wireless Gamepad Visualizer")
    
    # Try setting app window icon
    screen_w, screen_h = 960, 680
    screen = pygame.display.set_mode((screen_w, screen_h))
    clock = pygame.time.Clock()

    # Fonts
    font_title = pygame.font.SysFont("Segoe UI, Arial, sans-serif", 20, bold=True)
    font_bold  = pygame.font.SysFont("Segoe UI, Arial, sans-serif", 16, bold=True)
    font_btn   = pygame.font.SysFont("Segoe UI, Arial, sans-serif", 15, bold=True)
    font_mono  = pygame.font.SysFont("Consolas, Courier New, monospace", 13)
    font_sm    = pygame.font.SysFont("Segoe UI, Arial, sans-serif", 12)

    # Launch background serial thread
    t = threading.Thread(target=serial_reader_thread, daemon=True)
    t.start()

    running = True
    start_time = time.time()

    while running:
        dt = clock.tick(60)

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    running = False

        # Read snapshot of gamepad state
        with state.lock:
            s_a = state.a
            s_b = state.b
            s_x = state.x
            s_y = state.y
            s_lb = state.lb
            s_rb = state.rb
            s_lt_dig = state.lt_dig
            s_rt_dig = state.rt_dig
            s_lt_ana = state.lt_ana
            s_rt_ana = state.rt_ana
            s_select = state.select
            s_start  = state.start
            s_home   = state.home
            s_ls     = state.ls
            s_rs     = state.rs
            s_up     = state.up
            s_down   = state.down
            s_left   = state.left
            s_right  = state.right
            s_lx     = state.lx
            s_ly     = state.ly
            s_rx     = state.rx
            s_ry     = state.ry
            s_conn   = state.connected
            s_raw    = state.raw_hex
            s_pkts   = state.packet_count
            s_last_t = state.last_packet_time

        # Background
        screen.fill(BG_COLOR)

        # ---------------------------------------------------------------------
        # Top Header Bar
        # ---------------------------------------------------------------------
        draw_pill(screen, CARD_BG, (20, 16, screen_w - 40, 60), radius=14, border_color=CARD_BORDER)
        
        # Connection status badge
        dot_color = ACCENT_GREEN if s_conn else (255, 60, 60)
        pygame.draw.circle(screen, dot_color, (45, 46), 7)
        if s_conn:
            # Pulsing glow
            pulse = (math.sin(time.time() * 5.0) + 1.0) * 0.5
            glow_r = int(9 + pulse * 4)
            glow_surf = pygame.Surface((glow_r * 2, glow_r * 2), pygame.SRCALPHA)
            pygame.draw.circle(glow_surf, (0, 230, 118, int(60 + pulse * 60)), (glow_r, glow_r), glow_r)
            screen.blit(glow_surf, (45 - glow_r, 46 - glow_r))

        status_txt = "CONNECTED (COM8 @ 115200)" if s_conn else "CONNECTING TO ESP32-C3 (COM8)..."
        t_stat = font_bold.render(status_txt, True, TEXT_WHITE if s_conn else (255, 120, 120))
        screen.blit(t_stat, (62, 36))

        # Title & Device Name
        t_model = font_title.render("EvoFox One S", True, ACCENT_CYAN)
        screen.blit(t_model, (530, 26))
        t_sub = font_sm.render("Tri-Mode Gamepad  •  ESP32-C3 Zero-Latency BLE Bridge", True, TEXT_MUTED)
        screen.blit(t_sub, (530, 50))

        # ---------------------------------------------------------------------
        # Main Controller Card Area
        # ---------------------------------------------------------------------
        cx, cy = screen_w // 2, 340
        draw_pill(screen, CARD_BG, (20, 90, screen_w - 40, 480), radius=18, border_color=CARD_BORDER)

        # Draw Controller Silhouette (Decorative Chassis)
        shell_rect = pygame.Rect(cx - 260, cy - 140, 520, 310)
        draw_pill(screen, SHELL_COLOR, shell_rect, radius=50, border_color=SHELL_OUTLINE, border_width=3)
        # Left Grip
        pygame.draw.ellipse(screen, SHELL_COLOR, (cx - 280, cy - 20, 110, 210))
        pygame.draw.ellipse(screen, SHELL_OUTLINE, (cx - 280, cy - 20, 110, 210), width=3)
        # Right Grip
        pygame.draw.ellipse(screen, SHELL_COLOR, (cx + 170, cy - 20, 110, 210))
        pygame.draw.ellipse(screen, SHELL_OUTLINE, (cx + 170, cy - 20, 110, 210), width=3)
        # Re-fill inner body so grips merge seamlessly
        draw_pill(screen, SHELL_COLOR, shell_rect, radius=50)

        # ---------------------------------------------------------------------
        # Left and Right Analog Triggers (LT & RT) Gauges
        # ---------------------------------------------------------------------
        # LT Gauge (Left Side)
        lt_box = pygame.Rect(50, 120, 75, 150)
        draw_pill(screen, (20, 22, 32), lt_box, radius=10, border_color=CARD_BORDER)
        lt_pct = s_lt_ana / 255.0
        lt_fill_h = int(110 * lt_pct)
        if lt_fill_h > 0:
            fill_rect = pygame.Rect(56, 120 + 110 - lt_fill_h + 30, 63, lt_fill_h)
            draw_pill(screen, ACCENT_CYAN, fill_rect, radius=6)
        screen.blit(font_bold.render("LT", True, ACCENT_CYAN if (s_lt_dig or s_lt_ana > 30) else TEXT_WHITE), (75, 126))
        screen.blit(font_mono.render(f"{int(lt_pct * 100)}%", True, TEXT_WHITE), (68, 245))

        # RT Gauge (Right Side)
        rt_box = pygame.Rect(screen_w - 125, 120, 75, 150)
        draw_pill(screen, (20, 22, 32), rt_box, radius=10, border_color=CARD_BORDER)
        rt_pct = s_rt_ana / 255.0
        rt_fill_h = int(110 * rt_pct)
        if rt_fill_h > 0:
            fill_rect = pygame.Rect(screen_w - 125 + 6, 120 + 110 - rt_fill_h + 30, 63, rt_fill_h)
            draw_pill(screen, ACCENT_RED, fill_rect, radius=6)
        screen.blit(font_bold.render("RT", True, ACCENT_RED if (s_rt_dig or s_rt_ana > 30) else TEXT_WHITE), (screen_w - 100, 126))
        screen.blit(font_mono.render(f"{int(rt_pct * 100)}%", True, TEXT_WHITE), (screen_w - 107, 245))

        # ---------------------------------------------------------------------
        # Shoulders (LB & RB)
        # ---------------------------------------------------------------------
        # LB Button
        lb_rect = pygame.Rect(cx - 210, cy - 145, 110, 36)
        draw_pill(screen, ACCENT_CYAN if s_lb else INACTIVE_BTN, lb_rect, radius=10,
                  border_color=(255, 255, 255) if s_lb else SHELL_OUTLINE, border_width=2)
        lb_txt = font_bold.render("LB", True, (0, 0, 0) if s_lb else TEXT_WHITE)
        screen.blit(lb_txt, lb_txt.get_rect(center=lb_rect.center))

        # RB Button
        rb_rect = pygame.Rect(cx + 100, cy - 145, 110, 36)
        draw_pill(screen, ACCENT_PURPLE if s_rb else INACTIVE_BTN, rb_rect, radius=10,
                  border_color=(255, 255, 255) if s_rb else SHELL_OUTLINE, border_width=2)
        rb_txt = font_bold.render("RB", True, (0, 0, 0) if s_rb else TEXT_WHITE)
        screen.blit(rb_txt, rb_txt.get_rect(center=rb_rect.center))

        # ---------------------------------------------------------------------
        # Left Analog Stick (LX, LY, LS click)
        # ---------------------------------------------------------------------
        ls_center = (cx - 150, cy - 30)
        radar_r = 46
        # Outer base
        pygame.draw.circle(screen, (22, 24, 34), ls_center, radar_r)
        pygame.draw.circle(screen, SHELL_OUTLINE, ls_center, radar_r, width=2)
        # Crosshairs
        pygame.draw.line(screen, CARD_BORDER, (ls_center[0] - radar_r, ls_center[1]), (ls_center[0] + radar_r, ls_center[1]), 1)
        pygame.draw.line(screen, CARD_BORDER, (ls_center[0], ls_center[1] - radar_r), (ls_center[0], ls_center[1] + radar_r), 1)
        # Dynamic Thumb Puck Position
        norm_lx = (s_lx - 128) / 128.0
        norm_ly = (s_ly - 128) / 128.0
        puck_x = int(ls_center[0] + norm_lx * 26)
        puck_y = int(ls_center[1] + norm_ly * 26)
        puck_color = ACCENT_CYAN if s_ls else (50, 55, 80)
        puck_border = (255, 255, 255) if s_ls else ACCENT_CYAN
        pygame.draw.circle(screen, puck_color, (puck_x, puck_y), 22)
        pygame.draw.circle(screen, puck_border, (puck_x, puck_y), 22, width=2)
        pygame.draw.circle(screen, (255, 255, 255) if s_ls else TEXT_MUTED, (puck_x, puck_y), 6)
        
        # Stick readout
        lbl_ls = font_sm.render(f"L-STICK: ({s_lx}, {s_ly})", True, ACCENT_CYAN if s_ls else TEXT_MUTED)
        screen.blit(lbl_ls, lbl_ls.get_rect(center=(ls_center[0], ls_center[1] + 62)))

        # ---------------------------------------------------------------------
        # Right Analog Stick (RX, RY, RS click)
        # ---------------------------------------------------------------------
        rs_center = (cx + 80, cy + 85)
        # Outer base
        pygame.draw.circle(screen, (22, 24, 34), rs_center, radar_r)
        pygame.draw.circle(screen, SHELL_OUTLINE, rs_center, radar_r, width=2)
        # Crosshairs
        pygame.draw.line(screen, CARD_BORDER, (rs_center[0] - radar_r, rs_center[1]), (rs_center[0] + radar_r, rs_center[1]), 1)
        pygame.draw.line(screen, CARD_BORDER, (rs_center[0], rs_center[1] - radar_r), (rs_center[0], rs_center[1] + radar_r), 1)
        # Dynamic Thumb Puck Position
        norm_rx = (s_rx - 128) / 128.0
        norm_ry = (s_ry - 128) / 128.0
        r_puck_x = int(rs_center[0] + norm_rx * 26)
        r_puck_y = int(rs_center[1] + norm_ry * 26)
        r_puck_color = ACCENT_CYAN if s_rs else (50, 55, 80)
        r_puck_border = (255, 255, 255) if s_rs else ACCENT_CYAN
        pygame.draw.circle(screen, r_puck_color, (r_puck_x, r_puck_y), 22)
        pygame.draw.circle(screen, r_puck_border, (r_puck_x, r_puck_y), 22, width=2)
        pygame.draw.circle(screen, (255, 255, 255) if s_rs else TEXT_MUTED, (r_puck_x, r_puck_y), 6)

        # Stick readout
        lbl_rs = font_sm.render(f"R-STICK: ({s_rx}, {s_ry})", True, ACCENT_CYAN if s_rs else TEXT_MUTED)
        screen.blit(lbl_rs, lbl_rs.get_rect(center=(rs_center[0], rs_center[1] + 62)))

        # ---------------------------------------------------------------------
        # D-Pad (Cross Layout)
        # ---------------------------------------------------------------------
        dpad_c = (cx - 80, cy + 85)
        d_size = 28
        # Center hub
        pygame.draw.rect(screen, INACTIVE_BTN, (dpad_c[0] - 14, dpad_c[1] - 14, 28, 28))
        
        # UP
        up_rect = pygame.Rect(dpad_c[0] - 14, dpad_c[1] - 44, 28, 28)
        draw_pill(screen, ACCENT_GREEN if s_up else INACTIVE_BTN, up_rect, radius=6,
                  border_color=(255, 255, 255) if s_up else SHELL_OUTLINE)
        txt_u = font_bold.render("▲", True, (0, 0, 0) if s_up else TEXT_MUTED)
        screen.blit(txt_u, txt_u.get_rect(center=up_rect.center))

        # DOWN
        down_rect = pygame.Rect(dpad_c[0] - 14, dpad_c[1] + 16, 28, 28)
        draw_pill(screen, ACCENT_GREEN if s_down else INACTIVE_BTN, down_rect, radius=6,
                  border_color=(255, 255, 255) if s_down else SHELL_OUTLINE)
        txt_d = font_bold.render("▼", True, (0, 0, 0) if s_down else TEXT_MUTED)
        screen.blit(txt_d, txt_d.get_rect(center=down_rect.center))

        # LEFT
        left_rect = pygame.Rect(dpad_c[0] - 44, dpad_c[1] - 14, 28, 28)
        draw_pill(screen, ACCENT_GREEN if s_left else INACTIVE_BTN, left_rect, radius=6,
                  border_color=(255, 255, 255) if s_left else SHELL_OUTLINE)
        txt_l = font_bold.render("◀", True, (0, 0, 0) if s_left else TEXT_MUTED)
        screen.blit(txt_l, txt_l.get_rect(center=left_rect.center))

        # RIGHT
        right_rect = pygame.Rect(dpad_c[0] + 16, dpad_c[1] - 14, 28, 28)
        draw_pill(screen, ACCENT_GREEN if s_right else INACTIVE_BTN, right_rect, radius=6,
                  border_color=(255, 255, 255) if s_right else SHELL_OUTLINE)
        txt_r = font_bold.render("▶", True, (0, 0, 0) if s_right else TEXT_MUTED)
        screen.blit(txt_r, txt_r.get_rect(center=right_rect.center))

        lbl_dp = font_sm.render("D-PAD", True, TEXT_MUTED)
        screen.blit(lbl_dp, lbl_dp.get_rect(center=(dpad_c[0], dpad_c[1] + 62)))

        # ---------------------------------------------------------------------
        # Action Buttons (ABXY Diamond Layout)
        # ---------------------------------------------------------------------
        abxy_c = (cx + 150, cy - 30)
        btn_r = 18
        # Y (Top, Yellow)
        draw_circle_btn(screen, (abxy_c[0], abxy_c[1] - 36), btn_r, s_y, ACCENT_YELLOW, (50, 45, 10), "Y", font_btn, SHELL_OUTLINE)
        # A (Bottom, Green)
        draw_circle_btn(screen, (abxy_c[0], abxy_c[1] + 36), btn_r, s_a, ACCENT_GREEN, (10, 45, 25), "A", font_btn, SHELL_OUTLINE)
        # X (Left, Blue)
        draw_circle_btn(screen, (abxy_c[0] - 36, abxy_c[1]), btn_r, s_x, ACCENT_BLUE, (15, 30, 60), "X", font_btn, SHELL_OUTLINE)
        # B (Right, Red)
        draw_circle_btn(screen, (abxy_c[0] + 36, abxy_c[1]), btn_r, s_b, ACCENT_RED, (60, 15, 25), "B", font_btn, SHELL_OUTLINE)

        # ---------------------------------------------------------------------
        # Center Buttons (Select, Home, Start)
        # ---------------------------------------------------------------------
        # Select / Back
        sel_rect = pygame.Rect(cx - 70, cy - 50, 36, 20)
        draw_pill(screen, (240, 240, 240) if s_select else INACTIVE_BTN, sel_rect, radius=8,
                  border_color=(255, 255, 255) if s_select else SHELL_OUTLINE)
        sel_txt = font_sm.render("BACK", True, (0, 0, 0) if s_select else TEXT_MUTED)
        screen.blit(sel_txt, sel_txt.get_rect(center=(sel_rect.centerx, sel_rect.centery - 18)))

        # Home / Guide Button
        home_pos = (cx, cy - 55)
        pygame.draw.circle(screen, ACCENT_GREEN if s_home else (35, 40, 60), home_pos, 18)
        pygame.draw.circle(screen, (255, 255, 255) if s_home else ACCENT_GREEN, home_pos, 18, width=2)
        # Xbox / EvoFox logo symbol (stylized X inside)
        home_glyph = font_bold.render("X", True, (0, 0, 0) if s_home else (255, 255, 255))
        screen.blit(home_glyph, home_glyph.get_rect(center=home_pos))

        # Start / Menu
        start_rect = pygame.Rect(cx + 34, cy - 50, 36, 20)
        draw_pill(screen, (240, 240, 240) if s_start else INACTIVE_BTN, start_rect, radius=8,
                  border_color=(255, 255, 255) if s_start else SHELL_OUTLINE)
        start_txt = font_sm.render("START", True, (0, 0, 0) if s_start else TEXT_MUTED)
        screen.blit(start_txt, start_txt.get_rect(center=(start_rect.centerx, start_rect.centery - 18)))

        # ---------------------------------------------------------------------
        # Bottom Status & Raw Stream HUD
        # ---------------------------------------------------------------------
        draw_pill(screen, CARD_BG, (20, 585, screen_w - 40, 80), radius=14, border_color=CARD_BORDER)

        # Active buttons list
        active_list = []
        if s_a: active_list.append("A")
        if s_b: active_list.append("B")
        if s_x: active_list.append("X")
        if s_y: active_list.append("Y")
        if s_lb: active_list.append("LB")
        if s_rb: active_list.append("RB")
        if s_lt_dig or s_lt_ana > 30: active_list.append(f"LT({s_lt_ana})")
        if s_rt_dig or s_rt_ana > 30: active_list.append(f"RT({s_rt_ana})")
        if s_ls: active_list.append("LS-CLICK")
        if s_rs: active_list.append("RS-CLICK")
        if s_up: active_list.append("DPAD-UP")
        if s_down: active_list.append("DPAD-DOWN")
        if s_left: active_list.append("DPAD-LEFT")
        if s_right: active_list.append("DPAD-RIGHT")
        if s_select: active_list.append("SELECT")
        if s_start: active_list.append("START")
        if s_home: active_list.append("HOME")

        active_str = "  ".join(f"[{b}]" for b in active_list) if active_list else "[ RELEASED / NEUTRAL ]"
        t_active_lbl = font_sm.render("ACTIVE CONTROLS:", True, TEXT_MUTED)
        t_active_val = font_bold.render(active_str, True, ACCENT_CYAN if active_list else TEXT_MUTED)
        screen.blit(t_active_lbl, (40, 597))
        screen.blit(t_active_val, (170, 595))

        # Raw Hex Frame
        t_raw_lbl = font_sm.render("10-BYTE HID REPORT:", True, TEXT_MUTED)
        t_raw_val = font_mono.render(s_raw, True, ACCENT_YELLOW)
        t_pkt_cnt = font_sm.render(f"Frames Received: {s_pkts}", True, TEXT_MUTED)
        screen.blit(t_raw_lbl, (40, 627))
        screen.blit(t_raw_val, (170, 626))
        screen.blit(t_pkt_cnt, (screen_w - 200, 627))

        pygame.display.flip()

    pygame.quit()
    sys.exit(0)

if __name__ == "__main__":
    main()
